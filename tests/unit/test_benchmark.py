"""Unit tests for the Benchmark Runner and Scenario definitions.

Validates in-process experiment execution, metrics calculation, and comparative
strategy evaluation without needing external network or Docker services.
"""
from datetime import datetime, timezone
import pytest

from benchmark import (
    AttemptRecord,
    BenchmarkResult,
    BenchmarkRunner,
    DeliveryStrategyConfig,
    ScenarioPhase,
    SyntheticTelemetryGenerator,
    compute_avg_retry_count,
    compute_delivery_success_rate,
    compute_quarantine_count,
    compute_recovery_time_events,
    compute_state_transition_count,
    get_all_scenarios,
    get_scenario,
    summarise,
    benchmark_store,
)


@pytest.fixture(autouse=True)
def clean_benchmark_store():
    benchmark_store.clear()
    yield
    benchmark_store.clear()


def test_get_all_scenarios_returns_10():
    """Verify that exactly 10 research failure scenarios are defined."""
    scenarios = get_all_scenarios()
    assert len(scenarios) == 10
    ids = [sc.scenario_id for sc in scenarios]
    assert ids == list(range(1, 11))


def test_get_scenario_valid():
    """Verify retrieving a scenario by ID returns the correct Scenario object."""
    sc = get_scenario(10)
    assert sc.scenario_id == 10
    assert "Slow Degradation" in sc.name
    assert len(sc.phases) > 0


def test_get_scenario_invalid():
    """Verify retrieving an invalid scenario ID raises ValueError."""
    with pytest.raises(ValueError) as exc_info:
        get_scenario(99)
    assert "not found" in str(exc_info.value)


def test_scenario_10_has_multiple_phases():
    """Verify Scenario 10 defines a gradual stepped degradation sequence."""
    sc = get_scenario(10)
    assert len(sc.phases) >= 5
    # Success rate should step down monotonically during degradation phases
    rates = [p.success_rate for p in sc.phases[:5]]
    assert rates == [1.0, 0.85, 0.60, 0.35, 0.0]


def test_synthetic_telemetry_success_rate():
    """Verify SyntheticTelemetryGenerator produces telemetry matching phase rates."""
    gen = SyntheticTelemetryGenerator(seed=42)
    phase = ScenarioPhase(
        duration_events=1000,
        success_rate=0.75,
        latency_ms=100.0,
        timed_out_rate=0.10,
        http_5xx_rate=0.15,
        description="Test 75% success",
    )
    items = gen.generate(phase, 1000, "test-ep", datetime.now(timezone.utc))
    assert len(items) == 1000
    successes = sum(1 for it in items if it.succeeded)
    # 75% rate over 1000 events should be within [700, 800]
    assert 700 <= successes <= 800


def test_synthetic_telemetry_reproducible():
    """Verify identical random seeds produce identical telemetry sequences."""
    gen1 = SyntheticTelemetryGenerator(seed=12345)
    gen2 = SyntheticTelemetryGenerator(seed=12345)
    phase = ScenarioPhase(
        duration_events=50,
        success_rate=0.5,
        latency_ms=200.0,
        timed_out_rate=0.2,
        http_5xx_rate=0.3,
        description="Reproducibility test",
    )
    now = datetime(2026, 8, 25, 12, 0, 0, tzinfo=timezone.utc)
    batch1 = gen1.generate(phase, 50, "ep", now)
    batch2 = gen2.generate(phase, 50, "ep", now)

    for it1, it2 in zip(batch1, batch2):
        assert it1.succeeded == it2.succeeded
        assert it1.timed_out == it2.timed_out
        assert it1.http_status == it2.http_status
        assert it1.latency_ms == it2.latency_ms


def test_runner_strategy_a_scenario_3():
    """Verify running Strategy A on Burst Failures records attempts and fixed retries."""
    runner = BenchmarkRunner(
        strategy_config=DeliveryStrategyConfig(strategy="STRATEGY_A"),
        random_seed=42,
    )
    scenario = get_scenario(3)
    result = runner.run(scenario)

    assert result.scenario_id == 3
    assert result.strategy == "STRATEGY_A"
    assert len(result.attempts) == 190  # 50 + 60 + 80
    assert result.finished_at is not None
    # Strategy A should maintain fixed retries on failed items
    failed_attempts = [a for a in result.attempts if not a.succeeded]
    assert len(failed_attempts) > 0
    assert all(a.retry_count == 10 for a in failed_attempts)


def test_runner_strategy_c_quarantines_on_scenario_4():
    """Verify Strategy C transitions into QUARANTINED on prolonged Endpoint Outage."""
    runner = BenchmarkRunner(
        strategy_config=DeliveryStrategyConfig(strategy="STRATEGY_C"),
        random_seed=42,
    )
    scenario = get_scenario(4)  # 40 nominal + 150 outage
    result = runner.run(scenario)

    assert "QUARANTINED" in result.state_transitions
    # When quarantined, retries should drop to 0
    quarantined_attempts = [a for a in result.attempts if a.final_state == "QUARANTINED"]
    assert len(quarantined_attempts) > 0
    assert all(a.retry_count == 0 for a in quarantined_attempts)


def test_runner_strategy_c_fewer_retries_than_a():
    """Primary Research Claim Validation:
    AEDE (Strategy C) uses significantly fewer retries than Static Retry (Strategy A)
    under Slow Degradation (Scenario 10).
    """
    runner_a = BenchmarkRunner(
        strategy_config=DeliveryStrategyConfig(strategy="STRATEGY_A"),
        random_seed=42,
    )
    runner_c = BenchmarkRunner(
        strategy_config=DeliveryStrategyConfig(strategy="STRATEGY_C"),
        random_seed=42,
    )
    scenario = get_scenario(10)

    result_a = runner_a.run(scenario)
    result_c = runner_c.run(scenario)

    avg_retries_a = compute_avg_retry_count(result_a)
    avg_retries_c = compute_avg_retry_count(result_c)

    # Strategy C must waste significantly fewer retries by isolating degraded/quarantined states
    assert avg_retries_c < avg_retries_a
    total_retries_a = sum(a.retry_count for a in result_a.attempts)
    total_retries_c = sum(a.retry_count for a in result_c.attempts)
    assert total_retries_c < total_retries_a


def test_metrics_delivery_success_rate():
    """Verify compute_delivery_success_rate correctly aggregates success ratio."""
    result = BenchmarkResult(
        scenario_id=1,
        scenario_name="Test",
        strategy="STRATEGY_A",
        attempts=[
            AttemptRecord(succeeded=True, retry_count=0, latency_ms=10.0, timed_out=False, final_state="AVAILABLE", eri_score=100.0),
            AttemptRecord(succeeded=True, retry_count=0, latency_ms=10.0, timed_out=False, final_state="AVAILABLE", eri_score=100.0),
            AttemptRecord(succeeded=False, retry_count=3, latency_ms=50.0, timed_out=False, final_state="AVAILABLE", eri_score=70.0),
            AttemptRecord(succeeded=True, retry_count=0, latency_ms=10.0, timed_out=False, final_state="AVAILABLE", eri_score=80.0),
        ],
    )
    rate = compute_delivery_success_rate(result)
    assert rate == 0.75


def test_metrics_state_transition_count():
    """Verify compute_state_transition_count and compute_quarantine_count."""
    result = BenchmarkResult(
        scenario_id=1,
        scenario_name="Test",
        strategy="STRATEGY_C",
        state_transitions=["AVAILABLE", "OBSERVED_DEGRADATION", "UNSTABLE", "QUARANTINED", "RECOVERING", "AVAILABLE"],
    )
    assert compute_state_transition_count(result) == 5
    assert compute_quarantine_count(result) == 1

    summary = summarise(result)
    assert summary["state_transitions"] == 5
    assert summary["quarantine_count"] == 1


def test_benchmark_result_stored_in_chart_store():
    """Verify BenchmarkRunner automatically populates the Chart Data API benchmark_store."""
    assert len(benchmark_store.get_runs()) == 0

    runner = BenchmarkRunner(
        strategy_config=DeliveryStrategyConfig(strategy="STRATEGY_C"),
        random_seed=42,
    )
    scenario = get_scenario(1)
    runner.run(scenario)

    runs = benchmark_store.get_runs()
    assert len(runs) == 1
    assert runs[0].scenario == "Scenario 1: Intermittent Timeouts"
    assert runs[0].strategy == "STRATEGY_C"
    assert runs[0].total_events > 0
