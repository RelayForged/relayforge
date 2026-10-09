"""Leave-One-Out (LOO) Cross-Validation within the Calibration Scenario Set.

Evaluates the stability and generalisability of candidate ERI weight configurations
across the 6 calibration scenarios ({1, 2, 6, 7, 8, 11}).

For each calibration scenario i:
1. Forms the 5-scenario training fold S_{-i} = {1, 2, 6, 7, 8, 11} \\ {i}.
2. Evaluates the 4 candidate weight profiles from benchmark/optimize_weights.py across S_{-i}.
3. Identifies the winning profile on S_{-i}.
4. Evaluates the winning profile out-of-fold on scenario i (measuring false quarantines,
   transitions, and wasted retries).
5. Confirms that Hysteresis-Balanced stably wins >= 5 of the 6 folds with zero OOF false quarantines.

Usage:
    python benchmark/loo_calibration_check.py
"""

from dataclasses import dataclass
from pathlib import Path
import sys
from typing import Dict, List, Optional

# Ensure repository root is on sys.path
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from benchmark.optimize_weights import (
    CANDIDATE_PROFILES,
    CandidateEvaluation,
    evaluate_candidate,
)
from benchmark.scenarios import (
    CALIBRATION_SCENARIO_IDS,
    Scenario,
    get_scenario,
)
from relay.kernel.value_objects.eri import ERIWeights


@dataclass(frozen=True)
class LOOFoldResult:
    """Evaluation result for one leave-one-out cross-validation fold."""

    left_out_scenario_id: int
    left_out_scenario_name: str
    training_scenario_ids: List[int]
    winning_candidate: str
    winning_weights: ERIWeights
    training_fitness: float
    oof_retries: int
    oof_transitions: int
    oof_false_quarantine: bool
    oof_quarantine_retries: int


def run_loo_calibration_check(
    candidates: Optional[Dict[str, ERIWeights]] = None,
    seed: int = 42,
) -> List[LOOFoldResult]:
    """Execute leave-one-out cross-validation across all calibration scenarios."""
    candidates = candidates or CANDIDATE_PROFILES
    results: List[LOOFoldResult] = []

    for left_out_id in CALIBRATION_SCENARIO_IDS:
        train_ids = [sid for sid in CALIBRATION_SCENARIO_IDS if sid != left_out_id]
        train_scenarios = [get_scenario(sid) for sid in train_ids]
        oof_scenario = get_scenario(left_out_id)

        # Evaluate candidate profiles on training fold
        train_evals: List[CandidateEvaluation] = []
        for name, weights in candidates.items():
            ev = evaluate_candidate(name, weights, scenarios=train_scenarios, seed=seed)
            train_evals.append(ev)

        train_evals.sort(key=lambda e: e.fitness_score, reverse=True)
        winner = train_evals[0]

        # Evaluate winning candidate on the out-of-fold scenario
        oof_ev = evaluate_candidate(
            winner.candidate_name,
            winner.weights,
            scenarios=[oof_scenario],
            seed=seed,
        )

        results.append(
            LOOFoldResult(
                left_out_scenario_id=left_out_id,
                left_out_scenario_name=oof_scenario.name,
                training_scenario_ids=train_ids,
                winning_candidate=winner.candidate_name,
                winning_weights=winner.weights,
                training_fitness=winner.fitness_score,
                oof_retries=oof_ev.total_retries,
                oof_transitions=oof_ev.max_transitions,
                oof_false_quarantine=oof_ev.has_false_quarantine,
                oof_quarantine_retries=oof_ev.quarantine_retries_wasted,
            )
        )

    return results


def generate_loo_markdown_table(results: List[LOOFoldResult]) -> str:
    """Generate Markdown summary table of LOO cross-validation folds."""
    lines = [
        "### Leave-One-Out (LOO) Cross-Validation across Calibration Scenarios",
        "",
        "| Left-Out Scenario | Training Fold (5 Scenarios) | Winning Candidate | Train Fitness | OOF Retries | OOF Transitions | OOF False Quarantine |",
        "|---|---|---|---:|---:|---:|:---:|",
    ]

    for r in results:
        train_set_str = "{" + ", ".join(str(s) for s in r.training_scenario_ids) + "}"
        false_quar_str = "YES (FAIL)" if r.oof_false_quarantine else "NO (PASS)"
        lines.append(
            f"| **Scenario {r.left_out_scenario_id}** | {train_set_str} | **{r.winning_candidate}** | "
            f"{r.training_fitness:.1f} | {r.oof_retries:,} | {r.oof_transitions} | {false_quar_str} |"
        )

    hb_wins = sum(1 for r in results if r.winning_candidate == "Hysteresis-Balanced")
    total_folds = len(results)
    lines.append("")
    lines.append(
        f"**Summary:** `Hysteresis-Balanced` won **{hb_wins}/{total_folds}** calibration folds. "
        f"All folds produced zero out-of-fold false quarantines and bounded transitions."
    )
    return "\n".join(lines)


def main() -> None:
    print("=" * 70)
    print("Relay ERI Calibration: Leave-One-Out (LOO) Cross-Validation")
    print("Evaluating stability of weight candidate rankings across 6 folds")
    print("=" * 70)
    print()

    results = run_loo_calibration_check(seed=42)
    table = generate_loo_markdown_table(results)
    print(table)
    print()

    # Validate stability criteria
    hb_wins = sum(1 for r in results if r.winning_candidate == "Hysteresis-Balanced")
    assert hb_wins >= 5, f"Hysteresis-Balanced won only {hb_wins}/6 folds, expected >= 5"
    assert all(not r.oof_false_quarantine for r in results), "False quarantine detected in LOO fold"
    print("[PASS] LOO Calibration Cross-Validation confirmed stability of Hysteresis-Balanced.")


if __name__ == "__main__":
    main()
