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
