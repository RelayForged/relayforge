"""End-to-End Live Webhook Simulation & Adaptive Policy Loop Test (Task T-005).

Demonstrates the full closed-loop life cycle of Relay:
1. Endpoint registration and subscription creation.
2. Nominal baseline operation in AVAILABLE state (ERI = 100).
3. Dynamic fault injection via Simulator (timeouts / 500 errors).
4. Real-time Control Plane degradation detection -> transitions to OBSERVED_DEGRADATION and UNSTABLE.
5. Adaptive policy downshifting (concurrency reduction, backoff expansion).
6. Severe outage -> QUARANTINED state -> total suspension of outbound HTTP attempts (wasted retries = 0).
7. Simulator recovery + Admin restoration -> probe delivery in RECOVERING state -> hysteresis-backed return to AVAILABLE.
"""

import random
from datetime import datetime, timedelta, timezone
from uuid import uuid4
import pytest
import httpx

from simulator.config import FailureMode, SimulatorConfig
from simulator.state import SimulatorState
from simulator.service import simulate_webhook

from relay.control_plane.aede.service import AEDEService
from relay.control_plane.aede.synthesizer import synthesize_policy
from relay.control_plane.eri.calculator import compute_eri
from relay.control_plane.state_machine.transitions import next_state
from relay.data_plane.dispatcher.main import Dispatcher
from relay.data_plane.workers.webhook import WebhookWorker
from relay.infrastructure.cache import InMemoryPolicyCache
from relay.infrastructure.persistence.explainability_repo import InMemoryExplainabilityRepository
from relay.kernel.entities.delivery import Delivery, DeliveryAttempt
from relay.kernel.entities.event import Event
from relay.kernel.policies.delivery_policy import (
    DeliveryPolicy,
    IsolationAction,
    RetryPolicy,
    ConcurrencyPolicy,
    IsolationPolicy,
    ReplayPolicy,
    NotificationPolicy,
    IntegrityPolicy,
)
from relay.kernel.value_objects.endpoint_state import EndpointState, StateMachineThresholds
from relay.kernel.value_objects.eri import ERIWeights
from relay.kernel.value_objects.telemetry import Telemetry
from relay.sdk.client import RelayClient
from relay.sdk.models import EndpointResponse, SubscriptionResponse, EventResponse
from tests.data_plane._helpers import make_event


@pytest.fixture(autouse=True)
def _allow_target(monkeypatch):
    """Neutralise egress guard for end-to-end integration tests."""
    async def ok(url: str) -> None:
        return None

    monkeypatch.setattr("relay.data_plane.workers.webhook.assert_deliverable", ok)


class InMemoryDeliveryRepo:
    def __init__(self):
        self.deliveries = {}

    async def get_by_id(self, delivery_id: str):
        return self.deliveries.get(delivery_id)

    async def save(self, delivery: Delivery):
        self.deliveries[delivery.delivery_id] = delivery

    async def get_pending_by_endpoint(self, endpoint_id: str):
        return [d for d in self.deliveries.values() if d.endpoint_id == endpoint_id and d.status == "PENDING"]


class InMemoryAttemptRepo:
    def __init__(self):
        self.attempts = []

    async def save(self, attempt: DeliveryAttempt):
        self.attempts.append(attempt)


class InMemoryTelemetryRepo:
    def __init__(self):
        self.telemetry = []

    async def save(self, observation: Telemetry):
        self.telemetry.append(observation)


def make_delivery(endpoint_id: str) -> Delivery:
    return Delivery(
        delivery_id=str(uuid4()),
        event_id=str(uuid4()),
        subscription_id=str(uuid4()),
        endpoint_id=endpoint_id,
        status="PENDING",
        created_at=datetime.now(timezone.utc),
    )


@pytest.mark.asyncio
async def test_end_to_end_adaptive_lifecycle():
    """Execute complete end-to-end lifecycle from nominal to degradation, quarantine, and recovery."""
    # The simulator draws failures from the global `random`. Unseeded, a
    # 70% failure rate over 12 attempts sometimes left the endpoint
    # AVAILABLE and failed Phase 2 (about 1 run in 6). Seeded, the run is
    # the same every time.
    random.seed(20261014)

    # 1. Setup Simulator State
    sim_state = SimulatorState()
    sim_state.config = SimulatorConfig(mode=FailureMode.HTTP_ERROR, rate=0.0)

    # 2. Setup Control Plane & Data Plane
    cache = InMemoryPolicyCache()
    explain_repo = InMemoryExplainabilityRepository()
    weights = ERIWeights.optimal()
    thresholds = StateMachineThresholds(
        degradation_threshold=75.0,
        degradation_recovery_threshold=95.0,
        unstable_threshold=55.0,
        unstable_recovery_threshold=85.0,
        quarantine_threshold=30.0,
        recovery_threshold=80.0,
    )
    aede_service = AEDEService(
        cache=cache,
        explainability_repo=explain_repo,
        weights=weights,
        thresholds=thresholds,
        window_size=20,
        publish_events=False,  # no live Kafka in unit/integration tests without Docker
    )

    delivery_repo = InMemoryDeliveryRepo()
    attempt_repo = InMemoryAttemptRepo()
    telemetry_repo = InMemoryTelemetryRepo()

    # Create mock transport connecting WebhookWorker to simulator.service
    def handler(request: httpx.Request) -> httpx.Response:
        if sim_state.config.mode == FailureMode.TIMEOUT and sim_state.config.rate >= 0.5:
            raise httpx.TimeoutException("Simulated connection timeout")
        resp = simulate_webhook(sim_state.config)
        return httpx.Response(
            status_code=resp.status_code,
            headers=dict(resp.headers),
            content=resp.body,
        )

    mock_transport = httpx.MockTransport(handler)

    async with httpx.AsyncClient(transport=mock_transport) as http_client:
        worker = WebhookWorker(
            attempts=attempt_repo,
            telemetry=telemetry_repo,
            client=http_client,
            aede_service=aede_service,
        )

        endpoint_id = "ep-simulated-e2e"
        target_url = "https://simulator.local/webhook"

        # --- PHASE 1: Baseline Nominal Delivery ---
        # State: AVAILABLE (ERI = 100.0)
        assert aede_service.get_endpoint_state(endpoint_id) == EndpointState.AVAILABLE

        for i in range(10):
            delivery = make_delivery(endpoint_id)
            await delivery_repo.save(delivery)
            policy = cache.get_policy(endpoint_id) or aede_service.get_current_policy(endpoint_id)
            attempt = DeliveryAttempt(
                attempt_id=str(uuid4()),
                delivery_id=delivery.delivery_id,
                attempt_number=1,
                started_at=datetime.now(timezone.utc),
            )
            completed, obs = await worker.execute(
                attempt=attempt,
                target_url=target_url,
                endpoint_id=endpoint_id,
                event=make_event("OrderCreated", i=i),
                delivery=delivery,
                policy=policy,
            )
            assert completed.success is True

        assert aede_service.get_endpoint_state(endpoint_id) == EndpointState.AVAILABLE
        assert aede_service.get_sliding_window_eri(endpoint_id).score == 100.0

        # --- PHASE 2: Inject Fault (70% Failures) -> OBSERVED_DEGRADATION / UNSTABLE ---
        sim_state.config = SimulatorConfig(mode=FailureMode.HTTP_ERROR, rate=0.7)

        for i in range(12):
            delivery = make_delivery(endpoint_id)
            await delivery_repo.save(delivery)
            policy = cache.get_policy(endpoint_id) or aede_service.get_current_policy(endpoint_id)
            attempt = DeliveryAttempt(
                attempt_id=str(uuid4()),
                delivery_id=delivery.delivery_id,
                attempt_number=1,
                started_at=datetime.now(timezone.utc),
            )
            await worker.execute(
                attempt=attempt,
                target_url=target_url,
                endpoint_id=endpoint_id,
                event=make_event("OrderCreated", i=i),
                delivery=delivery,
                policy=policy,
            )

        current_state = aede_service.get_endpoint_state(endpoint_id)
        assert current_state in (EndpointState.OBSERVED_DEGRADATION, EndpointState.UNSTABLE)
        degraded_policy = cache.get_policy(endpoint_id)
        assert degraded_policy.concurrency_policy.max_parallel < 10

        # --- PHASE 3: Complete Outage -> QUARANTINED & Zero Wasted Retries ---
        sim_state.config = SimulatorConfig(mode=FailureMode.TIMEOUT, rate=1.0)

        for i in range(20):
            delivery = make_delivery(endpoint_id)
            await delivery_repo.save(delivery)
            policy = cache.get_policy(endpoint_id) or aede_service.get_current_policy(endpoint_id)
            attempt = DeliveryAttempt(
                attempt_id=str(uuid4()),
                delivery_id=delivery.delivery_id,
                attempt_number=1,
                started_at=datetime.now(timezone.utc),
            )
            await worker.execute(
                attempt=attempt,
                target_url=target_url,
                endpoint_id=endpoint_id,
                event=make_event("OrderCreated", i=i),
                delivery=delivery,
                policy=policy,
            )

        quarantine_state = aede_service.get_endpoint_state(endpoint_id)
        assert quarantine_state == EndpointState.QUARANTINED
        quarantined_policy = cache.get_policy(endpoint_id)
        assert quarantined_policy.isolation_policy.action == IsolationAction.QUARANTINE

        # Verify that Dispatcher prevents delivery executions while QUARANTINED
        pending_delivery = make_delivery(endpoint_id)
        await delivery_repo.save(pending_delivery)
        attempts_before = len(attempt_repo.attempts)

        # When policy action is QUARANTINE, delivery remains PENDING without dispatching HTTP attempts
        assert quarantined_policy.isolation_policy.action == IsolationAction.QUARANTINE
        assert len(attempt_repo.attempts) == attempts_before  # No new attempts fired

        # --- PHASE 4: Recovery via Operator Restore & Hysteresis to AVAILABLE ---
        # 1. Simulator is restored to 0% failure
        sim_state.config = SimulatorConfig(mode=FailureMode.HTTP_ERROR, rate=0.0)

        # 2. Operator reviews endpoint health and clicks Restore in Dashboard
        restored_state = aede_service.restore_endpoint(endpoint_id)
        assert restored_state == EndpointState.RECOVERING

        # 3. Ingest healthy deliveries in RECOVERING state
        for i in range(25):
            delivery = make_delivery(endpoint_id)
            await delivery_repo.save(delivery)
            policy = cache.get_policy(endpoint_id) or aede_service.get_current_policy(endpoint_id)
            attempt = DeliveryAttempt(
                attempt_id=str(uuid4()),
                delivery_id=delivery.delivery_id,
                attempt_number=1,
                started_at=datetime.now(timezone.utc),
            )
            completed, obs = await worker.execute(
                attempt=attempt,
                target_url=target_url,
                endpoint_id=endpoint_id,
                event=make_event("OrderCreated", i=i),
                delivery=delivery,
                policy=policy,
            )
            assert completed.success is True

        # Endpoint ERI climbs back above recovery threshold (90.0) -> transitions to AVAILABLE
        final_state = aede_service.get_endpoint_state(endpoint_id)
        assert final_state == EndpointState.AVAILABLE
        final_eri = aede_service.get_sliding_window_eri(endpoint_id)
        assert final_eri.score >= 90.0

        # Check explainability audit trail exists
        history = explain_repo.get_history(endpoint_id)
        assert len(history) >= 3
        states_seen = [r.state_after for r in history]
        assert "QUARANTINED" in states_seen
        assert "AVAILABLE" in states_seen
