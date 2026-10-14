"""BenchmarkRunner — orchestrates automated Strategy A vs B vs C experiments.

Drives the Control Plane pipeline (ERI + State Machine + Policy Synthesizer)
with synthetic telemetry derived from Scenario definitions. Runs fully in-process
for deterministic, reproducible validation.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import random
from typing import Dict, List, Optional
from uuid import uuid4

from benchmark.metrics import (
    AttemptRecord,
    BenchmarkResult,
    BenchmarkRunSummary,
    benchmark_store,
    compute_avg_retry_count,
    compute_recovery_time_events,
)
from benchmark.scenarios import Scenario, ScenarioPhase
from relay.control_plane.aede.strategies.adaptive import StrategyB
from relay.control_plane.aede.strategies.static import StrategyA
from relay.control_plane.aede.synthesizer import synthesize_policy
from relay.control_plane.eri.calculator import compute_eri
from relay.control_plane.state_machine.quarantine import hold_expired, quarantine_hold_seconds
from relay.control_plane.state_machine.transitions import next_state
from relay.kernel.value_objects.endpoint_state import EndpointState, StateMachineThresholds
from relay.kernel.value_objects.eri import ERIWeights
from relay.kernel.value_objects.telemetry import Telemetry


class SyntheticTelemetryGenerator:
    """Generates deterministic, seeded synthetic telemetry from scenario phase definitions."""

    def __init__(self, seed: int = 42) -> None:
        self._rng = random.Random(seed)

    def generate(
        self,
        phase: ScenarioPhase,
        count: int,
        endpoint_id: str,
        start_time: datetime,
        bursty: bool = False,
    ) -> List[Telemetry]:
        """Generate a block of synthetic Telemetry records matching the phase specification."""
        items: List[Telemetry] = []
        obs_time = start_time
        for i in range(count):
            if bursty:
                interval_ms = max(1.0, self._rng.expovariate(1.0 / 50.0))
                obs_time = obs_time + timedelta(milliseconds=interval_ms)
            else:
                obs_time = start_time + timedelta(milliseconds=i * 50)
            roll = self._rng.random()

            if roll < phase.success_rate:
                succeeded = True
                timed_out = False
                http_status = 200
                latency = max(10.0, phase.latency_ms + self._rng.gauss(0, 15))
            else:
                succeeded = False
                fail_roll = self._rng.random()
                if fail_roll < phase.timed_out_rate:
                    timed_out = True
                    http_status = None
                    latency = max(50.0, phase.latency_ms)
                elif fail_roll < phase.timed_out_rate + phase.http_5xx_rate:
                    timed_out = False
                    http_status = 500
                    latency = max(20.0, phase.latency_ms)
                else:
                    timed_out = False
                    http_status = 400
                    latency = max(20.0, phase.latency_ms)

            items.append(
                Telemetry(
                    attempt_id=str(uuid4()),
                    endpoint_id=endpoint_id,
                    succeeded=succeeded,
                    http_status=http_status,
                    latency_ms=round(latency, 2),
                    timed_out=timed_out,
                    observed_at=obs_time,
                )
            )
        return items


@dataclass(frozen=True)
class DeliveryStrategyConfig:
    """Configures the delivery strategy and feature flags for an experiment run."""
    strategy: str  # "STRATEGY_A", "STRATEGY_B", "STRATEGY_C", "STRATEGY_HCB"
    feature_flags: Optional[Dict[str, bool]] = None


# Hystrix-style circuit breaker defaults (verified against the official Netflix Hystrix
# configuration wiki, github.com/Netflix/Hystrix/wiki/configuration):
#   circuitBreaker.requestVolumeThreshold = 20
#   circuitBreaker.errorThresholdPercentage = 50
#   circuitBreaker.sleepWindowInMilliseconds = 5000
# This benchmark harness advances simulated wall-clock time by exactly 1 second per event
# (see the `current_time += timedelta(seconds=1)` line in BenchmarkRunner.run), so the
# 5000ms sleep window is translated directly and losslessly into 5 simulated events.
HCB_ROLLING_WINDOW = 20
HCB_ERROR_THRESHOLD = 0.5
HCB_SLEEP_WINDOW_EVENTS = 5


class BenchmarkRunner:
    """Executes a benchmark experiment across a scenario, collecting comparative metrics."""

    def __init__(
        self,
        strategy_config: DeliveryStrategyConfig,
        eri_weights: Optional[ERIWeights] = None,
        sm_thresholds: Optional[StateMachineThresholds] = None,
        window_size: int = 100,
        random_seed: int = 42,
        bursty: bool = False,
        quarantine_hold_seconds: int = 0,
        quarantine_hold_max_seconds: int = 3600,
    ) -> None:
        self.strategy_config = strategy_config
        # Timed quarantine (ADR 0027) for Strategy C. 0, the default, keeps the
        # published model: only a scenario's manual_restore phase ends a
        # quarantine. One simulated event is one second.
        self.quarantine_hold_seconds = quarantine_hold_seconds
        self.quarantine_hold_max_seconds = quarantine_hold_max_seconds
        self.eri_weights = eri_weights or ERIWeights.optimal()
        if sm_thresholds is not None:
            self.sm_thresholds = sm_thresholds
        elif self.strategy_config.strategy == "STRATEGY_C_DOUBLE_PRIME":
            # Ablation C'' (no hysteresis): near-zero epsilon hysteresis gap (0.001)
            self.sm_thresholds = StateMachineThresholds(
                degradation_threshold=80.0,
                degradation_recovery_threshold=80.001,
                unstable_threshold=60.0,
                unstable_recovery_threshold=60.001,
                quarantine_threshold=30.0,
                recovery_threshold=30.001,
            )
        else:
            # Matches the production default in relay/control_plane/aede/service.py: wider
            # hysteresis gaps than the original 80/90, 60/75, verified to cut flapping on
            # long-duration moderate-degradation scenarios without regressing outage detection.
            self.sm_thresholds = StateMachineThresholds(
                degradation_threshold=75.0,
                degradation_recovery_threshold=95.0,
                unstable_threshold=55.0,
                unstable_recovery_threshold=85.0,
                quarantine_threshold=30.0,
                recovery_threshold=80.0,
            )
        self.window_size = window_size
        self.random_seed = random_seed
        self.bursty = bursty
        self.strategy_a = StrategyA()
        self.strategy_b = StrategyB()

    def run(
        self,
        scenario: Scenario,
        endpoint_id: str = "benchmark-ep",
        start_time: Optional[datetime] = None,
    ) -> BenchmarkResult:
        """Run an entire scenario through the configured strategy and produce a BenchmarkResult."""
        # Re-seed per (base seed, scenario_id) rather than reusing one generator across every
        # scenario a runner instance happens to process. A single stateful RNG shared across
        # scenarios makes each scenario's telemetry depend on which OTHER scenarios ran before
        # it and in what order within that particular script -- so the same nominal seed and
        # scenario produced different telemetry in run_experiments.py (which iterates all 14
        # scenarios) versus ablation.py (which iterates only the 8 held-out scenarios). Deriving
        # the seed from (random_seed, scenario_id) makes a given scenario's telemetry depend only
        # on the base seed and that scenario, never on run order or which sibling scenarios are
        # present in the same batch.
        self.generator = SyntheticTelemetryGenerator(seed=self.random_seed * 10_000 + scenario.scenario_id)
        current_time = start_time or datetime.now(timezone.utc)
        result = BenchmarkResult(
            scenario_id=scenario.scenario_id,
            scenario_name=scenario.name,
            strategy=self.strategy_config.strategy,
            started_at=current_time,
        )

        current_state = EndpointState.AVAILABLE
        is_hcb = self.strategy_config.strategy == "STRATEGY_HCB"
        result.state_transitions = ["CB_CLOSED"] if is_hcb else [current_state.value]

        sliding_window: List[Telemetry] = []
        failure_streak = 0
        quarantined_at: Optional[datetime] = None
        relapses = 0
        is_c = self.strategy_config.strategy.startswith("STRATEGY_C")

        # Independent Hystrix-style circuit breaker bookkeeping (STRATEGY_HCB only; unused,
        # harmless no-ops for every other strategy). Deliberately does not touch AEDE's ERI or
        # state machine at all -- this models what an off-the-shelf circuit breaker library
        # would do with no knowledge of AEDE's design.
        cb_state = "CB_CLOSED"
        cb_window: List[bool] = []
        cb_open_remaining = 0

        for phase in scenario.phases:
            # Simulate manual admin restoration if the endpoint was quarantined
            # and this phase represents post-fault recovery (e.g. operator verified health and clicked Restore)
            if self.strategy_config.strategy.startswith("STRATEGY_C") and phase.manual_restore and current_state == EndpointState.QUARANTINED:
                current_state = EndpointState.RECOVERING
                result.state_transitions.append(current_state.value)

            telemetry_batch = self.generator.generate(
                phase=phase,
                count=phase.duration_events,
                endpoint_id=endpoint_id,
                start_time=current_time,
                bursty=self.bursty,
            )

            for item in telemetry_batch:
                current_time += timedelta(seconds=1)

                # Timed quarantine (ADR 0027): an expired hold is probed.
                if is_c and current_state == EndpointState.QUARANTINED and hold_expired(
                    quarantined_at,
                    current_time,
                    quarantine_hold_seconds(
                        self.quarantine_hold_seconds, self.quarantine_hold_max_seconds, relapses
                    ),
                ):
                    current_state = EndpointState.RECOVERING
                    result.state_transitions.append(current_state.value)
                sliding_window.append(item)
                if len(sliding_window) > self.window_size:
                    sliding_window = sliding_window[-self.window_size:]

                # 1. Compute ERI
                eri = compute_eri(
                    attempts=sliding_window,
                    queue_depth=0,
                    weights=self.eri_weights,
                    baseline_latency_ms=100.0,
                    max_latency_ms=5000.0,
                    max_consecutive_failures=10,
                    max_queue_depth=100,
                    computed_at=current_time,
                )

                # 2. Evaluate State Transition
                if self.strategy_config.strategy.startswith("STRATEGY_C"):
                    new_state = next_state(
                        current=current_state,
                        eri=eri,
                        thresholds=self.sm_thresholds,
                    )
                else:
                    # Strategy A, B, O do not have a standard state machine progression
                    new_state = EndpointState.AVAILABLE

                if new_state != current_state:
                    if new_state == EndpointState.QUARANTINED:
                        quarantined_at = current_time
                        if current_state == EndpointState.RECOVERING:
                            relapses += 1
                    elif new_state == EndpointState.AVAILABLE:
                        quarantined_at = None
                        relapses = 0
                    current_state = new_state
                    result.state_transitions.append(current_state.value)

                # 3. Model Retry Behaviour per Strategy
                if self.strategy_config.strategy == "STRATEGY_A":
                    # Fixed static retry baseline (max_retries=10 on failure)
                    policy, _ = self.strategy_a.evaluate(
                        endpoint_id=endpoint_id,
                        endpoint_state=EndpointState.AVAILABLE,
                        eri=eri,
                        computed_at=current_time,
                    )
                    retries = 0 if item.succeeded else policy.retry_policy.max_retries
                    delay_sec = policy.retry_policy.initial_delay_seconds if not item.succeeded else 0.0
                    concurrency = policy.concurrency_policy.max_parallel
                elif self.strategy_config.strategy == "STRATEGY_B":
                    # Adaptive retry baseline without state model
                    policy, _ = self.strategy_b.evaluate(
                        endpoint_id=endpoint_id,
                        endpoint_state=EndpointState.AVAILABLE,
                        eri=eri,
                        computed_at=current_time,
                    )
                    retries = 0 if item.succeeded else policy.retry_policy.max_retries
                    delay_sec = policy.retry_policy.initial_delay_seconds if not item.succeeded else 0.0
                    concurrency = policy.concurrency_policy.max_parallel
                elif self.strategy_config.strategy == "STRATEGY_O":
                    # Strategy O: Oracle upper bound with ground truth phase knowledge
                    if phase.success_rate == 0.0 or item.succeeded:
                        retries = 0
                    else:
                        retries = 1
                    delay_sec = 0.0
                    concurrency = 10
                elif self.strategy_config.strategy == "STRATEGY_HCB":
                    # Hystrix-style circuit breaker baseline: an off-the-shelf pattern, not AEDE.
                    # CLOSED: normal delivery, retry policy mirrors Strategy A's static exponential
                    # backoff (isolates "does the breaker mechanism help" from "does a smarter
                    # backoff shape help", the latter already covered by Strategy B).
                    a_policy, _ = self.strategy_a.evaluate(
                        endpoint_id=endpoint_id,
                        endpoint_state=EndpointState.AVAILABLE,
                        eri=eri,
                        computed_at=current_time,
                    )
                    concurrency = a_policy.concurrency_policy.max_parallel

                    if cb_state == "CB_CLOSED":
                        retries = 0 if item.succeeded else a_policy.retry_policy.max_retries
                        delay_sec = 0.0 if item.succeeded else a_policy.retry_policy.initial_delay_seconds
                        cb_window.append(item.succeeded)
                        if len(cb_window) > HCB_ROLLING_WINDOW:
                            cb_window = cb_window[-HCB_ROLLING_WINDOW:]
                        if len(cb_window) >= HCB_ROLLING_WINDOW:
                            error_rate = 1.0 - (sum(cb_window) / len(cb_window))
                            if error_rate >= HCB_ERROR_THRESHOLD:
                                cb_state = "CB_OPEN"
                                cb_open_remaining = HCB_SLEEP_WINDOW_EVENTS
                                cb_window = []
                                result.state_transitions.append(cb_state)
                    elif cb_state == "CB_OPEN":
                        # Fail fast: circuit is tripped, no delivery attempt is made at all.
                        retries = 0
                        delay_sec = 0.0
                        cb_open_remaining -= 1
                        if cb_open_remaining <= 0:
                            cb_state = "CB_HALF_OPEN"
                            result.state_transitions.append(cb_state)
                    else:  # CB_HALF_OPEN: exactly one probe attempt
                        if item.succeeded:
                            retries = 0
                            delay_sec = 0.0
                            cb_state = "CB_CLOSED"
                            cb_window = []
                        else:
                            retries = a_policy.retry_policy.max_retries
                            delay_sec = a_policy.retry_policy.initial_delay_seconds
                            cb_state = "CB_OPEN"
                            cb_open_remaining = HCB_SLEEP_WINDOW_EVENTS
                        result.state_transitions.append(cb_state)
                else:
                    # Strategy C (AEDE), C' (no isolation), C'' (no hysteresis)
                    policy, _ = synthesize_policy(
                        endpoint_id=endpoint_id,
                        state_before=current_state,
                        state_after=new_state,
                        eri=eri,
                        thresholds=self.sm_thresholds,
                        computed_at=current_time,
                    )
                    if self.strategy_config.strategy == "STRATEGY_C_PRIME":
                        # Ablation Strategy C' (no isolation): retries executed even while QUARANTINED
                        if item.succeeded:
                            retries = 0
                        elif current_state == EndpointState.QUARANTINED:
                            retries = 5  # Continues attempting delivery without isolation
                        else:
                            retries = policy.retry_policy.max_retries
                    else:
                        # Standard Strategy C and Strategy C'' (with isolation)
                        if current_state == EndpointState.QUARANTINED:
                            retries = 0
                        elif item.succeeded:
                            retries = 0
                        else:
                            retries = policy.retry_policy.max_retries
                    delay_sec = policy.retry_policy.initial_delay_seconds if not item.succeeded else 0.0
                    concurrency = policy.concurrency_policy.max_parallel

                # 4. Update failure streak
                if item.succeeded:
                    failure_streak = 0
                else:
                    failure_streak += 1

                # 5. Record Attempt
                result.attempts.append(
                    AttemptRecord(
                        succeeded=item.succeeded,
                        retry_count=retries,
                        latency_ms=item.latency_ms,
                        timed_out=item.timed_out,
                        final_state=current_state.value,
                        eri_score=eri.score,
                        delay_seconds=delay_sec,
                        max_parallel=concurrency,
                    )
                )

        result.finished_at = current_time

        # Store run into benchmark store
        successful = sum(1 for a in result.attempts if a.succeeded)
        benchmark_store.add_run(
            BenchmarkRunSummary(
                run_id=result.run_id,
                scenario=result.scenario_name,
                strategy=result.strategy,
                total_events=len(result.attempts),
                successful_deliveries=successful,
                failed_deliveries=len(result.attempts) - successful,
                avg_retry_count=compute_avg_retry_count(result),
                avg_recovery_time_seconds=float(compute_recovery_time_events(result)),
                started_at=result.started_at.isoformat(),
                finished_at=result.finished_at.isoformat(),
            )
        )

        return result
