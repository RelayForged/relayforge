"""AEDEService — Coordinates the closed telemetry feedback loop.

Upon attempt completion, WebhookWorker notifies AEDEService with the Telemetry record.
AEDEService executes the Control Plane feedback loop:
1. Telemetry Ingestion: Appends observation to endpoint sliding window.
2. ERI Recomputation: Computes new ERI score using optimal weights over the sliding window.
3. State Machine Transition: Evaluates next_state() using hysteresis thresholds.
4. Policy Synthesis: Derives an immutable DeliveryPolicy and matching ExplainabilityRecord.
5. Storage & Persistence:
   - Persists ExplainabilityRecord to ExplainabilityRepository.
   - Refreshes PolicyCache with new synthesized policy.
   - Tracks current EndpointState for future evaluations.
"""

import asyncio
from datetime import datetime, timedelta, timezone
import json
import logging
import os
from typing import Any, Callable, Dict, List, Optional, Tuple

from relay.control_plane.aede.synthesizer import synthesize_policy
from relay.control_plane.eri.calculator import compute_eri
from relay.control_plane.explainability.record import ExplainabilityRecord
from relay.control_plane.state_machine.transitions import next_state
from relay.infrastructure.auth.signing import hmac_required_from_env
from relay.infrastructure.cache import InMemoryPolicyCache, PolicyCache
from relay.infrastructure.messaging.admin import EVENTS_INTERNAL_POLICY_TOPIC
from relay.infrastructure.messaging.producer import publish
from relay.infrastructure.persistence.explainability_repo import (
    ExplainabilityRepository,
    InMemoryExplainabilityRepository,
)
from relay.kernel.entities.policy_evaluation import PolicyEvaluation
from relay.kernel.policies.delivery_policy import DeliveryPolicy
from relay.kernel.value_objects.endpoint_state import (
    EndpointState,
    StateMachineThresholds,
)
from relay.kernel.value_objects.eri import ERIScore, ERIWeights
from relay.kernel.value_objects.telemetry import Telemetry

logger = logging.getLogger(__name__)

# The producer's cold-start (ensure_topics + AIOKafkaProducer.start) measured
# ~8s combined against a real broker; the process-level entry points
# (relay/api/main.py, relay/data_plane/dispatcher/main.py) call
# producer.warmup() eagerly at startup so steady-state publishes only pay the
# ~10ms send cost. This timeout is a safety net for an unwarmed or
# reconnecting producer, not the expected per-call cost -- keep it well above
# the measured cold-start time rather than tuned to the fast-path latency.
PUBLISH_EVENT_TIMEOUT_SECONDS = 15.0


class AEDEService:
    """Central coordinator for the Adaptive Event Delivery Engine (AEDE) feedback loop."""

    def __init__(
        self,
        *,
        cache: Optional[PolicyCache] = None,
        explainability_repo: Optional[ExplainabilityRepository] = None,
        weights: Optional[ERIWeights] = None,
        thresholds: Optional[StateMachineThresholds] = None,
        window_size: int = 100,
        publish_events: bool = True,
        publisher: Optional[Callable[[str, str, bytes], Any]] = None,
        hmac_required: Optional[bool] = None,
    ) -> None:
        self._cache = cache or InMemoryPolicyCache()
        self._explainability_repo = (
            explainability_repo or InMemoryExplainabilityRepository()
        )
        self._weights = weights or ERIWeights.optimal()
        quarantine_thresh = float(os.environ.get("AEDE_QUARANTINE_THRESHOLD", "30.0"))
        # Probation length for RECOVERING (ADR 0023); 0 keeps the benchmark's model.
        recovery_min_samples = int(os.environ.get("AEDE_RECOVERY_MIN_SAMPLES", "0"))
        # Wider hysteresis gaps than the original 80/90, 60/70: empirically, ERI during
        # sustained borderline (not catastrophic) degradation swings by ~40 points within the
        # sliding window (observed on a long-duration moderate-degradation scenario), which
        # straddled the old narrow gaps and caused persistent state flapping. Lowering the
        # trigger thresholds slightly and widening the recovery thresholds keeps detection speed
        # for genuine outages intact while making recovery stickier -- verified to reduce
        # flapping without regressing outage detection (see benchmark/genetic_optimizer.py and
        # the held-out evaluation in docs/report/04-evaluation-methodology.md §4.14).
        self._thresholds = thresholds or StateMachineThresholds(
            degradation_threshold=75.0,
            degradation_recovery_threshold=95.0,
            unstable_threshold=55.0,
            unstable_recovery_threshold=85.0,
            quarantine_threshold=quarantine_thresh,
            recovery_threshold=80.0,
            recovery_min_samples=recovery_min_samples,
        )
        self._window_size = window_size
        # Fixed for the life of the service and passed to every synthesis
        # as an explicit argument (the synthesizer never reads the env).
        self._hmac_required = (
            hmac_required if hmac_required is not None else hmac_required_from_env()
        )
        self._publish_events = publish_events
        self._publisher = publisher

        # In-memory operational state tracking
        self._endpoint_states: Dict[str, EndpointState] = {}
        self._telemetry_windows: Dict[str, List[Telemetry]] = {}
        # When each endpoint was last restored by an operator (ADR 0020).
        self._restored_at: Dict[str, datetime] = {}

    def get_endpoint_state(self, endpoint_id: str) -> EndpointState:
        """Return the current EndpointState for the given endpoint (defaults to AVAILABLE)."""
        return self._endpoint_states.get(endpoint_id, EndpointState.AVAILABLE)

    def set_endpoint_state(self, endpoint_id: str, state: EndpointState) -> None:
        """Explicitly set the EndpointState for an endpoint."""
        self._endpoint_states[endpoint_id] = state

    def restore_endpoint(self, endpoint_id: str) -> EndpointState:
        """
        State-only restoration from QUARANTINED to RECOVERING.

        This is the transition the benchmark runner models (it flips the
        state and keeps the sliding window), and
        benchmark/validate_against_real_aede.py checks parity against it,
        so it is deliberately left unchanged. The live operator path is
        restore_and_publish(); see ADR 0020 for how the two differ.
        """
        current = self.get_endpoint_state(endpoint_id)
        if current == EndpointState.QUARANTINED:
            self.set_endpoint_state(endpoint_id, EndpointState.RECOVERING)
            logger.info("Admin restored endpoint %s from QUARANTINED to RECOVERING", endpoint_id)
            return EndpointState.RECOVERING
        return current

    async def restore_and_publish(
        self, endpoint_id: str, restored_at: datetime
    ) -> EndpointState:
        """
        Operator restore on the live platform: QUARANTINED to RECOVERING,
        a no-op in any other state. Applies ADR 0020, then announces the
        transition on the policy topic so observers (the dashboard) see
        RECOVERING immediately rather than at the next delivery attempt.
        """
        applied = self._apply_restore(endpoint_id, restored_at)
        if applied is not None:
            eri, policy, record = applied
            await self._publish_evaluation(
                endpoint_id=endpoint_id,
                eri=eri,
                state_before=EndpointState.QUARANTINED,
                state_after=EndpointState.RECOVERING,
                policy=policy,
                explainability_record=record,
                computed_at=restored_at,
                window_size=0,
            )
        return self.get_endpoint_state(endpoint_id)

    def _apply_restore(
        self, endpoint_id: str, restored_at: datetime
    ) -> Optional[Tuple[ERIScore, DeliveryPolicy, ExplainabilityRecord]]:
        """
        QUARANTINED -> RECOVERING, per ADR 0020.

        Flipping the state alone does not restore delivery. The cached
        QUARANTINE policy would keep being served, and the sliding window
        still holds the outage, so the first observation after restore
        would score below the quarantine threshold and re-quarantine the
        endpoint. Recovery is therefore judged only on evidence gathered
        after the restore: the window is reset and the RECOVERING policy
        replaces the cached one.
        """
        if self.get_endpoint_state(endpoint_id) != EndpointState.QUARANTINED:
            return None

        self._telemetry_windows[endpoint_id] = []
        self._restored_at[endpoint_id] = restored_at
        self.set_endpoint_state(endpoint_id, EndpointState.RECOVERING)

        eri = ERIScore.perfect(window_size=0, computed_at=restored_at)
        policy, record = synthesize_policy(
            endpoint_id=endpoint_id,
            state_before=EndpointState.QUARANTINED,
            state_after=EndpointState.RECOVERING,
            eri=eri,
            thresholds=self._thresholds,
            hmac_required=self._hmac_required,
            computed_at=restored_at,
        )
        record = record.model_copy(
            update={
                "reason": (
                    "Admin restore: endpoint moved from QUARANTINED to RECOVERING. "
                    "ERI window reset; recovery is judged on attempts started after the restore."
                ),
            }
        )
        self._explainability_repo.save(record)
        try:
            self._cache.set_policy(
                endpoint_id=endpoint_id,
                policy=policy,
                ttl_seconds=300,
            )
        except Exception:
            logger.exception("Failed to update policy cache for endpoint %s", endpoint_id)

        logger.info("Admin restored endpoint %s from QUARANTINED to RECOVERING", endpoint_id)
        return eri, policy, record

    def _predates_restore(self, telemetry: Telemetry) -> bool:
        """True for an attempt that began before the last restore of its endpoint."""
        restored_at = self._restored_at.get(telemetry.endpoint_id)
        if restored_at is None:
            return False
        started_at = telemetry.observed_at - timedelta(milliseconds=telemetry.latency_ms)
        return started_at < restored_at

    def get_sliding_window_eri(self, endpoint_id: str, computed_at: Optional[datetime] = None) -> ERIScore:
        """Return the current ERI score over the active sliding window."""
        window = self._telemetry_windows.get(endpoint_id, [])
        return compute_eri(
            attempts=window,
            queue_depth=0,
            weights=self._weights,
            baseline_latency_ms=100.0,
            max_latency_ms=5000.0,
            max_consecutive_failures=10,
            max_queue_depth=100,
            computed_at=computed_at or datetime.now(timezone.utc),
        )

    def get_current_policy(self, endpoint_id: str, computed_at: Optional[datetime] = None) -> DeliveryPolicy:
        """Return cached policy if present, or synthesize a policy for the current state."""
        cached = self._cache.get_policy(endpoint_id)
        if cached is not None:
            return cached
        current_state = self.get_endpoint_state(endpoint_id)
        eri = self.get_sliding_window_eri(endpoint_id, computed_at=computed_at)
        policy, _ = synthesize_policy(
            endpoint_id=endpoint_id,
            state_before=current_state,
            state_after=current_state,
            eri=eri,
            thresholds=self._thresholds,
            hmac_required=self._hmac_required,
            computed_at=computed_at or datetime.now(timezone.utc),
        )
        return policy

    async def record_telemetry(
        self,
        telemetry: Telemetry,
        *,
        queue_depth: int = 0,
    ) -> Tuple[ERIScore, EndpointState, DeliveryPolicy, ExplainabilityRecord]:
        """Closed feedback loop triggered on every attempt completion:

        1. Ingest telemetry into sliding window.
        2. Compute updated ERI.
        3. Evaluate state transition with hysteresis.
        4. Synthesize new DeliveryPolicy and ExplainabilityRecord.
        5. Persist explainability record and update policy cache.
        """
        endpoint_id = telemetry.endpoint_id
        computed_at = telemetry.observed_at

        if self._predates_restore(telemetry):
            # An attempt already in flight when the operator restored the
            # endpoint describes the outage, not the recovery. Counting it
            # would re-quarantine on stale evidence (ADR 0020). The
            # observation itself is still persisted by the Worker.
            logger.info(
                "Excluding pre-restore telemetry from ERI window attempt_id=%s endpoint_id=%s",
                telemetry.attempt_id,
                endpoint_id,
            )
            current_state = self.get_endpoint_state(endpoint_id)
            eri = self.get_sliding_window_eri(endpoint_id, computed_at=computed_at)
            policy, explainability_record = synthesize_policy(
                endpoint_id=endpoint_id,
                state_before=current_state,
                state_after=current_state,
                eri=eri,
                thresholds=self._thresholds,
            hmac_required=self._hmac_required,
                computed_at=computed_at,
            )
            return eri, current_state, policy, explainability_record

        # 1. Update sliding window
        window = self._telemetry_windows.setdefault(endpoint_id, [])
        window.append(telemetry)
        if len(window) > self._window_size:
            self._telemetry_windows[endpoint_id] = window[-self._window_size:]
            window = self._telemetry_windows[endpoint_id]

        # 2. Compute ERI
        eri = compute_eri(
            attempts=window,
            queue_depth=queue_depth,
            weights=self._weights,
            baseline_latency_ms=100.0,
            max_latency_ms=5000.0,
            max_consecutive_failures=10,
            max_queue_depth=100,
            computed_at=computed_at,
        )

        # 3. State machine transition
        current_state = self.get_endpoint_state(endpoint_id)
        new_state = next_state(
            current=current_state,
            eri=eri,
            thresholds=self._thresholds,
        )
        self.set_endpoint_state(endpoint_id, new_state)

        # 4. Policy synthesis
        policy, explainability_record = synthesize_policy(
            endpoint_id=endpoint_id,
            state_before=current_state,
            state_after=new_state,
            eri=eri,
            thresholds=self._thresholds,
            hmac_required=self._hmac_required,
            computed_at=computed_at,
        )

        # 5. Persist explainability record
        self._explainability_repo.save(explainability_record)

        # 6. Refresh policy cache (Tier 2 ready)
        try:
            self._cache.set_policy(
                endpoint_id=endpoint_id,
                policy=policy,
                ttl_seconds=300,
            )
        except Exception:
            logger.exception("Failed to update policy cache for endpoint %s", endpoint_id)

        # 7. Construct and publish PolicyEvaluation event to Kafka
        await self._publish_evaluation(
            endpoint_id=endpoint_id,
            eri=eri,
            state_before=current_state,
            state_after=new_state,
            policy=policy,
            explainability_record=explainability_record,
            computed_at=computed_at,
            window_size=len(window),
        )

        return eri, new_state, policy, explainability_record

    async def _publish_evaluation(
        self,
        *,
        endpoint_id: str,
        eri: ERIScore,
        state_before: EndpointState,
        state_after: EndpointState,
        policy: DeliveryPolicy,
        explainability_record: ExplainabilityRecord,
        computed_at: datetime,
        window_size: int,
    ) -> None:
        if not self._publish_events:
            return
        try:
            evaluation = PolicyEvaluation(
                endpoint_id=endpoint_id,
                policy_id=policy.policy_id,
                explainability_record_id=explainability_record.record_id,
                eri_score=eri.score,
                state_before=state_before.value,
                state_after=state_after.value,
                state_changed=(state_before != state_after),
                strategy_used="STRATEGY_C",
                is_fallback=False,
                fallback_tier=1,
                evaluated_at=computed_at or datetime.now(timezone.utc),
                window_size=window_size,
            )
            bundle = {
                "endpoint_id": endpoint_id,
                "state": state_after.value,
                "eri_score": eri.model_dump(mode="json"),
                "policy_evaluation": evaluation.model_dump(mode="json"),
                "delivery_policy": policy.model_dump(mode="json"),
                "explainability_record": explainability_record.model_dump(mode="json"),
            }
            payload = json.dumps(bundle, default=str).encode("utf-8")
            if self._publisher is not None:
                res = self._publisher(EVENTS_INTERNAL_POLICY_TOPIC, endpoint_id, payload)
                if asyncio.iscoroutine(res):
                    await asyncio.wait_for(res, timeout=PUBLISH_EVENT_TIMEOUT_SECONDS)
            else:
                await asyncio.wait_for(
                    publish(EVENTS_INTERNAL_POLICY_TOPIC, key=endpoint_id, value=payload),
                    timeout=PUBLISH_EVENT_TIMEOUT_SECONDS,
                )
        except Exception as exc:
            # repr(), not str() -- TimeoutError's str() is "", which previously
            # logged as a blank, undiagnosable warning (e.g. "...endpoint x: ").
            logger.warning(
                "Failed to publish policy evaluation event for endpoint %s: %s: %r",
                endpoint_id,
                type(exc).__name__,
                exc,
            )
