from datetime import datetime, timezone

import pytest
from fastapi import FastAPI, WebSocket
from fastapi.testclient import TestClient

from relay.api.chart.stores import EndpointStateStore, state_store
from relay.control_plane.aede.strategies.aede_strategy import StrategyC
from relay.control_plane.eri.calculator import compute_eri
from relay.dashboard.adapter import SUB_SCORE_KEYS, build_live_state_update
from relay.dashboard.websocket import send_state_update
from relay.kernel.entities.policy_evaluation import PolicyEvaluation
from relay.kernel.value_objects.endpoint_state import EndpointState
from relay.kernel.value_objects.eri import ERIWeights
from relay.kernel.value_objects.telemetry import Telemetry


def _record(store: EndpointStateStore, endpoint_id: str, *, failures: int) -> EndpointState:
    """Push one real Control Plane evaluation into the store."""
    now = datetime.now(timezone.utc)
    attempts = [
        Telemetry(
            attempt_id=f"{endpoint_id}-attempt-{i}",
            endpoint_id=endpoint_id,
            succeeded=i >= failures,
            http_status=200 if i >= failures else 500,
            latency_ms=120.0,
            timed_out=False,
            observed_at=now,
        )
        for i in range(20)
    ]
    weights = ERIWeights.optimal()
    eri = compute_eri(
        attempts=attempts,
        queue_depth=0,
        weights=weights,
        baseline_latency_ms=100.0,
        max_latency_ms=5000.0,
        max_consecutive_failures=10,
        max_queue_depth=100,
        computed_at=now,
    )
    before = store.get_state(endpoint_id) or EndpointState.AVAILABLE
    policy, record = StrategyC().evaluate(
        endpoint_id=endpoint_id,
        endpoint_state=before,
        eri=eri,
        computed_at=now,
    )
    after = EndpointState(policy.endpoint_state)
    store.record_evaluation(
        endpoint_id=endpoint_id,
        state=after,
        eri_score=eri,
        policy_evaluation=PolicyEvaluation(
            endpoint_id=endpoint_id,
            policy_id=policy.policy_id,
            explainability_record_id=record.record_id,
            eri_score=eri.score,
            state_before=before.value,
            state_after=after.value,
            state_changed=before != after,
            strategy_used="STRATEGY_C",
            is_fallback=False,
            fallback_tier=1,
            evaluated_at=now,
            window_size=len(attempts),
        ),
        delivery_policy=policy,
        explainability_record=record,
    )
    return after


@pytest.fixture(autouse=True)
def _clean_global_store():
    state_store.clear()
    yield
    state_store.clear()


def test_live_update_reports_unknown_before_first_evaluation() -> None:
    update = build_live_state_update(
        endpoint_id=None,
        store=EndpointStateStore(),
        weights=ERIWeights.optimal(),
    )

    assert update["source"] == "live"
    assert update["endpoint_id"] is None
    assert update["state"] == "UNKNOWN"
    assert update["eri_history"] == []
    assert update["delivery_events"] == []
    assert set(update["eri_sub_scores"]) == set(SUB_SCORE_KEYS)


def test_live_update_carries_real_evaluation() -> None:
    store = EndpointStateStore()
    state = _record(store, "ep-1", failures=0)

    update = build_live_state_update(
        endpoint_id="ep-1",
        store=store,
        weights=ERIWeights.optimal(),
        delivery_events=[{"id": "a1"}],
    )

    assert update["type"] == "state_update"
    assert update["endpoint_id"] == "ep-1"
    assert update["state"] == state.value
    assert update["eri"] == pytest.approx(store.get_eri_history("ep-1")[-1].score)

    point = update["eri_history"][-1]
    assert point["state"] == state.value
    assert point["value"] == update["eri"]
    assert set(point["sub_scores"]) == set(SUB_SCORE_KEYS)

    assert update["eri_sub_scores"] == point["sub_scores"]
    assert set(update["eri_weights"]) == set(SUB_SCORE_KEYS)
    assert sum(update["eri_weights"].values()) == pytest.approx(1.0)
    assert update["delivery_events"] == [{"id": "a1"}]
    assert update["policy_decision"]["endpoint_id"] == "ep-1"
    assert update["policy_explanation"]["state_after"] == state.value


def test_live_update_thresholds_come_from_the_evaluation() -> None:
    store = EndpointStateStore()
    _record(store, "ep-1", failures=0)

    update = build_live_state_update(
        endpoint_id="ep-1", store=store, weights=ERIWeights.optimal()
    )

    expected = store.get_explainability_records("ep-1")[-1].thresholds.model_dump()
    assert update["thresholds"] == expected


def test_live_update_history_tracks_degradation() -> None:
    store = EndpointStateStore()
    _record(store, "ep-1", failures=0)
    _record(store, "ep-1", failures=20)

    update = build_live_state_update(
        endpoint_id="ep-1", store=store, weights=ERIWeights.optimal()
    )

    values = [point["value"] for point in update["eri_history"]]
    assert len(values) == 2
    assert values[1] < values[0]
    assert update["eri_sub_scores"]["success_rate"] == 0.0


def test_latest_endpoint_id_follows_most_recent_evaluation() -> None:
    store = EndpointStateStore()
    assert store.get_latest_endpoint_id() is None

    _record(store, "ep-old", failures=0)
    _record(store, "ep-new", failures=0)

    assert store.get_latest_endpoint_id() == "ep-new"


def _app(fetch_events) -> FastAPI:
    app = FastAPI()

    @app.websocket("/ws")
    async def ws(websocket: WebSocket, endpoint_id: str | None = None) -> None:
        await websocket.accept()
        await send_state_update(
            websocket,
            endpoint_id,
            fetch_events=fetch_events,
            interval_seconds=0.01,
        )

    return app


def test_websocket_streams_live_store(monkeypatch) -> None:
    monkeypatch.delenv("DASHBOARD_SYNTHETIC_SOURCE", raising=False)
    _record(state_store, "ep-live", failures=0)
    asked: list[str] = []

    async def fetch_events(endpoint_id: str):
        asked.append(endpoint_id)
        return [{"id": "attempt-1", "type": "DELIVERY_SUCCESS"}]

    with TestClient(_app(fetch_events)).websocket_connect("/ws") as ws:
        message = ws.receive_json()

    assert message["source"] == "live"
    assert message["endpoint_id"] == "ep-live"
    assert message["delivery_events"] == [
        {"id": "attempt-1", "type": "DELIVERY_SUCCESS"}
    ]
    assert asked[0] == "ep-live"


def test_websocket_honors_requested_endpoint(monkeypatch) -> None:
    monkeypatch.delenv("DASHBOARD_SYNTHETIC_SOURCE", raising=False)
    _record(state_store, "ep-a", failures=0)
    _record(state_store, "ep-b", failures=0)

    async def fetch_events(endpoint_id: str):
        return []

    with TestClient(_app(fetch_events)).websocket_connect("/ws?endpoint_id=ep-a") as ws:
        message = ws.receive_json()

    assert message["endpoint_id"] == "ep-a"


def test_websocket_survives_delivery_event_query_failure(monkeypatch) -> None:
    monkeypatch.delenv("DASHBOARD_SYNTHETIC_SOURCE", raising=False)
    _record(state_store, "ep-live", failures=0)

    async def fetch_events(endpoint_id: str):
        raise RuntimeError("database unavailable")

    with TestClient(_app(fetch_events)).websocket_connect("/ws") as ws:
        first = ws.receive_json()
        second = ws.receive_json()

    assert first["state"] == second["state"]
    assert first["delivery_events"] == []


def test_websocket_synthetic_source_is_opt_in_and_labelled(monkeypatch) -> None:
    monkeypatch.setenv("DASHBOARD_SYNTHETIC_SOURCE", "true")

    async def fetch_events(endpoint_id: str):
        raise AssertionError("synthetic stream must not query deliveries")

    with TestClient(_app(fetch_events)).websocket_connect("/ws") as ws:
        message = ws.receive_json()

    assert message["source"] == "synthetic"
    assert message["endpoint_id"] == "dashboard-demo-endpoint"


def test_live_update_reports_the_policy_tier() -> None:
    """Tier 1 unless the Dispatcher has announced a fallback (ADR 0024)."""
    store = EndpointStateStore()

    def update():
        return build_live_state_update(
            endpoint_id="ep-1", store=store, weights=ERIWeights.optimal()
        )

    assert update()["policy_tier"] == 1
    store.record_policy_tier("ep-1", 3)
    assert update()["policy_tier"] == 3
    store.record_policy_tier("ep-1", 1)
    assert update()["policy_tier"] == 1
