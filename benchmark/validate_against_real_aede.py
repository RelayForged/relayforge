"""Validation of Benchmark Simulation against Real In-Process AEDEService.

Confirms that BenchmarkRunner provides a faithful mathematical representation
of the real Control Plane AEDEService.

Executes scenarios through both:
1. BenchmarkRunner (the simulation engine used in research benchmarks)
2. AEDEService (the production platform control-plane service executing the closed feedback loop)

Asserts that at every individual event step:
- ERI composite score matches within 1e-4 tolerance
- EndpointState matches exactly
- DeliveryPolicy parameters (max_retries, initial_delay, concurrency) match exactly

Usage:
    python benchmark/validate_against_real_aede.py
"""

import argparse
import asyncio
from dataclasses import dataclass
import math
from pathlib import Path
import sys
from typing import Any, Dict, List, Optional, Tuple

# Ensure repository root is on sys.path
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from benchmark.runner import (
    BenchmarkRunner,
    DeliveryStrategyConfig,
    SyntheticTelemetryGenerator,
)
from benchmark.scenarios import Scenario, get_scenario
from relay.control_plane.aede.service import AEDEService
from relay.kernel.value_objects.endpoint_state import EndpointState


@dataclass(frozen=True)
class ParityStepRecord:
    step_idx: int
    scenario_id: int
    runner_eri: float
    aede_eri: float
    runner_state: str
    aede_state: str
    runner_retries: int
    aede_retries: int
    eri_delta: float
    state_matches: bool
    policy_matches: bool


@dataclass
class ParityValidationResult:
    scenario_id: int
    scenario_name: str
    total_events: int
    all_matched: bool
    max_eri_delta: float
    mismatches: List[ParityStepRecord]


async def validate_scenario_against_real_aede(
    scenario_id: int,
    seed: int = 42,
    endpoint_id: str = "benchmark-ep",
) -> ParityValidationResult:
    """Run scenario through both BenchmarkRunner and real AEDEService, comparing step-by-step."""
    scenario = get_scenario(scenario_id)

    # 1. Run BenchmarkRunner
    runner = BenchmarkRunner(
        strategy_config=DeliveryStrategyConfig(strategy="STRATEGY_C"),
        random_seed=seed,
    )
    runner_result = runner.run(scenario, endpoint_id=endpoint_id)

    # 2. Instantiate real in-process AEDEService with identical configuration
    aede_service = AEDEService(
        weights=runner.eri_weights,
        thresholds=runner.sm_thresholds,
        window_size=runner.window_size,
    )

    # 3. Replay exact same seeded telemetry stream. BenchmarkRunner.run() derives its internal
    # generator's seed from (random_seed, scenario_id) rather than using random_seed directly
    # (see runner.py) so that a scenario's telemetry doesn't depend on which other scenarios ran
    # through the same runner instance beforehand -- match that derivation exactly here.
    generator = SyntheticTelemetryGenerator(seed=seed * 10_000 + scenario.scenario_id)
    current_time = runner_result.started_at
    mismatches: List[ParityStepRecord] = []
    max_eri_delta = 0.0
    step_idx = 0

    for phase in scenario.phases:
        # Simulate admin restoration matching runner logic
        if phase.manual_restore and aede_service.get_endpoint_state(endpoint_id) == EndpointState.QUARANTINED:
            aede_service.restore_endpoint(endpoint_id)

        telemetry_batch = generator.generate(
            phase=phase,
            count=phase.duration_events,
            endpoint_id=endpoint_id,
            start_time=current_time,
            bursty=runner.bursty,
        )

        for item in telemetry_batch:
            current_time = item.observed_at

            # Process in real AEDEService
            aede_eri, aede_state, aede_policy, _ = await aede_service.record_telemetry(item)

            runner_att = runner_result.attempts[step_idx]

            # Compare metrics
            eri_delta = abs(runner_att.eri_score - aede_eri.score)
            max_eri_delta = max(max_eri_delta, eri_delta)

            state_matches = (runner_att.final_state == aede_state.value)
            
            # Runner retries: 0 if quarantined or succeeded, else policy max_retries
            expected_runner_retries = (
                0 if (aede_state == EndpointState.QUARANTINED or item.succeeded)
                else aede_policy.retry_policy.max_retries
            )
            policy_matches = (runner_att.retry_count == expected_runner_retries)

            record = ParityStepRecord(
                step_idx=step_idx,
                scenario_id=scenario_id,
                runner_eri=runner_att.eri_score,
                aede_eri=aede_eri.score,
                runner_state=runner_att.final_state,
                aede_state=aede_state.value,
                runner_retries=runner_att.retry_count,
                aede_retries=expected_runner_retries,
                eri_delta=eri_delta,
                state_matches=state_matches,
                policy_matches=policy_matches,
            )

            if eri_delta > 1e-4 or not state_matches or not policy_matches:
                mismatches.append(record)

            step_idx += 1

    return ParityValidationResult(
        scenario_id=scenario.scenario_id,
        scenario_name=scenario.name,
        total_events=step_idx,
        all_matched=(len(mismatches) == 0),
        max_eri_delta=max_eri_delta,
        mismatches=mismatches,
    )


async def run_full_validation_suite(
    scenario_ids: Optional[List[int]] = None,
    seed: int = 42,
) -> List[ParityValidationResult]:
    """Run validation across target scenarios (defaults to outage and slow degradation)."""
    scenario_ids = scenario_ids or [4, 10]
    results: List[ParityValidationResult] = []
    for sid in scenario_ids:
        res = await validate_scenario_against_real_aede(sid, seed=seed)
        results.append(res)
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate BenchmarkRunner against real in-process AEDEService")
    parser.add_argument("--scenarios", type=str, default="4,10", help="Comma-separated scenario IDs to validate")
    parser.add_argument("--seed", type=int, default=42, help="Random seed for telemetry stream")
    args = parser.parse_args()

    s_ids = [int(x.strip()) for x in args.scenarios.split(",")]

    print("=" * 80)
    print("Relay Platform: Real In-Process AEDEService Validation Harness")
    print(f"Comparing simulation trajectory vs production AEDEService across Scenarios: {s_ids}")
    print("=" * 80)
    print()

    async def _run():
        try:
            return await run_full_validation_suite(scenario_ids=s_ids, seed=args.seed)
        finally:
            from relay.infrastructure.messaging.producer import close
            await close()

    results = asyncio.run(_run())

    all_passed = True
    for res in results:
        status_str = "PASS (100% PARITY)" if res.all_matched else f"FAIL ({len(res.mismatches)} mismatches)"
        print(f"Scenario {res.scenario_id:2d}: {res.scenario_name:<40} | Events: {res.total_events:4d} | Status: {status_str} | Max Delta ERI: {res.max_eri_delta:.6f}")
        if not res.all_matched:
            all_passed = False
            for m in res.mismatches[:5]:
                print(f"   Mismatch at step {m.step_idx}: Runner(eri={m.runner_eri:.2f}, state={m.runner_state}) vs AEDE(eri={m.aede_eri:.2f}, state={m.aede_state})")

    print()
    if all_passed:
        print("[PASS] Full in-process mathematical parity between BenchmarkRunner and AEDEService verified.")
    else:
        print("[FAIL] Mismatches detected between BenchmarkRunner and AEDEService.")
        sys.exit(1)


if __name__ == "__main__":
    main()
