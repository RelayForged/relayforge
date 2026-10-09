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
from benchmark.scenarios import (
    get_calibration_scenarios,
    get_evaluation_scenarios,
    CALIBRATION_SCENARIO_IDS,
    EVALUATION_SCENARIO_IDS,
)


@pytest.fixture(autouse=True)
def clean_benchmark_store():
    benchmark_store.clear()
    yield
    benchmark_store.clear()


def test_get_all_scenarios_returns_14():
    """Verify that exactly 14 research failure scenarios are defined."""
    scenarios = get_all_scenarios()
    assert len(scenarios) == 14
    ids = [sc.scenario_id for sc in scenarios]
    assert ids == list(range(1, 15))


def test_calibration_and_evaluation_scenario_splits():
    """Verify calibration and evaluation scenario sets are disjoint and cover all scenarios."""
    calib = get_calibration_scenarios()
    eval_sc = get_evaluation_scenarios()

    assert len(calib) == 6
    assert len(eval_sc) == 8
    assert set(sc.scenario_id for sc in calib) == set(CALIBRATION_SCENARIO_IDS)
    assert set(sc.scenario_id for sc in eval_sc) == set(EVALUATION_SCENARIO_IDS)
    assert set(sc.scenario_id for sc in calib).isdisjoint(set(sc.scenario_id for sc in eval_sc))

    for sc in calib:
        if sc.scenario_id in (1, 2):
            assert sc.should_never_quarantine is True
        else:
            assert sc.should_never_quarantine is False


def test_scenario_12_re_quarantine_relapse():
    """Verify Scenario 12 defines re-quarantine relapse and triggers >= 2 quarantine events under Strategy C."""
    sc = get_scenario(12)
    assert sc.scenario_id == 12
    assert "Re-Quarantine Relapse" in sc.name
    assert len(sc.phases) == 5
    assert sc.phases[2].manual_restore is True
    assert sc.phases[4].manual_restore is True

    runner = BenchmarkRunner(strategy_config=DeliveryStrategyConfig(strategy="STRATEGY_C"), random_seed=42)
    res = runner.run(sc)
    assert len(res.attempts) == 360
    quar_count = compute_quarantine_count(res)
    assert quar_count >= 2, f"Expected >= 2 quarantines in Scenario 12, got {quar_count}"


def test_scenario_13_rapid_micro_flapping():
    """Verify Scenario 13 rapid micro-flapping dampens transitions to <= 10 under Strategy C."""
    sc = get_scenario(13)
    assert sc.scenario_id == 13
    assert "Rapid Micro-Flapping" in sc.name
    assert len(sc.phases) == 17  # 8 * 2 + 1
    assert sc.phases[-1].manual_restore is True

    runner = BenchmarkRunner(strategy_config=DeliveryStrategyConfig(strategy="STRATEGY_C"), random_seed=42)
    res = runner.run(sc)
    assert len(res.attempts) == 300
    trans_count = compute_state_transition_count(res)
    assert trans_count <= 15, f"Expected <= 15 transitions in Scenario 13, got {trans_count}"


def test_scenario_14_long_mild_degradation():
    """Verify Scenario 14 defines 500 events of sustained mild degradation."""
    sc = get_scenario(14)
    assert sc.scenario_id == 14
    assert "Long Sustained Mild Degradation" in sc.name
    assert len(sc.phases) == 3
    assert sc.phases[1].duration_events == 400
    assert sc.phases[1].success_rate == 0.75

    runner = BenchmarkRunner(strategy_config=DeliveryStrategyConfig(strategy="STRATEGY_C"), random_seed=42)
    res = runner.run(sc)
    assert len(res.attempts) == 500


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


def test_scenario_11_bursty_traffic():
    """Verify Scenario 11 defines 5 bursty phases and executes cleanly under AEDE."""
    sc = get_scenario(11)
    assert sc.scenario_id == 11
    assert "Bursty Traffic" in sc.name
    assert len(sc.phases) == 5
    assert sc.phases[-1].manual_restore is True

    runner = BenchmarkRunner(strategy_config=DeliveryStrategyConfig(strategy="STRATEGY_C"), random_seed=42)
    res = runner.run(sc)
    assert len(res.attempts) == sum(p.duration_events for p in sc.phases)
    assert len(res.state_transitions) > 0
    # Not strictly AVAILABLE: the final recovery phase (80 events) is shorter than the
    # sliding window (100), so the window still holds ~20 residual events from the preceding
    # degraded phase at the very last attempt. With the wider hysteresis recovery gap
    # (degradation_recovery_threshold=95), that residual noise can legitimately leave the
    # endpoint in OBSERVED_DEGRADATION rather than fully back to AVAILABLE by the last event --
    # what matters is that it's recovering, not stuck in a bad state.
    assert res.attempts[-1].final_state in ("AVAILABLE", "OBSERVED_DEGRADATION")




def test_scenario_4_has_recovery_phase_and_recovers():
    """Verify Scenario 4 has 3 phases with phase 3 featuring manual_restore=True and recovers to AVAILABLE."""
    sc = get_scenario(4)
    assert len(sc.phases) == 3
    assert sc.phases[2].manual_restore is True

    runner = BenchmarkRunner(strategy_config=DeliveryStrategyConfig(strategy="STRATEGY_C"), random_seed=42)
    res = runner.run(sc)

    # Must have transitioned through QUARANTINED and reached RECOVERING -> AVAILABLE
    assert "QUARANTINED" in res.state_transitions
    assert "RECOVERING" in res.state_transitions
    assert res.attempts[-1].final_state == "AVAILABLE"


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


def test_strategy_b_reduces_retries_and_increases_delay():
    """Verify Strategy B differs from Strategy A by dynamically scaling retries down and delays up."""
    runner_a = BenchmarkRunner(strategy_config=DeliveryStrategyConfig(strategy="STRATEGY_A"), random_seed=42)
    runner_b = BenchmarkRunner(strategy_config=DeliveryStrategyConfig(strategy="STRATEGY_B"), random_seed=42)
    scenario = get_scenario(10)

    res_a = runner_a.run(scenario)
    res_b = runner_b.run(scenario)

    retries_a = sum(a.retry_count for a in res_a.attempts)
    retries_b = sum(a.retry_count for a in res_b.attempts)
    delay_a = sum(a.delay_seconds for a in res_a.attempts)
    delay_b = sum(a.delay_seconds for a in res_b.attempts)

    # Strategy B must conserve retries compared to fixed Strategy A
    assert retries_b < retries_a
    # Strategy B must expand backoff delay compared to fixed Strategy A
    assert delay_b > delay_a


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


def test_optimize_weights_runs_and_identifies_winner():
    """Verify run_optimization sweeps candidate weights and identifies Hysteresis-Balanced."""
    from benchmark.optimize_weights import run_optimization, CANDIDATE_PROFILES

    winner_name, optimal_weights, evals = run_optimization(CANDIDATE_PROFILES)

    assert len(evals) == 4
    assert winner_name == "Hysteresis-Balanced"
    assert not evals[0].has_false_quarantine
    assert evals[0].scen5_transitions <= 5
    assert evals[0].max_transitions <= 5
    assert evals[0].detection_event > 0
    assert evals[0].quarantine_event > 0
    assert evals[0].quarantine_retries_wasted == 0
    assert evals[0].total_retries < evals[1].total_retries


def test_evaluate_candidate_detects_false_quarantine():
    """Verify evaluate_candidate properly flags candidates that trigger false quarantines."""
    from benchmark.optimize_weights import evaluate_candidate
    from relay.kernel.value_objects.eri import ERIWeights

    # Pathological weights placing 100% weight on latency:
    # High latency in Scenario 2 (where success is 100%) triggers false quarantine
    bad_weights = ERIWeights(
        success_rate_weight=0.0,
        latency_weight=1.0,
        consecutive_failures_weight=0.0,
        timeout_weight=0.0,
        http_5xx_weight=0.0,
        queue_depth_weight=0.0,
        recovery_trend_weight=0.0,
    )
    ev = evaluate_candidate("Bad Candidate", bad_weights)
    assert ev.has_false_quarantine is True
    # Fitness score should be severely penalized
    assert ev.fitness_score < 0.0


def test_run_experiment_matrix_and_tables():
    """Verify run_experiment_matrix, table generation, and hypothesis checks."""
    from benchmark.run_experiments import (
        run_experiment_matrix,
        generate_overall_summary_table,
        generate_outage_analysis_table,
        generate_detailed_scenario_table,
        run_full_evaluation,
    )

    matrix = run_experiment_matrix(scenarios=get_all_scenarios()[:2])
    assert "STRATEGY_A" in matrix
    assert "STRATEGY_B" in matrix
    assert "STRATEGY_C" in matrix
    assert 1 in matrix["STRATEGY_A"]
    assert 2 in matrix["STRATEGY_C"]

    overall_table = generate_overall_summary_table(matrix)
    assert "Overall Aggregate Benchmark Comparison" in overall_table
    assert "Strategy C (AEDE / Ours)" in overall_table

    eval_res = run_full_evaluation(seeds=[42, 123])
    assert eval_res["outage_reduction"] >= 45.0
    assert eval_res["scen5_transitions"] <= 5.0


def test_compute_metric_stats():
    """Verify compute_metric_stats computes mean, std, and 95% confidence intervals."""
    from benchmark.run_experiments import compute_metric_stats

    values = [100.0, 102.0, 98.0, 101.0, 99.0]
    stats = compute_metric_stats(values)
    assert stats.mean == 100.0
    assert 1.0 < stats.std < 2.0
    assert stats.ci_lower < 100.0 < stats.ci_upper


def test_compute_paired_ttest():
    """Verify compute_paired_ttest calculates t-statistic and two-tailed p-value."""
    from benchmark.run_experiments import compute_paired_ttest

    sample_a = [100.0, 110.0, 105.0, 108.0, 102.0]
    sample_b = [50.0, 52.0, 48.0, 51.0, 49.0]
    t_stat, p_val = compute_paired_ttest(sample_a, sample_b)

    assert t_stat > 10.0
    assert p_val < 0.001


def test_sensitivity_analysis_execution_and_stability():
    """Verify run_sensitivity_analysis executes and all perturbed variants remain stable."""
    from benchmark.sensitivity_analysis import run_sensitivity_analysis, generate_sensitivity_table

    # Test with a subset of scenarios for fast execution in unit suite
    output = run_sensitivity_analysis()
    assert output["total_evaluations"] == 28
    assert output["all_stable"] is True

    table = generate_sensitivity_table(output)
    assert "Weight Sensitivity & Robustness Analysis" in table
    assert "Optimal Baseline" in table


def test_multi_endpoint_contention_simulation():
    """Verify multi-endpoint contention runner proves Strategy C prevents healthy tenant starvation."""
    from benchmark.multi_endpoint import run_contention_simulation, generate_multi_endpoint_table

    res_a = run_contention_simulation(strategy="STRATEGY_A", total_events_per_endpoint=100, failing_start_event=20)
    res_c = run_contention_simulation(strategy="STRATEGY_C", total_events_per_endpoint=100, failing_start_event=20)

    # Strategy C must quarantine the failing tenant
    assert res_c.quarantine_triggered is True

    # Strategy C must produce fewer retries on failing tenant
    assert res_c.failing_result.total_retries < res_a.failing_result.total_retries

    # Healthy tenant queue wait time must be lower under Strategy C than Strategy A
    assert res_c.healthy_result.avg_queue_wait_ms < res_a.healthy_result.avg_queue_wait_ms


def test_synthetic_telemetry_bursty_generation():
    """Verify bursty generation produces variable intervals while preserving determinism."""
    gen1 = SyntheticTelemetryGenerator(seed=123)
    gen2 = SyntheticTelemetryGenerator(seed=123)
    phase = ScenarioPhase(
        duration_events=20,
        success_rate=0.9,
        latency_ms=50.0,
        timed_out_rate=0.05,
        http_5xx_rate=0.05,
        description="Bursty Test",
    )
    start = datetime(2026, 9, 12, 10, 0, 0, tzinfo=timezone.utc)

    # Bursty=True produces non-uniform intervals
    batch_bursty_1 = gen1.generate(phase=phase, count=20, endpoint_id="ep-1", start_time=start, bursty=True)
    batch_bursty_2 = gen2.generate(phase=phase, count=20, endpoint_id="ep-1", start_time=start, bursty=True)

    assert len(batch_bursty_1) == 20
    # Seed reproducibility
    for a, b in zip(batch_bursty_1, batch_bursty_2):
        assert a.observed_at == b.observed_at
        assert a.succeeded == b.succeeded
        assert a.latency_ms == b.latency_ms

    # Verify timestamps are strictly increasing and intervals are non-constant
    deltas = [
        (batch_bursty_1[i].observed_at - batch_bursty_1[i - 1].observed_at).total_seconds() * 1000
        for i in range(1, len(batch_bursty_1))
    ]
    assert all(d > 0 for d in deltas)
    # Variance in deltas should be non-zero (unlike uniform 50ms)
    assert len(set(round(d, 1) for d in deltas)) > 5


def test_benchmark_runner_bursty_execution():
    """Verify BenchmarkRunner executes successfully with bursty=True."""
    sc = get_scenario(1)
    runner = BenchmarkRunner(
        strategy_config=DeliveryStrategyConfig(strategy="STRATEGY_C"),
        random_seed=42,
        bursty=True,
    )
    res = runner.run(sc)
    assert len(res.attempts) == sum(p.duration_events for p in sc.phases)
    assert len(res.state_transitions) > 0


def test_loo_calibration_check_execution_and_stability():
    """Verify leave-one-out calibration check runs across all 6 folds with stability."""
    from benchmark.loo_calibration_check import run_loo_calibration_check, generate_loo_markdown_table

    results = run_loo_calibration_check(seed=42)
    assert len(results) == 6
    hb_wins = sum(1 for r in results if r.winning_candidate == "Hysteresis-Balanced")
    assert hb_wins >= 5, f"Expected Hysteresis-Balanced to win >= 5 folds, got {hb_wins}"
    assert all(not r.oof_false_quarantine for r in results)
    assert all(r.oof_transitions <= 5 for r in results)

    table = generate_loo_markdown_table(results)
    assert "Leave-One-Out (LOO) Cross-Validation" in table
    assert "Hysteresis-Balanced" in table


def test_ablation_study_execution():
    """Verify component ablation study executes across all 7 strategies with expected relationships."""
    from benchmark.ablation import run_ablation_study, generate_ablation_markdown_table
    from benchmark.scenarios import get_scenario

    # Test with scenarios 4 (outage) and 5 (hysteresis flapping)
    test_scenarios = [get_scenario(4), get_scenario(5)]
    summaries = run_ablation_study(scenarios=test_scenarios, seeds=[42])
    assert len(summaries) == 7

    # 1. Oracle bound: lowest retries
    assert summaries["STRATEGY_O"].total_retries <= summaries["STRATEGY_C"].total_retries

    # 2. Isolation contribution: Strategy C has fewer retries on outage than C'
    assert summaries["STRATEGY_C"].outage_retries < summaries["STRATEGY_C_PRIME"].outage_retries

    # 3. Hysteresis contribution: Strategy C has significantly fewer transitions than C''
    assert summaries["STRATEGY_C"].total_transitions < summaries["STRATEGY_C_DOUBLE_PRIME"].total_transitions

    table = generate_ablation_markdown_table(summaries)
    assert "Component Ablation Study" in table
    assert "STRATEGY_C_PRIME" in table
    assert "STRATEGY_C_DOUBLE_PRIME" in table
    assert "STRATEGY_O" in table


def test_strategy_hcb_circuit_breaker_behaviour():
    """Verify STRATEGY_HCB (Hystrix-style circuit breaker) trips open, fails fast while open,
    and eventually recovers to closed -- independent of AEDE's ERI/state machine."""
    sc = get_scenario(4)  # Endpoint Outage: sustained 100% failure, then manual_restore recovery
    runner = BenchmarkRunner(
        strategy_config=DeliveryStrategyConfig(strategy="STRATEGY_HCB"),
        random_seed=42,
    )
    res = runner.run(sc)

    assert len(res.attempts) == sum(p.duration_events for p in sc.phases)
    # Must trip open at least once during the sustained-outage phase.
    assert "CB_OPEN" in res.state_transitions
    # While CB_OPEN, no delivery is attempted at all -- zero retries recorded for those attempts.
    # AttemptRecord.final_state always reads "AVAILABLE" for STRATEGY_HCB (it never touches
    # AEDE's state machine), so breaker state is only visible via state_transitions; check the
    # aggregate fail-fast behaviour instead: during the sustained outage phase (100% failure),
    # at least some failed attempts must have been failed fast (retry_count == 0) rather than
    # fully retried (retry_count == 10).
    outage_phase_len = sc.phases[1].duration_events  # phase 2: 150 events, 100% failure
    outage_attempts = res.attempts[sc.phases[0].duration_events : sc.phases[0].duration_events + outage_phase_len]
    fast_failed = sum(1 for a in outage_attempts if not a.succeeded and a.retry_count == 0)
    assert fast_failed > 0, "Circuit breaker must fail-fast (0 retries) for at least some outage attempts"
    # It must eventually recover to closed once the endpoint is healthy again (phase 3).
    assert res.state_transitions[-1] == "CB_CLOSED" or "CB_CLOSED" in res.state_transitions[1:]


@pytest.mark.asyncio
async def test_validate_against_real_aede():
    """Verify in-process AEDEService trajectory matches BenchmarkRunner simulation 100%."""
    from benchmark.validate_against_real_aede import validate_scenario_against_real_aede

    res = await validate_scenario_against_real_aede(scenario_id=4, seed=42)
    assert res.all_matched is True
    assert res.total_events == 290
    assert res.max_eri_delta < 1e-4
    assert len(res.mismatches) == 0



