"""
Does a timed quarantine (ADR 0027) change Strategy C's benchmark results?

Runs Strategy C on every scenario and seed with the hold off (the published
model: only a scenario's manual restore ends a quarantine) and with several
hold lengths, and compares wasted retries, success rate and quarantines.
Strategies A and B have no quarantine, so they are run once as reference.

    python benchmark/timed_quarantine_check.py
"""
from __future__ import annotations

import statistics
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from benchmark.metrics import summarise  # noqa: E402
from benchmark.runner import BenchmarkRunner, DeliveryStrategyConfig  # noqa: E402
from benchmark.scenarios import EVALUATION_SCENARIO_IDS, get_all_scenarios  # noqa: E402

SEEDS = [42, 123, 456, 789, 1337]
HOLDS = [0, 30, 60, 120, 300]


def run(strategy: str, scenario, seed: int, hold: int = 0) -> dict:
    return summarise(
        BenchmarkRunner(
            DeliveryStrategyConfig(strategy=strategy), random_seed=seed, quarantine_hold_seconds=hold
        ).run(scenario)
    )


def totals(rows: list[dict]) -> tuple[int, float, int]:
    return (
        sum(r["total_wasted_retries"] for r in rows),
        statistics.mean(r["delivery_success_rate"] for r in rows),
        sum(r["quarantine_count"] for r in rows),
    )


def main() -> None:
    scenarios = get_all_scenarios()
    held_out = [s for s in scenarios if s.scenario_id in EVALUATION_SCENARIO_IDS]

    for label, group in (("Held-out (reported)", held_out), ("All 14", scenarios)):
        print(f"\n== {label}: {len(group)} scenarios x {len(SEEDS)} seeds ==")
        ref = {
            s: totals([run(s, sc, seed) for sc in group for seed in SEEDS])
            for s in ("STRATEGY_A", "STRATEGY_B")
        }
        print(f"{'run':<22}{'wasted retries':>16}{'success':>10}{'quarantines':>13}{'vs A':>8}{'vs B':>8}")
        for s, (wasted, success, quarantines) in ref.items():
            print(f"{s:<22}{wasted:>16,}{success:>10.4f}{quarantines:>13}")
        for hold in HOLDS:
            wasted, success, quarantines = totals(
                [run("STRATEGY_C", sc, seed, hold) for sc in group for seed in SEEDS]
            )
            vs_a = (1 - wasted / ref["STRATEGY_A"][0]) * 100
            vs_b = (1 - wasted / ref["STRATEGY_B"][0]) * 100
            name = "C, hold off" if hold == 0 else f"C, hold {hold} s"
            print(f"{name:<22}{wasted:>16,}{success:>10.4f}{quarantines:>13}{vs_a:>7.1f}%{vs_b:>7.1f}%")

        print("\nPer held-out scenario, C wasted retries (seed 42): hold off -> 300 s"
              if group is held_out else "")
        if group is held_out:
            for sc in held_out:
                off = run("STRATEGY_C", sc, 42)["total_wasted_retries"]
                on = run("STRATEGY_C", sc, 42, 300)["total_wasted_retries"]
                mark = "" if off == on else "  <- changed"
                print(f"  #{sc.scenario_id:<3}{sc.name:<45}{off:>6} -> {on:<6}{mark}")


if __name__ == "__main__":
    main()
