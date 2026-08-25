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
    ) -> List[Telemetry]:
        """Generate a block of synthetic Telemetry records matching the phase specification."""
        items: List[Telemetry] = []
        for i in range(count):
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
    strategy: str  # "STRATEGY_A", "STRATEGY_B", "STRATEGY_C"
    feature_flags: Optional[Dict[str, bool]] = None


class BenchmarkRunner:
    """Executes a benchmark experiment across a scenario, collecting comparative metrics."""

    def __init__(
        self,
        strategy_config: DeliveryStrategyConfig,
        eri_weights: Optional[ERIWeights] = None,
        sm_thresholds: Optional[StateMachineThresholds] = None,
        window_size: int = 100,
        random_seed: int = 42,
    ) -> None:
        self.strategy_config = strategy_config
        self.eri_weights = eri_weights or ERIWeights(
            success_rate_weight=0.35,
            latency_weight=0.20,
            consecutive_failures_weight=0.20,
            timeout_weight=0.10,
            http_5xx_weight=0.05,
            queue_depth_weight=0.05,
            recovery_trend_weight=0.05,
        )
        self.sm_thresholds = sm_thresholds or StateMachineThresholds(
            degradation_threshold=80.0,
            degradation_recovery_threshold=90.0,
            unstable_threshold=60.0,
            unstable_recovery_threshold=75.0,
            quarantine_threshold=30.0,
            recovery_threshold=90.0,
        )
        self.window_size = window_size
        self.random_seed = random_seed
        self.generator = SyntheticTelemetryGenerator(seed=random_seed)
        self.strategy_a = StrategyA()
        self.strategy_b = StrategyB()

    def run(
        self,
        scenario: Scenario,
        endpoint_id: str = "benchmark-ep",
        start_time: Optional[datetime] = None,
    ) -> BenchmarkResult:
        """Run an entire scenario through the configured strategy and produce a BenchmarkResult."""
        current_time = start_time or datetime.now(timezone.utc)
        result = BenchmarkResult(
            scenario_id=scenario.scenario_id,
            scenario_name=scenario.name,
            strategy=self.strategy_config.strategy,
            started_at=current_time,
        )

        current_state = EndpointState.AVAILABLE
        result.state_transitions = [current_state.value]

        sliding_window: List[Telemetry] = []
        failure_streak = 0

        for phase in scenario.phases:
            telemetry_batch = self.generator.generate(
                phase=phase,
                count=phase.duration_events,
                endpoint_id=endpoint_id,
                start_time=current_time,
            )

            for item in telemetry_batch:
                current_time += timedelta(seconds=1)
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
                if self.strategy_config.strategy == "STRATEGY_C":
                    new_state = next_state(
                        current=current_state,
                        eri=eri,
                        thresholds=self.sm_thresholds,
                    )
                else:
                    # Strategy A & B do not have an endpoint state model
                    new_state = EndpointState.AVAILABLE

                if new_state != current_state:
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
                elif self.strategy_config.strategy == "STRATEGY_B":
                    # Adaptive retry baseline without state model
                    policy, _ = self.strategy_b.evaluate(
                        endpoint_id=endpoint_id,
                        endpoint_state=EndpointState.AVAILABLE,
                        eri=eri,
                        computed_at=current_time,
                    )
                    retries = 0 if item.succeeded else policy.retry_policy.max_retries
                else:
                    # Strategy C (AEDE): state-driven policy synthesis
                    policy, _ = synthesize_policy(
                        endpoint_id=endpoint_id,
                        state_before=current_state,
                        state_after=new_state,
                        eri=eri,
                        thresholds=self.sm_thresholds,
                        computed_at=current_time,
                    )
                    if current_state == EndpointState.QUARANTINED:
                        # Full isolation: no delivery attempts / retries wasted
                        retries = 0
                    elif item.succeeded:
                        retries = 0
                    else:
                        retries = policy.retry_policy.max_retries

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
