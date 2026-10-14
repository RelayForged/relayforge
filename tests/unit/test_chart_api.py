"""Unit tests for the Chart Data API.

Validates read-only dashboard endpoints using FastAPI TestClient and in-memory stores.
"""
from datetime import datetime, timezone
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from relay.api.chart import BenchmarkRun, chart_router, state_store, benchmark_store
from relay.control_plane.explainability.record import (
    ExplainabilityRecord,
    MetricSnapshot,
    ThresholdSnapshot,
)
from relay.kernel.entities.policy_evaluation import PolicyEvaluation
from relay.kernel.policies.delivery_policy import (
    AlertLevel,
    BackoffStrategy,
    ConcurrencyPolicy,
    DeliveryPolicy,
    IntegrityPolicy,
    IsolationAction,
    IsolationPolicy,
    NotificationPolicy,
    ReplayPolicy,
    RetryPolicy,
)
from relay.kernel.value_objects.endpoint_state import EndpointState
from relay.kernel.value_objects.eri import ERIScore


@pytest.fixture(autouse=True)
def clean_stores():
    """Reset the in-memory stores before each test."""
    state_store.clear()
    benchmark_store.clear()
    yield
    state_store.clear()
    benchmark_store.clear()


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(chart_router)
    return TestClient(app)


def _populate_sample_data(endpoint_id: str = "ep-test-1", score: float = 85.0, state: EndpointState = EndpointState.AVAILABLE):
    now = datetime.now(timezone.utc)
    eri = ERIScore(
        score=score,
        success_rate_score=0.9,
        latency_score=0.8,
        consecutive_failure_score=1.0,
        timeout_score=1.0,
        http_5xx_score=1.0,
        queue_depth_score=1.0,
        recovery_trend_score=0.5,
        computed_at=now,
        window_size=20,
    )
    policy = DeliveryPolicy(
        policy_id="pol-uuid-1",
        endpoint_id=endpoint_id,
        endpoint_state=state.value,
        eri_score=score,
        computed_at=now,
        retry_policy=RetryPolicy(
            initial_delay_seconds=1.0,
            max_delay_seconds=30.0,
            max_retries=5,
            backoff_strategy=BackoffStrategy.EXPONENTIAL,
        ),
        concurrency_policy=ConcurrencyPolicy(max_parallel=5, queue_priority=5, throughput_limit=100),
        isolation_policy=IsolationPolicy(action=IsolationAction.CONTINUE),
        replay_policy=ReplayPolicy(allowed=True, batch_size=50, speed=1.0),
        notification_policy=NotificationPolicy(notify_admin=False, alert_level=AlertLevel.INFO, suppress=False),
        integrity_policy=IntegrityPolicy(hmac_required=False, signature_ttl=300, replay_protection=True),
    )
    evaluation = PolicyEvaluation(
        evaluation_id="eval-uuid-1",
        endpoint_id=endpoint_id,
        policy_id="pol-uuid-1",
        explainability_record_id="rec-uuid-1",
        eri_score=score,
        state_before="AVAILABLE",
        state_after=state.value,
        state_changed=False,
        strategy_used="STRATEGY_C",
        is_fallback=False,
        fallback_tier=1,
        evaluated_at=now,
        window_size=20,
    )
    explainability = ExplainabilityRecord(
        record_id="rec-uuid-1",
        policy_id="pol-uuid-1",
        endpoint_id=endpoint_id,
        state_before="AVAILABLE",
        state_after=state.value,
        state_changed=False,
        metrics=MetricSnapshot(
            success_rate_score=0.9,
            latency_score=0.8,
            consecutive_failure_score=1.0,
            timeout_score=1.0,
            http_5xx_score=1.0,
            queue_depth_score=1.0,
            recovery_trend_score=0.5,
            composite_eri=score,
        ),
        thresholds=ThresholdSnapshot(
            degradation_threshold=80.0,
            degradation_recovery_threshold=90.0,
            unstable_threshold=60.0,
            unstable_recovery_threshold=75.0,
            quarantine_threshold=30.0,
            recovery_threshold=90.0,
        ),
        reason="Endpoint operating within nominal parameters.",
        triggered_thresholds=[],
        policy_changes=[],
        strategy_used="STRATEGY_C",
        evaluated_at=now,
        window_size=20,
    )
    state_store.record_evaluation(
        endpoint_id=endpoint_id,
        state=state,
        eri_score=eri,
        policy_evaluation=evaluation,
        delivery_policy=policy,
        explainability_record=explainability,
    )
    return now


def test_list_endpoints_empty(client):
    res = client.get("/api/chart/endpoints")
    assert res.status_code == 200
    assert res.json() == {"endpoints": []}


def test_list_endpoints_with_data(client):
    _populate_sample_data("ep-1", score=92.0, state=EndpointState.AVAILABLE)
    _populate_sample_data("ep-2", score=55.0, state=EndpointState.UNSTABLE)

    res = client.get("/api/chart/endpoints")
    assert res.status_code == 200
    data = res.json()["endpoints"]
    assert len(data) == 2
    ep_map = {item["endpoint_id"]: item for item in data}
    assert ep_map["ep-1"]["state"] == "AVAILABLE"
    assert ep_map["ep-1"]["eri_score"] == 92.0
    assert ep_map["ep-2"]["state"] == "UNSTABLE"
    assert ep_map["ep-2"]["eri_score"] == 55.0


def test_get_endpoint_state(client):
    _populate_sample_data("ep-state-test", score=75.0, state=EndpointState.OBSERVED_DEGRADATION)
    res = client.get("/api/chart/endpoints/ep-state-test/state")
    assert res.status_code == 200
    assert res.json()["endpoint_id"] == "ep-state-test"
    assert res.json()["state"] == "OBSERVED_DEGRADATION"
    assert res.json()["last_evaluated_at"] is not None


def test_get_endpoint_state_not_found(client):
    res = client.get("/api/chart/endpoints/nonexistent/state")
    assert res.status_code == 404
    assert "has not been evaluated yet" in res.json()["detail"]


def test_eri_history_returns_points(client):
    _populate_sample_data("ep-history", score=80.0)
    _populate_sample_data("ep-history", score=70.0, state=EndpointState.OBSERVED_DEGRADATION)

    res = client.get("/api/chart/endpoints/ep-history/eri-history")
    assert res.status_code == 200
    data = res.json()
    assert data["endpoint_id"] == "ep-history"
    assert len(data["points"]) == 2
    assert data["points"][0]["score"] == 80.0
    assert data["points"][1]["score"] == 70.0
    assert "success_rate" in data["points"][0]["sub_scores"]


def test_eri_history_limit(client):
    for i in range(10):
        _populate_sample_data("ep-limit", score=float(50 + i))

    res = client.get("/api/chart/endpoints/ep-limit/eri-history?limit=3")
    assert res.status_code == 200
    assert len(res.json()["points"]) == 3
    assert res.json()["points"][-1]["score"] == 59.0


def test_eri_history_empty(client):
    res = client.get("/api/chart/endpoints/ep-none/eri-history")
    assert res.status_code == 200
    assert res.json() == {"endpoint_id": "ep-none", "points": []}


def test_get_latest_policy(client):
    _populate_sample_data("ep-policy-test", score=88.0)
    res = client.get("/api/chart/endpoints/ep-policy-test/policy")
    assert res.status_code == 200
    data = res.json()
    assert data["endpoint_id"] == "ep-policy-test"
    assert data["policy_id"] == "pol-uuid-1"
    assert data["strategy_used"] == "STRATEGY_C"
    assert data["retry_policy"]["max_retries"] == 5
    assert data["concurrency_policy"]["max_parallel"] == 5
    assert data["isolation_action"] == "CONTINUE"


def test_get_latest_policy_not_found(client):
    res = client.get("/api/chart/endpoints/nonexistent/policy")
    assert res.status_code == 404
    assert "No policy evaluations found" in res.json()["detail"]


def test_explainability_records(client):
    _populate_sample_data("ep-exp-test", score=85.0)
    res = client.get("/api/chart/endpoints/ep-exp-test/explainability")
    assert res.status_code == 200
    data = res.json()
    assert data["endpoint_id"] == "ep-exp-test"
    assert len(data["records"]) == 1
    rec = data["records"][0]
    assert rec["record_id"] == "rec-uuid-1"
    assert rec["reason"] == "Endpoint operating within nominal parameters."
    assert rec["metrics"]["composite_eri"] == 85.0
    assert rec["thresholds"]["degradation_threshold"] == 80.0


def test_explainability_empty(client):
    res = client.get("/api/chart/endpoints/ep-no-exp/explainability")
    assert res.status_code == 200
    assert res.json() == {"endpoint_id": "ep-no-exp", "records": []}


def test_benchmark_results_empty(client):
    res = client.get("/api/chart/benchmark/results")
    assert res.status_code == 200
    assert res.json() == {"runs": []}


def test_benchmark_results_with_data(client):
    run = BenchmarkRun(
        run_id="run-1",
        scenario="Scenario 10: Slow Degradation",
        strategy="STRATEGY_C",
        total_events=5000,
        successful_deliveries=4800,
        failed_deliveries=200,
        avg_retry_count=1.2,
        avg_recovery_time_seconds=14.5,
        started_at="2026-08-24T12:00:00Z",
        finished_at="2026-08-24T12:05:00Z",
    )
    benchmark_store.add_run(run)

    res = client.get("/api/chart/benchmark/results")
    assert res.status_code == 200
    runs = res.json()["runs"]
    assert len(runs) == 1
    assert runs[0]["scenario"] == "Scenario 10: Slow Degradation"
    assert runs[0]["strategy"] == "STRATEGY_C"
    assert runs[0]["total_events"] == 5000
    assert runs[0]["avg_recovery_time_seconds"] == 14.5


@pytest.mark.asyncio
async def test_aede_service_publishes_policy_evaluation():
    import json
    from relay.control_plane.aede.service import AEDEService
    from relay.kernel.value_objects.telemetry import Telemetry
    from relay.infrastructure.messaging.admin import EVENTS_INTERNAL_POLICY_TOPIC

    published_messages = []

    async def mock_publisher(topic: str, key: str, value: bytes):
        published_messages.append((topic, key, value))

    service = AEDEService(publisher=mock_publisher)
    obs = Telemetry(
        attempt_id="att-kafka-1",
        endpoint_id="ep-kafka-test",
        succeeded=True,
        http_status=200,
        latency_ms=45.0,
        timed_out=False,
        observed_at=datetime.now(timezone.utc),
    )

    eri, state, policy, record = await service.record_telemetry(obs)

    assert len(published_messages) == 1
    topic, key, payload_bytes = published_messages[0]
    assert topic == EVENTS_INTERNAL_POLICY_TOPIC
    assert key == "ep-kafka-test"

    data = json.loads(payload_bytes.decode("utf-8"))
    assert data["endpoint_id"] == "ep-kafka-test"
    assert data["state"] == state.value
    assert "eri_score" in data
    assert "policy_evaluation" in data
    assert "delivery_policy" in data
    assert "explainability_record" in data
    assert data["policy_evaluation"]["strategy_used"] == "STRATEGY_C"
    assert data["policy_evaluation"]["policy_id"] == policy.policy_id
    assert data["policy_evaluation"]["explainability_record_id"] == record.record_id


@pytest.mark.asyncio
async def test_aede_service_publish_failure_is_resilient():
    from relay.control_plane.aede.service import AEDEService
    from relay.kernel.value_objects.telemetry import Telemetry

    async def failing_publisher(topic: str, key: str, value: bytes):
        raise ConnectionError("Kafka cluster offline")

    service = AEDEService(publisher=failing_publisher)
    obs = Telemetry(
        attempt_id="att-fail-1",
        endpoint_id="ep-fail-test",
        succeeded=True,
        http_status=200,
        latency_ms=60.0,
        timed_out=False,
        observed_at=datetime.now(timezone.utc),
    )

    # Must complete cleanly without raising exception
    eri, state, policy, record = await service.record_telemetry(obs)
    assert eri is not None
    assert state == EndpointState.AVAILABLE


def test_dashboard_ingests_policy_evaluation_into_state_store(client):
    import json
    from relay.kernel.value_objects.eri import ERIScore
    from relay.kernel.entities.policy_evaluation import PolicyEvaluation
    from relay.control_plane.explainability.record import ExplainabilityRecord

    endpoint_id = "ep-ingest-test"
    now = datetime.now(timezone.utc)
    eri = ERIScore(
        score=78.5,
        success_rate_score=0.8,
        latency_score=0.7,
        consecutive_failure_score=1.0,
        timeout_score=1.0,
        http_5xx_score=1.0,
        queue_depth_score=1.0,
        recovery_trend_score=0.5,
        computed_at=now,
        window_size=25,
    )
    from relay.control_plane.aede.synthesizer import synthesize_policy
    from relay.kernel.value_objects.endpoint_state import StateMachineThresholds

    policy, rec = synthesize_policy(
        endpoint_id=endpoint_id,
        state_before=EndpointState.AVAILABLE,
        state_after=EndpointState.OBSERVED_DEGRADATION,
        eri=eri,
        thresholds=StateMachineThresholds(
            degradation_threshold=80.0,
            degradation_recovery_threshold=90.0,
            unstable_threshold=60.0,
            unstable_recovery_threshold=70.0,
            quarantine_threshold=30.0,
            recovery_threshold=90.0,
        ),
        computed_at=now,
    )
    eval_ent = PolicyEvaluation(
        evaluation_id="eval-ingest-1",
        endpoint_id=endpoint_id,
        policy_id=policy.policy_id,
        explainability_record_id=rec.record_id,
        eri_score=78.5,
        state_before="AVAILABLE",
        state_after="OBSERVED_DEGRADATION",
        state_changed=True,
        strategy_used="STRATEGY_C",
        evaluated_at=now,
        window_size=25,
    )

    bundle = {
        "endpoint_id": endpoint_id,
        "state": "OBSERVED_DEGRADATION",
        "eri_score": eri.model_dump(mode="json"),
        "policy_evaluation": eval_ent.model_dump(mode="json"),
        "delivery_policy": policy.model_dump(mode="json"),
        "explainability_record": rec.model_dump(mode="json"),
    }

    # Ingest directly as dashboard consumer does
    state_store.record_evaluation(
        endpoint_id=bundle["endpoint_id"],
        state=EndpointState(bundle["state"]),
        eri_score=ERIScore.model_validate(bundle["eri_score"]),
        policy_evaluation=PolicyEvaluation.model_validate(bundle["policy_evaluation"]),
        delivery_policy=DeliveryPolicy.model_validate(bundle["delivery_policy"]),
        explainability_record=ExplainabilityRecord.model_validate(bundle["explainability_record"]),
    )

    # Verify chart endpoints expose this state accurately
    res_state = client.get(f"/api/chart/endpoints/{endpoint_id}/state")
    assert res_state.status_code == 200
    assert res_state.json()["state"] == "OBSERVED_DEGRADATION"

    res_eri = client.get(f"/api/chart/endpoints/{endpoint_id}/eri-history")
    assert res_eri.status_code == 200
    assert len(res_eri.json()["points"]) == 1
    assert res_eri.json()["points"][0]["score"] == 78.5

    res_pol = client.get(f"/api/chart/endpoints/{endpoint_id}/policy")
    assert res_pol.status_code == 200
    assert res_pol.json()["policy_id"] == policy.policy_id
    assert res_pol.json()["strategy_used"] == "STRATEGY_C"

    res_exp = client.get(f"/api/chart/endpoints/{endpoint_id}/explainability")
    assert res_exp.status_code == 200
    assert len(res_exp.json()["records"]) == 1
    assert res_exp.json()["records"][0]["record_id"] == rec.record_id



def test_benchmark_run_populates_results_endpoint(client):
    res = client.post(
        "/api/chart/benchmark/run", json={"scenario_ids": [10], "seed": 42}
    )

    assert res.status_code == 200
    runs = res.json()["runs"]
    assert {r["strategy"] for r in runs} == {"STRATEGY_A", "STRATEGY_B", "STRATEGY_C"}
    assert all(r["scenario_id"] == 10 and r["seed"] == 42 for r in runs)
    assert all(r["total_wasted_retries"] is not None for r in runs)

    stored = client.get("/api/chart/benchmark/results").json()["runs"]
    assert {r["run_id"] for r in stored} == {r["run_id"] for r in runs}


def test_benchmark_run_is_deterministic_and_does_not_duplicate(client):
    body = {"scenario_ids": [10], "seed": 42}
    first = client.post("/api/chart/benchmark/run", json=body).json()
    second = client.post("/api/chart/benchmark/run", json=body).json()

    def metrics(payload):
        return [
            (r["strategy"], r["delivery_success_rate"], r["total_wasted_retries"], r["total_delay_seconds"])
            for r in payload["runs"]
        ]

    assert metrics(first) == metrics(second)
    assert first["comparison"] == second["comparison"]
    assert len(client.get("/api/chart/benchmark/results").json()["runs"]) == 3


def test_benchmark_comparison_is_computed_from_the_runs(client):
    payload = client.post(
        "/api/chart/benchmark/run", json={"scenario_ids": [4, 10]}
    ).json()
    runs = payload["runs"]

    def total(strategy, field):
        return sum(r[field] for r in runs if r["strategy"] == strategy)

    for key, baseline in (("c_vs_a", "STRATEGY_A"), ("c_vs_b", "STRATEGY_B")):
        cmp = payload["comparison"][key]
        assert cmp["baseline"] == baseline
        assert cmp["scenarios"] == [4, 10]
        c, b = total("STRATEGY_C", "total_wasted_retries"), total(baseline, "total_wasted_retries")
        assert cmp["wasted_retries"]["strategy_c"] == c
        assert cmp["wasted_retries"]["baseline"] == b
        assert cmp["wasted_retries"]["pct_change"] == pytest.approx((c - b) / b * 100, abs=0.01)


def test_benchmark_run_without_strategy_c_has_no_comparison(client):
    payload = client.post(
        "/api/chart/benchmark/run",
        json={"scenario_ids": [10], "strategies": ["STRATEGY_A"]},
    ).json()

    assert len(payload["runs"]) == 1
    assert payload["comparison"] == {}


def test_benchmark_run_rejects_unknown_inputs(client):
    res = client.post(
        "/api/chart/benchmark/run",
        json={"scenario_ids": [99], "strategies": ["STRATEGY_Z"]},
    )

    assert res.status_code == 422
    detail = res.json()["detail"]
    assert detail["unknown_scenario_ids"] == [99]
    assert detail["unknown_strategies"] == ["STRATEGY_Z"]
    assert client.get("/api/chart/benchmark/results").json()["runs"] == []


def test_multi_endpoint_comparison_shape(client):
    res = client.get("/api/chart/benchmark/multi-endpoint")

    assert res.status_code == 200
    body = res.json()
    assert body["worker_pool_size"] > 0
    by_strategy = {s["strategy"]: s for s in body["strategies"]}
    assert set(by_strategy) == {"STRATEGY_A", "STRATEGY_B", "STRATEGY_C"}
    for result in by_strategy.values():
        assert result["healthy"]["throughput_events_per_sec"] > 0
        assert result["failing"]["endpoint_id"] != result["healthy"]["endpoint_id"]
    assert client.get("/api/chart/benchmark/multi-endpoint").json() == body


# --------------------------------------------------------------------------
# Scenario Lab
# --------------------------------------------------------------------------


def test_scenarios_lists_all_fourteen_with_their_evaluation_set(client) -> None:
    scenarios = client.get("/api/chart/scenarios").json()["scenarios"]

    assert [s["scenario_id"] for s in scenarios] == list(range(1, 15))
    held_out = sorted(s["scenario_id"] for s in scenarios if s["evaluation_set"] == "held_out")
    assert held_out == [3, 4, 5, 9, 10, 12, 13, 14]
    first = scenarios[0]
    assert first["total_events"] == sum(p["events"] for p in first["phases"])


def test_trace_replays_the_same_run_as_the_benchmark(client) -> None:
    trace = client.get("/api/chart/scenarios/10/trace").json()

    by_strategy = {s["strategy"]: s for s in trace["strategies"]}
    assert list(by_strategy) == ["STRATEGY_A", "STRATEGY_B", "STRATEGY_C"]
    # Seed 42, scenario 10: the figures reported for the benchmark.
    assert [by_strategy[k]["total_wasted_retries"] for k in by_strategy] == [930, 626, 428]
    for strategy in by_strategy.values():
        steps = strategy["steps"]
        assert len(steps) == trace["scenario"]["total_events"]
        assert sum(step["retries"] for step in steps) == strategy["total_wasted_retries"]
        assert [step["phase"] for step in steps] == sorted(step["phase"] for step in steps)
    assert {step["state"] for step in by_strategy["STRATEGY_A"]["steps"]} == {"AVAILABLE"}


def test_trace_of_unknown_scenario_is_404(client) -> None:
    assert client.get("/api/chart/scenarios/99/trace").status_code == 404
