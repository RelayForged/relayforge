"""Empirical Trial-and-Error ERI Weight Optimization.

Sweeps candidate ERI weight configurations across all 10 failure injection scenarios
defined in benchmark/scenarios.py using BenchmarkRunner (Strategy C).

Evaluates and scores each candidate on:
1. False quarantine prevention (Scenarios 1 & 2 must never enter QUARANTINED)
2. Early degradation detection (Scenario 10 detection event index)
3. Fast quarantine during prolonged outages (Scenario 4 events to QUARANTINED)
4. State stability / hysteresis under flapping (Scenario 5 transition count <= 3)
5. Network & resource conservation (total retries across all scenarios)
6. Zero retries wasted while quarantined

Usage:
    python benchmark/optimize_weights.py
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import sys

# Ensure repository root is on sys.path
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from benchmark.metrics import BenchmarkResult, compute_recovery_time_events
from benchmark.runner import BenchmarkRunner, DeliveryStrategyConfig
from benchmark.scenarios import (
    ALL_SCENARIOS,
    Scenario,
    get_all_scenarios,
    get_calibration_scenarios,
    get_scenario,
)
from relay.kernel.value_objects.endpoint_state import EndpointState
from relay.kernel.value_objects.eri import ERIWeights


# Candidate configurations formulated according to PLAN.md Stage 1 specifications
CANDIDATE_PROFILES: Dict[str, ERIWeights] = {
    "Baseline Default": ERIWeights(
        success_rate_weight=0.35,
        latency_weight=0.20,
        consecutive_failures_weight=0.20,
        timeout_weight=0.10,
        http_5xx_weight=0.05,
        queue_depth_weight=0.05,
        recovery_trend_weight=0.05,
    ),
    "Failure-Aggressive": ERIWeights(
        success_rate_weight=0.25,
        latency_weight=0.15,
        consecutive_failures_weight=0.30,
        timeout_weight=0.15,
        http_5xx_weight=0.10,
        queue_depth_weight=0.025,
        recovery_trend_weight=0.025,
    ),
    "Latency-Sensitive": ERIWeights(
        success_rate_weight=0.25,
        latency_weight=0.30,
        consecutive_failures_weight=0.15,
        timeout_weight=0.20,
        http_5xx_weight=0.05,
        queue_depth_weight=0.025,
        recovery_trend_weight=0.025,
    ),
    "Hysteresis-Balanced": ERIWeights(
        success_rate_weight=0.30,
        latency_weight=0.15,
        consecutive_failures_weight=0.20,
        timeout_weight=0.15,
        http_5xx_weight=0.08,
        queue_depth_weight=0.02,
        recovery_trend_weight=0.10,
    ),
}


@dataclass
class CandidateEvaluation:
    """Detailed evaluation metrics and score for an ERI weight candidate."""

    candidate_name: str
    weights: ERIWeights
    has_false_quarantine: bool
    detection_event: int
    quarantine_event: int
    max_transitions: int
    total_retries: int
    quarantine_retries_wasted: int
    never_recovers: bool
    mttr: int
    fitness_score: float
    scenario_results: Dict[int, BenchmarkResult] = field(default_factory=dict)

    @property
    def scen5_transitions(self) -> int:
        """Backward compatibility alias for max_transitions."""
        return self.max_transitions

    @property
    def scen10_detection_event(self) -> int:
        """Backward compatibility alias for detection_event."""
        return self.detection_event

    @property
    def scen4_quarantine_event(self) -> int:
        """Backward compatibility alias for quarantine_event."""
        return self.quarantine_event


def evaluate_candidate(
    candidate_name: str,
    weights: ERIWeights,
    scenarios: Optional[List[Scenario]] = None,
    seed: int = 42,
) -> CandidateEvaluation:
    """Run calibration scenarios through Strategy C with given weights and collect evaluation metrics."""
    if scenarios is None:
        scenarios = get_calibration_scenarios()

    runner = BenchmarkRunner(
        strategy_config=DeliveryStrategyConfig(strategy="STRATEGY_C"),
        eri_weights=weights,
        random_seed=seed,
    )

    results_by_id: Dict[int, BenchmarkResult] = {}
    total_retries = 0
    quarantine_retries_wasted = 0

    for sc in scenarios:
        res = runner.run(sc)
        results_by_id[sc.scenario_id] = res
        for att in res.attempts:
            total_retries += att.retry_count
            if att.final_state == EndpointState.QUARANTINED.value and att.retry_count > 0:
                quarantine_retries_wasted += att.retry_count

    # 1. False quarantine check driven by should_never_quarantine
    has_false_quarantine = any(
        sc.should_never_quarantine and (EndpointState.QUARANTINED.value in results_by_id[sc.scenario_id].state_transitions)
        for sc in scenarios
        if sc.scenario_id in results_by_id
    )

    # 2. Early degradation detection in Scenario 11 (bursty traffic; degradation onset ~event 20)
    if 11 in results_by_id:
        res11 = results_by_id[11]
        detection_event = next(
            (
                i
                for i, a in enumerate(res11.attempts)
                if a.final_state != EndpointState.AVAILABLE.value
            ),
            len(res11.attempts),
        )
    else:
        detection_event = 200

    # 3. Quarantine latency under persistent failure in Scenario 7 (connection failure)
    if 7 in results_by_id:
        res7 = results_by_id[7]
        quarantine_event = next(
            (
                i
                for i, a in enumerate(res7.attempts)
                if a.final_state == EndpointState.QUARANTINED.value
            ),
            len(res7.attempts),
        )
    else:
        quarantine_event = 200

    # 4. Flapping / stability across evaluated scenarios
    max_transitions = max(
        (max(0, len(res.state_transitions) - 1) for res in results_by_id.values()),
        default=0,
    )

    # 5. Recovery completeness & speed: any scenario that both reaches QUARANTINED and has a
    # manual_restore phase is expected to fully recover to AVAILABLE by the end of the run. A
    # candidate that gets permanently stuck in RECOVERING is broken (added after a real instance
    # of this was found in production defaults -- see docs/report/04-evaluation-methodology.md
    # §4.14.5) and is disqualified the same way a false quarantine is. `mttr` is the worst
    # (slowest) recovery time observed across such scenarios, in events.
    never_recovers = False
    mttr_values: List[int] = []
    for sc in scenarios:
        if sc.scenario_id not in results_by_id:
            continue
        res = results_by_id[sc.scenario_id]
        if EndpointState.QUARANTINED.value not in res.state_transitions:
            continue
        if not any(p.manual_restore for p in sc.phases):
            continue
        if res.attempts[-1].final_state != EndpointState.AVAILABLE.value:
            never_recovers = True
        mttr_values.append(compute_recovery_time_events(res))
    mttr = max(mttr_values) if mttr_values else 0

    # 6. Composite Fitness Scoring
    score = 1000.0

    if has_false_quarantine:
        score -= 10000.0

    if never_recovers:
        score -= 10000.0

    score += max(0.0, (200.0 - detection_event) * 2.0)
    score += max(0.0, (200.0 - quarantine_event) * 2.0)

    # Penalize state machine flapping (ideal is <= 5 transitions)
    if max_transitions > 5:
        score -= (max_transitions - 5) * 150.0

    # Resource conservation: reward low total retry count
    score += max(0.0, (5000.0 - total_retries) * 0.5)

    # Strict penalty if retries leaked during quarantine
    score -= quarantine_retries_wasted * 500.0

    # Penalize slow recovery (modest weight -- this is a secondary concern relative to false
    # quarantine / retry leaks, but must not be zero, which is how this got missed before)
    score -= mttr * 2.0

    return CandidateEvaluation(
        candidate_name=candidate_name,
        weights=weights,
        has_false_quarantine=has_false_quarantine,
        detection_event=detection_event,
        quarantine_event=quarantine_event,
        max_transitions=max_transitions,
        total_retries=total_retries,
        quarantine_retries_wasted=quarantine_retries_wasted,
        never_recovers=never_recovers,
        mttr=mttr,
        fitness_score=round(score, 2),
        scenario_results=results_by_id,
    )


def run_optimization(
    candidates: Optional[Dict[str, ERIWeights]] = None,
    seed: int = 42,
) -> Tuple[str, ERIWeights, List[CandidateEvaluation]]:
    """Execute parameter sweep across candidates on calibration scenarios and return winning profile."""
    if candidates is None:
        candidates = CANDIDATE_PROFILES

    evaluations: List[CandidateEvaluation] = []
    scenarios = get_calibration_scenarios()

    for name, weights in candidates.items():
        ev = evaluate_candidate(name, weights, scenarios=scenarios, seed=seed)
        evaluations.append(ev)

    evaluations.sort(key=lambda e: e.fitness_score, reverse=True)
    winner = evaluations[0]

    return winner.candidate_name, winner.weights, evaluations


def print_optimization_report(evaluations: List[CandidateEvaluation]) -> None:
    """Print structured markdown report of empirical trial-and-error optimization."""
    print("=" * 90)
    print("      STAGE 1 EMPIRICAL ERI WEIGHT CALIBRATION REPORT (CALIBRATION SET {1,2,6,7,8,11})")
    print("=" * 90)
    print(
        f"{'Rank':<5} {'Candidate Name':<22} {'Score':<9} {'FalseQuar':<10} "
        f"{'Det(Sc11)':<10} {'Quar(Sc7)':<10} {'MaxTrans':<11} {'TotalRetries':<12} "
        f"{'NeverRecov':<11} {'MTTR':<6}"
    )
    print("-" * 90)

    for rank, ev in enumerate(evaluations, start=1):
        fq_str = "YES (FAIL)" if ev.has_false_quarantine else "NO (PASS)"
        nr_str = "YES (FAIL)" if ev.never_recovers else "NO (PASS)"
        print(
            f"{rank:<5} {ev.candidate_name:<22} {ev.fitness_score:<9.1f} {fq_str:<10} "
            f"Event {ev.detection_event:<4} Event {ev.quarantine_event:<4} "
            f"{ev.max_transitions:<11} {ev.total_retries:<12} {nr_str:<11} {ev.mttr:<6}"
        )

    print("-" * 90)
    winner = evaluations[0]
    print(f"\nWINNER: {winner.candidate_name} (Fitness Score: {winner.fitness_score})")
    print("\nOptimal Weight Configuration:")
    w = winner.weights
    print(f"  success_rate_weight:         {w.success_rate_weight:.3f}")
    print(f"  latency_weight:              {w.latency_weight:.3f}")
    print(f"  consecutive_failures_weight: {w.consecutive_failures_weight:.3f}")
    print(f"  timeout_weight:              {w.timeout_weight:.3f}")
    print(f"  http_5xx_weight:             {w.http_5xx_weight:.3f}")
    print(f"  queue_depth_weight:          {w.queue_depth_weight:.3f}")
    print(f"  recovery_trend_weight:       {w.recovery_trend_weight:.3f}")
    print(
        f"  Sum check:                   {sum([w.success_rate_weight, w.latency_weight, w.consecutive_failures_weight, w.timeout_weight, w.http_5xx_weight, w.queue_depth_weight, w.recovery_trend_weight]):.4f}"
    )
    print("=" * 90)


if __name__ == "__main__":
    winner_name, optimal_weights, evals = run_optimization()
    print_optimization_report(evals)

    # Verification assertions
    assert not evals[0].has_false_quarantine, "Optimal candidate must have zero false quarantines!"
    assert evals[0].max_transitions <= 5, f"Optimal candidate must not flap excessively (got {evals[0].max_transitions})"
    assert evals[0].quarantine_retries_wasted == 0, "Zero retries should be wasted during quarantine!"
    assert not evals[0].never_recovers, "Optimal candidate must fully recover to AVAILABLE after every manual restore, not get stuck in RECOVERING!"
    assert evals[0].mttr <= 200, f"Optimal candidate recovers too slowly on calibration scenarios (MTTR={evals[0].mttr} events, expected <= 200)"
    print("\n[OK] All acceptance criteria validated successfully.")
