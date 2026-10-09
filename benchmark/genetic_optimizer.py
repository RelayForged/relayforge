"""Automated ERI Weight Discovery via Genetic Algorithm (DEAP).

Uses evolutionary algorithms to discover optimal ERI weights across benchmark scenarios.
Explicitly chosen over Bayesian Optimization to handle non-smooth, discontinuous penalty
cliffs (+1000 for false quarantines, +500 for state flapping).

Usage:
    python benchmark/genetic_optimizer.py --generations 10 --population 20 --verbose
"""

import argparse
from functools import partial
from pathlib import Path
import random
import sys
from typing import Any, Dict, List, Optional, Tuple

# Ensure repository root is on sys.path
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from deap import base, creator, tools
from benchmark.metrics import BenchmarkResult, compute_recovery_time_events
from benchmark.runner import BenchmarkRunner, DeliveryStrategyConfig
from benchmark.scenarios import (
    Scenario,
    get_all_scenarios,
    get_calibration_scenarios,
    get_scenario,
)
from relay.kernel.value_objects.endpoint_state import EndpointState
from relay.kernel.value_objects.eri import ERIWeights


# Initialize DEAP creators safely (guard against repeated creation in test environments)
if not hasattr(creator, "FitnessMin"):
    creator.create("FitnessMin", base.Fitness, weights=(-1.0,))
if not hasattr(creator, "Individual"):
    creator.create("Individual", list, fitness=creator.FitnessMin)


RECOVERY_TREND_WEIGHT_CAP = 0.15
"""
Cap on recovery_trend_weight specifically -- not a generic cap on all 7 dimensions.

recovery_trend_score (relay/control_plane/eri/calculator.py) is structurally different from
the other 6 ERI sub-scores: it is not a smooth function of the underlying telemetry. It only
takes three discrete values (1.0 / 0.5 / 0.0), produced by comparing the success rate of the
first half of the sliding window against the second half. Every unconstrained GA run
converged to recovery_trend_weight > 0.55 -- an excellent fit for the calibration set, but a
real production liability: over a long window sitting near a borderline (not catastrophic)
success rate, this comparison flips on small fluctuations, and at >55% of the composite
weight that flip directly swings the ERI score enough to cause persistent state-machine
flapping (observed on the held-out long-duration Scenario 14 -- 30-57 transitions vs ~7-21
under weights that don't over-rely on this signal). Capping this one dimension lets the
search still use the signal (it is a real, useful one, just noisy in isolation) without
letting it dominate the composite score.
"""


def individual_to_weights(individual: List[float]) -> ERIWeights:
    """Normalize 7 genome values to a valid, immutable ERIWeights instance summing to 1.0,
    with recovery_trend_weight capped at RECOVERY_TREND_WEIGHT_CAP."""
    raw = [max(0.005, float(x)) for x in individual[:7]]
    total = sum(raw)
    norm = [r / total for r in raw]

    if norm[6] > RECOVERY_TREND_WEIGHT_CAP:
        excess = norm[6] - RECOVERY_TREND_WEIGHT_CAP
        norm[6] = RECOVERY_TREND_WEIGHT_CAP
        other_idx = list(range(6))
        other_total = sum(norm[i] for i in other_idx)
        if other_total > 0:
            for i in other_idx:
                norm[i] += excess * (norm[i] / other_total)

    w = [round(x, 6) for x in norm]
    w[-1] = round(1.0 - sum(w[:-1]), 6)
    return ERIWeights(
        success_rate_weight=w[0],
        latency_weight=w[1],
        consecutive_failures_weight=w[2],
        timeout_weight=w[3],
        http_5xx_weight=w[4],
        queue_depth_weight=w[5],
        recovery_trend_weight=w[6],
    )


def evaluate_weights(
    weights: ERIWeights,
    scenarios: Optional[List[Scenario]] = None,
    seed: int = 42,
) -> Tuple[float, Dict[str, Any]]:
    """Evaluate ERIWeights across scenarios, calculating penalty score and metrics."""
    if scenarios is None:
        scenarios = get_calibration_scenarios()

    runner = BenchmarkRunner(
        strategy_config=DeliveryStrategyConfig(strategy="STRATEGY_C"),
        eri_weights=weights,
        random_seed=seed,
    )

    total_retries = 0
    false_quarantines = 0
    excess_transitions = 0
    never_recovers = 0
    mttr_values: List[int] = []

    for sc in scenarios:
        res = runner.run(sc)
        for att in res.attempts:
            total_retries += att.retry_count

        # False quarantine check driven by should_never_quarantine
        if sc.should_never_quarantine and EndpointState.QUARANTINED.value in res.state_transitions:
            false_quarantines += 1

        # Flapping check: more than 5 transitions in a scenario
        transitions = max(0, len(res.state_transitions) - 1)
        if transitions > 5:
            excess_transitions += (transitions - 5)

        # Recovery completeness & speed: a scenario that reaches QUARANTINED and has a
        # manual_restore phase is expected to fully recover to AVAILABLE by the end of the run.
        # See the matching check in benchmark/optimize_weights.py for why this exists.
        if EndpointState.QUARANTINED.value in res.state_transitions and any(p.manual_restore for p in sc.phases):
            if res.attempts[-1].final_state != EndpointState.AVAILABLE.value:
                never_recovers += 1
            mttr_values.append(compute_recovery_time_events(res))

    mttr = max(mttr_values) if mttr_values else 0
    penalty = (
        float(total_retries)
        + 1000.0 * false_quarantines
        + 500.0 * excess_transitions
        + 1000.0 * never_recovers
        + 2.0 * mttr
    )
    metrics = {
        "total_retries": total_retries,
        "false_quarantines": false_quarantines,
        "excess_transitions": excess_transitions,
        "never_recovers": never_recovers,
        "mttr": mttr,
        "penalty": penalty,
    }
    return penalty, metrics


def _eval_individual(individual: List[float], scenarios: Optional[List[Scenario]] = None) -> Tuple[float]:
    """DEAP evaluation wrapper returning a 1-tuple of fitness."""
    try:
        weights = individual_to_weights(individual)
        penalty, _ = evaluate_weights(weights, scenarios=scenarios)
        return (penalty,)
    except Exception:
        return (100000.0,)


def check_bounds(min_val: float = 0.005, max_val: float = 1.0):
    """Decorator to enforce gene value bounds after crossover or mutation."""
    def decorator(func):
        def wrapper(*args, **kwargs):
            offspring = func(*args, **kwargs)
            for child in offspring:
                for i in range(len(child)):
                    if child[i] < min_val:
                        child[i] = min_val
                    elif child[i] > max_val:
                        child[i] = max_val
            return offspring
        return wrapper
    return decorator


def setup_toolbox(scenarios: Optional[List[Scenario]] = None) -> base.Toolbox:
    """Configure DEAP toolbox with genes, operators, and evaluation function."""
    if scenarios is None:
        scenarios = get_calibration_scenarios()

    toolbox = base.Toolbox()
    toolbox.register("attr_float", random.uniform, 0.01, 1.0)
    toolbox.register("individual", tools.initRepeat, creator.Individual, toolbox.attr_float, n=7)
    toolbox.register("population", tools.initRepeat, list, toolbox.individual)

    toolbox.register("evaluate", partial(_eval_individual, scenarios=scenarios))
    toolbox.register("mate", tools.cxBlend, alpha=0.5)
    toolbox.register("mutate", tools.mutGaussian, mu=0.0, sigma=0.1, indpb=0.2)
    toolbox.register("select", tools.selTournament, tournsize=3)

    toolbox.decorate("mate", check_bounds())
    toolbox.decorate("mutate", check_bounds())
    return toolbox


def run_genetic_optimizer(
    generations: int = 10,
    population_size: int = 20,
    crossover_prob: float = 0.7,
    mutation_prob: float = 0.2,
    scenarios: Optional[List[Scenario]] = None,
    seed: int = 42,
    verbose: bool = False,
) -> Tuple[ERIWeights, float, Dict[str, Any]]:
    """Execute GA optimization run and return (best_weights, best_penalty, metrics)."""
    if scenarios is None:
        scenarios = get_calibration_scenarios()

    random.seed(seed)
    toolbox = setup_toolbox(scenarios=scenarios)
    pop = toolbox.population(n=population_size)

    # Seed first individual with the current optimal baseline weights
    baseline = ERIWeights.optimal()
    pop[0] = creator.Individual([
        baseline.success_rate_weight,
        baseline.latency_weight,
        baseline.consecutive_failures_weight,
        baseline.timeout_weight,
        baseline.http_5xx_weight,
        baseline.queue_depth_weight,
        baseline.recovery_trend_weight,
    ])

    # Evaluate initial population
    fitnesses = list(map(toolbox.evaluate, pop))
    for ind, fit in zip(pop, fitnesses):
        ind.fitness.values = fit

    hof = tools.HallOfFame(1)
    hof.update(pop)

    if verbose:
        print(f"Gen   0: Best Penalty = {hof[0].fitness.values[0]:.2f}")

    for gen in range(1, generations + 1):
        offspring = toolbox.select(pop, len(pop))
        offspring = list(map(toolbox.clone, offspring))

        for child1, child2 in zip(offspring[::2], offspring[1::2]):
            if random.random() < crossover_prob:
                toolbox.mate(child1, child2)
                del child1.fitness.values
                del child2.fitness.values

        for mutant in offspring:
            if random.random() < mutation_prob:
                toolbox.mutate(mutant)
                del mutant.fitness.values

        invalid_ind = [ind for ind in offspring if not ind.fitness.valid]
        fitnesses = list(map(toolbox.evaluate, invalid_ind))
        for ind, fit in zip(invalid_ind, fitnesses):
            ind.fitness.values = fit

        pop[:] = offspring
        hof.update(pop)

        if verbose:
            print(f"Gen {gen:3d}: Best Penalty = {hof[0].fitness.values[0]:.2f}")

    best_ind = hof[0]
    best_weights = individual_to_weights(best_ind)
    best_penalty, metrics = evaluate_weights(best_weights, scenarios=scenarios, seed=seed)
    return best_weights, best_penalty, metrics


def main() -> None:
    parser = argparse.ArgumentParser(description="Automated ERI Weight Discovery via Genetic Algorithm (DEAP)")
    parser.add_argument("--generations", type=int, default=10, help="Number of GA generations")
    parser.add_argument("--population", type=int, default=20, help="Population size")
    parser.add_argument("--seed", type=int, default=42, help="Random seed")
    parser.add_argument("--scenarios", type=str, default=None, help="Comma-separated scenario IDs (e.g. '1,2,4,5,10')")
    parser.add_argument("--verbose", action="store_true", help="Print generation progress")
    args = parser.parse_args()

    scenarios = None
    if args.scenarios:
        ids = [int(s.strip()) for s in args.scenarios.split(",")]
        scenarios = [get_scenario(i) for i in ids]

    print("=" * 60)
    print("Relay ERI Weight Genetic Optimization (DEAP)")
    print(f"Generations: {args.generations} | Population: {args.population} | Seed: {args.seed}")
    print("=" * 60)

    best_weights, best_penalty, metrics = run_genetic_optimizer(
        generations=args.generations,
        population_size=args.population,
        scenarios=scenarios,
        seed=args.seed,
        verbose=args.verbose,
    )

    print("\nOptimization Complete!")
    print(f"Best Penalty Score: {best_penalty:.2f}")
    print(f"  Total Retries:       {metrics['total_retries']}")
    print(f"  False Quarantines:   {metrics['false_quarantines']}")
    print(f"  Excess Transitions:  {metrics['excess_transitions']}")
    print(f"  Never Recovers:      {metrics['never_recovers']}")
    print(f"  MTTR (worst, events): {metrics['mttr']}")
    print("\nDiscovered ERIWeights:")
    for k, v in best_weights.model_dump().items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
