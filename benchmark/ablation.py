"""Component Ablation Study & Oracle Upper Bound Evaluation for AEDE.

Evaluates the separate quantitative contributions of:
1. Adaptive Policy Synthesis without Quarantine Isolation (Strategy C')
2. State Machine Hysteresis Dampening (Strategy C'')
3. Full AEDE with Isolation and Hysteresis (Strategy C)
4. Omniscient Oracle Upper Bound (Strategy O)
compared against Static (Strategy A) and Adaptive (Strategy B) baselines, plus an external
baseline representing an off-the-shelf pattern rather than anything this project designed:
5. Strategy HCB, a faithful reimplementation of Netflix Hystrix's default circuit-breaker
   configuration (requestVolumeThreshold=20, errorThresholdPercentage=50,
   sleepWindowInMilliseconds=5000, translated 1:1 into this harness's 1-event=1-second
   simulated time model). HCB has no access to AEDE's ERI or state machine.

Usage:
    python benchmark/ablation.py
"""

from dataclasses import dataclass
from pathlib import Path
import statistics
import sys
from typing import Any, Dict, List, Optional

# Ensure repository root is on sys.path
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from benchmark.metrics import (
    compute_avg_retry_count,
    compute_delivery_success_rate,
    compute_quarantine_count,
    compute_state_transition_count,
    compute_total_retry_delay_seconds,
    compute_total_wasted_retries,
)
from benchmark.runner import BenchmarkRunner, DeliveryStrategyConfig
from benchmark.scenarios import (
    Scenario,
    get_all_scenarios,
    get_evaluation_scenarios,
    get_scenario,
)


ABLATION_STRATEGIES = [
    ("STRATEGY_A", "Strategy A (Static Retry)"),
    ("STRATEGY_B", "Strategy B (Adaptive Retry)"),
    ("STRATEGY_HCB", "Strategy HCB (Hystrix-Style Circuit Breaker)"),
    ("STRATEGY_C_PRIME", "Strategy C' (AEDE No Isolation)"),
    ("STRATEGY_C_DOUBLE_PRIME", "Strategy C'' (AEDE No Hysteresis)"),
    ("STRATEGY_C", "Strategy C (Full AEDE)"),
    ("STRATEGY_O", "Strategy O (Oracle Upper Bound)"),
]

# STRATEGY_HCB counts its "open" (isolating) state using "CB_OPEN" rather than "QUARANTINED"
# (it is an independent mechanism, not AEDE's state machine) -- compute_quarantine_count()
# only recognizes the literal string "QUARANTINED", so its open-transition count is tallied
# separately here rather than by modifying that shared metrics function.
def _count_transitions_into(state_list: List[str], target: str) -> int:
    count = 0
    for i in range(1, len(state_list)):
        if state_list[i] == target and state_list[i - 1] != target:
            count += 1
    return count


@dataclass
class AblationStrategySummary:
    """Aggregated multi-scenario evaluation results for an ablation strategy."""

    strategy_key: str
    strategy_label: str
    total_events: int
    total_retries: float
    total_retries_std: float
    avg_retries_per_event: float
    total_delay_seconds: float
    total_transitions: float
    total_quarantines: float
    outage_retries: float
    outage_reduction_vs_a: float


def run_ablation_study(
    scenarios: Optional[List[Scenario]] = None,
    seeds: Optional[List[int]] = None,
) -> Dict[str, AblationStrategySummary]:
    """Execute complete ablation study across configured scenarios and seeds."""
    scenarios = scenarios or get_evaluation_scenarios()
    seeds = seeds or [42, 123, 456, 789, 1337]  # matches the 5-seed protocol used throughout §4.9-4.14
    # Matches benchmark/run_experiments.py's Hypothesis 1 grouping: genuine outage/severe-failure
    # scenarios only. Scenario 14 (Long Sustained Mild Degradation, 75-80% success throughout) is
    # deliberately excluded -- it isn't an outage, and folding it in here would reintroduce the
    # same categorization error that was fixed in run_experiments.py.
    outage_ids = [3, 4, 9, 10, 12]

    raw_results: Dict[str, List[Dict[str, float]]] = {
        s_key: [] for s_key, _ in ABLATION_STRATEGIES
    }

    for seed in seeds:
        # Run each strategy across scenarios
        for s_key, _ in ABLATION_STRATEGIES:
            runner = BenchmarkRunner(
                strategy_config=DeliveryStrategyConfig(strategy=s_key),
                random_seed=seed,
            )
            seed_retries = 0
            seed_outage_retries = 0
            seed_delay = 0.0
            seed_transitions = 0
            seed_quarantines = 0
            total_events = 0

            for sc in scenarios:
                res = runner.run(sc)
                retries = compute_total_wasted_retries(res)
                seed_retries += retries
                if sc.scenario_id in outage_ids:
                    seed_outage_retries += retries
                seed_delay += compute_total_retry_delay_seconds(res)
                seed_transitions += compute_state_transition_count(res)
                if s_key == "STRATEGY_HCB":
                    seed_quarantines += _count_transitions_into(res.state_transitions, "CB_OPEN")
                else:
                    seed_quarantines += compute_quarantine_count(res)
                total_events += len(res.attempts)

            raw_results[s_key].append(
                {
                    "total_retries": float(seed_retries),
                    "outage_retries": float(seed_outage_retries),
                    "total_delay": seed_delay,
                    "total_transitions": float(seed_transitions),
                    "total_quarantines": float(seed_quarantines),
                    "total_events": float(total_events),
                }
            )

    # Compute static baseline average outage retries for reduction comparison
    strat_a_outage_mean = statistics.mean(
        r["outage_retries"] for r in raw_results["STRATEGY_A"]
    )

    summaries: Dict[str, AblationStrategySummary] = {}
    for s_key, s_label in ABLATION_STRATEGIES:
        data = raw_results[s_key]
        retries_list = [r["total_retries"] for r in data]
        outage_list = [r["outage_retries"] for r in data]
        delay_list = [r["total_delay"] for r in data]
        trans_list = [r["total_transitions"] for r in data]
        quar_list = [r["total_quarantines"] for r in data]
        total_evts = int(data[0]["total_events"])

        mean_retries = statistics.mean(retries_list)
        std_retries = statistics.stdev(retries_list) if len(retries_list) > 1 else 0.0
        mean_outage = statistics.mean(outage_list)
        outage_red = (
            ((strat_a_outage_mean - mean_outage) / strat_a_outage_mean * 100.0)
            if strat_a_outage_mean > 0
            else 0.0
        )

        summaries[s_key] = AblationStrategySummary(
            strategy_key=s_key,
            strategy_label=s_label,
            total_events=total_evts,
            total_retries=round(mean_retries, 1),
            total_retries_std=round(std_retries, 1),
            avg_retries_per_event=round(mean_retries / total_evts, 3),
            total_delay_seconds=round(statistics.mean(delay_list), 1),
            total_transitions=round(statistics.mean(trans_list), 1),
            total_quarantines=round(statistics.mean(quar_list), 1),
            outage_retries=round(mean_outage, 1),
            outage_reduction_vs_a=round(outage_red, 1),
        )

    return summaries


def generate_ablation_markdown_table(
    summaries: Dict[str, AblationStrategySummary],
) -> str:
    """Format ablation study results into a clear Markdown comparison table."""
    lines = [
        "### Component Ablation Study & Oracle Upper Bound Comparison (Held-Out Evaluation Set)",
        "",
        "| Strategy | Description | Total Retries | Outage Retries | Outage Reduction vs A | Total Transitions | Quarantines | Delay (s) |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]

    for s_key, s_label in ABLATION_STRATEGIES:
        s = summaries[s_key]
        lines.append(
            f"| **{s_label}** | `{s_key}` | **{s.total_retries:,.1f} ± {s.total_retries_std:.1f}** | "
            f"**{s.outage_retries:,.1f}** | **{s.outage_reduction_vs_a:+.1f}%** | "
            f"{s.total_transitions:.1f} | {s.total_quarantines:.1f} | {s.total_delay_seconds:,.0f}s |"
        )

    strat_c = summaries["STRATEGY_C"]
    strat_cp = summaries["STRATEGY_C_PRIME"]
    strat_cdp = summaries["STRATEGY_C_DOUBLE_PRIME"]
    strat_o = summaries["STRATEGY_O"]

    lines.append("")
    lines.append("#### Component Contributions Analysis:")
    lines.append(
        f"1. **Isolation Value (C vs C'):** Quarantine isolation contributes an additional "
        f"**{strat_cp.outage_retries - strat_c.outage_retries:,.0f} fewer wasted retries** on outage scenarios "
        f"({strat_c.outage_reduction_vs_a:.1f}% reduction with isolation vs {strat_cp.outage_reduction_vs_a:.1f}% without)."
    )
    lines.append(
        f"2. **Hysteresis Value (C vs C''):** Hysteresis dampening reduces state transitions from "
        f"**{strat_cdp.total_transitions:.1f}** down to **{strat_c.total_transitions:.1f}** (eliminating rapid flapping)."
    )
    lines.append(
        f"3. **Oracle Bound Proximity (C vs O):** Theoretical omniscient lower bound is **{strat_o.total_retries:,.0f} retries**; "
        f"Strategy C achieves within **{(strat_c.total_retries - strat_o.total_retries):,.0f} retries** of the theoretical optimum without any future knowledge."
    )
    return "\n".join(lines)


def main() -> None:
    print("=" * 80)
    print("Relay Research Contribution: Component Ablation & Oracle Upper Bound Study")
    print("Evaluating Strategy A, B, HCB, C', C'', C, and O across held-out scenarios")
    print("=" * 80)
    print()

    summaries = run_ablation_study()
    table = generate_ablation_markdown_table(summaries)
    print(table)
    print()

    # Integrity assertions
    assert summaries["STRATEGY_C"].outage_retries < summaries["STRATEGY_C_PRIME"].outage_retries, (
        "Strategy C must have fewer outage retries than Strategy C' (no isolation)"
    )
    assert summaries["STRATEGY_C"].total_transitions < summaries["STRATEGY_C_DOUBLE_PRIME"].total_transitions, (
        "Strategy C must have fewer transitions than Strategy C'' (no hysteresis)"
    )
    assert summaries["STRATEGY_O"].total_retries <= summaries["STRATEGY_C"].total_retries, (
        "Oracle baseline must have less than or equal retries compared to Strategy C"
    )
    assert summaries["STRATEGY_HCB"].total_quarantines > 0, (
        "Strategy HCB must trip open at least once across the held-out outage scenarios"
    )
    print("[PASS] All ablation integrity checks confirmed.")


if __name__ == "__main__":
    main()
