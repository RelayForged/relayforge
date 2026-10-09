"""Unit tests for the Genetic Algorithm ERI Weight Optimizer (DEAP)."""

import pytest
from benchmark.genetic_optimizer import (
    individual_to_weights,
    evaluate_weights,
    run_genetic_optimizer,
)
from benchmark.scenarios import get_scenario
from relay.kernel.value_objects.eri import ERIWeights


def test_individual_to_weights_normalizes_to_one():
    """Verify arbitrary float individuals normalize to a valid ERIWeights summing to 1.0."""
    raw_genes = [0.5, 1.2, 0.3, 0.8, 0.1, 0.05, 0.2]
    weights = individual_to_weights(raw_genes)

    assert isinstance(weights, ERIWeights)
    total = sum(weights.model_dump().values())
    assert abs(total - 1.0) < 1e-5
    for k, v in weights.model_dump().items():
        assert 0.0 <= v <= 1.0


def test_evaluate_weights_structure():
    """Verify evaluate_weights returns penalty score and structured breakdown."""
    weights = ERIWeights.optimal()
    sc1 = get_scenario(1)
    penalty, metrics = evaluate_weights(weights, scenarios=[sc1])

    assert isinstance(penalty, float)
    assert penalty >= 0.0
    assert "total_retries" in metrics
    assert "false_quarantines" in metrics
    assert "excess_transitions" in metrics
    # Scenario 1 is intermittent timeouts; with optimal weights, false_quarantines should be 0
    assert metrics["false_quarantines"] == 0


def test_run_genetic_optimizer_execution():
    """Verify run_genetic_optimizer executes multiple generations and yields valid weights."""
    sc1 = get_scenario(1)
    best_weights, best_penalty, metrics = run_genetic_optimizer(
        generations=2,
        population_size=4,
        scenarios=[sc1],
        seed=123,
        verbose=False,
    )

    assert isinstance(best_weights, ERIWeights)
    assert isinstance(best_penalty, float)
    assert best_penalty >= 0.0
    assert metrics["total_retries"] >= 0
