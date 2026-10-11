import asyncio

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from relay.api import demo, deps
from relay.kernel.entities.endpoint import Endpoint
from relay.kernel.entities.subscription import Subscription
from simulator.config import FailureMode, SimulatorConfig


class FakeEndpointRepository:
    def __init__(self) -> None:
        self.endpoints: list[Endpoint] = []

    async def save(self, endpoint: Endpoint) -> None:
        self.endpoints.append(endpoint)

    async def get_by_owner_and_target(self, owner_id: str, target_url: str):
        for endpoint in self.endpoints:
            if endpoint.owner_id == owner_id and endpoint.target_url == target_url:
                return endpoint
        return None


class FakeSubscriptionRepository:
    def __init__(self) -> None:
        self.subscriptions: list[Subscription] = []

    async def save(self, subscription: Subscription) -> None:
        self.subscriptions.append(subscription)

    async def list_active_by_event_type(self, event_type: str):
        return [s for s in self.subscriptions if event_type in s.event_types]


@pytest.fixture
def harness(monkeypatch):
    monkeypatch.setenv("RELAY_ALLOW_PRIVATE_TARGETS", "true")
    monkeypatch.setenv("SIMULATOR_URL", "http://simulator:8080")
    monkeypatch.setenv("DEMO_EVENTS_PER_SEC", "200")
    monkeypatch.delenv("DEMO_TARGET_URL", raising=False)
    monkeypatch.delenv("RELAY_API_KEYS", raising=False)
    monkeypatch.delenv("RELAY_ENV", raising=False)

    endpoints = FakeEndpointRepository()
    subscriptions = FakeSubscriptionRepository()
    calls = {"simulator": [], "published": [], "restored": []}

    async def fake_configure(settings):
        calls["simulator"].append(settings.model_dump())

    async def fake_persist_and_publish(event, repo):
        calls["published"].append(event)

    async def fake_restore(endpoint_id):
        calls["restored"].append(endpoint_id)

    monkeypatch.setattr(demo, "configure_simulator", fake_configure)
    monkeypatch.setattr(demo, "persist_and_publish", fake_persist_and_publish)
    monkeypatch.setattr(demo, "publish_restore", fake_restore)
    monkeypatch.setattr(demo, "controller", demo.DemoController())

    app = FastAPI()
    app.include_router(demo.router)
    app.dependency_overrides[deps.get_endpoint_repository] = lambda: endpoints
    app.dependency_overrides[deps.get_subscription_repository] = lambda: subscriptions
    app.dependency_overrides[deps.get_event_repository] = lambda: object()

    with TestClient(app) as client:
        client.calls = calls
        client.endpoints = endpoints
        client.subscriptions = subscriptions
        yield client
        client.post("/demo/stop")


def test_every_preset_uses_a_mode_the_simulator_accepts() -> None:
    for preset in demo.SCENARIOS.values():
        config = SimulatorConfig(**preset.simulator.model_dump())
        assert isinstance(config.mode, FailureMode)


def test_timeout_preset_outlasts_the_default_worker_timeout() -> None:
    from relay.adapters.webhook.adapter import DEFAULT_TIMEOUT_SECONDS

    latency_ms = demo.SCENARIOS["full_outage"].simulator.latency_ms
    assert latency_ms > DEFAULT_TIMEOUT_SECONDS * 1000


def test_list_scenarios_returns_all_presets(harness) -> None:
    res = harness.get("/demo/scenarios")

    assert res.status_code == 200
    assert {s["id"] for s in res.json()} == {
        "normal",
        "early_degradation",
        "instability",
        "full_outage",
        "recovery",
        "rate_limiting",
        "auth_failure",
        "network_partition",
    }


def test_status_is_idle_before_any_scenario(harness) -> None:
    body = harness.get("/demo/status").json()

    assert body["active_scenario"] is None
    assert body["publishing"] is False


def test_activate_configures_simulator_and_starts_publishing(harness) -> None:
    res = harness.post("/demo/scenario", json={"scenario": "instability"})

    assert res.status_code == 200
    body = res.json()
    assert body["active_scenario"] == "instability"
    assert body["publishing"] is True
    assert body["simulator"] == {"mode": "http_error", "rate": 0.75, "latency_ms": 0}
    assert harness.calls["simulator"] == [body["simulator"]]

    endpoint = harness.endpoints.endpoints[0]
    assert endpoint.target_url == "http://simulator:8080/webhook"
    assert body["endpoint_id"] == endpoint.endpoint_id
    assert body["subscription_id"] == harness.subscriptions.subscriptions[0].subscription_id

    harness.portal.call(asyncio.sleep, 0.1)
    assert harness.calls["published"], "no events were published"
    event = harness.calls["published"][0]
    assert event.event_type == demo.DEMO_EVENT_TYPE
    assert event.payload["scenario"] == "instability"
    assert harness.get("/demo/status").json()["events_published"] >= 1


def test_switching_scenario_reuses_endpoint_subscription_and_publisher(harness) -> None:
    first = harness.post("/demo/scenario", json={"scenario": "normal"}).json()
    task = demo.controller._task
    second = harness.post("/demo/scenario", json={"scenario": "full_outage"}).json()

    assert len(harness.endpoints.endpoints) == 1
    assert len(harness.subscriptions.subscriptions) == 1
    assert second["endpoint_id"] == first["endpoint_id"]
    assert second["subscription_id"] == first["subscription_id"]
    assert demo.controller._task is task
    assert second["active_scenario"] == "full_outage"
    assert harness.calls["simulator"][-1]["mode"] == "timeout"


def test_recovery_requests_restore_and_other_scenarios_do_not(harness) -> None:
    harness.post("/demo/scenario", json={"scenario": "full_outage"})
    assert harness.calls["restored"] == []

    body = harness.post("/demo/scenario", json={"scenario": "recovery"}).json()

    assert harness.calls["restored"] == [body["endpoint_id"]]


def test_unknown_scenario_is_rejected_without_side_effects(harness) -> None:
    res = harness.post("/demo/scenario", json={"scenario": "meteor_strike"})

    assert res.status_code == 404
    assert harness.calls["simulator"] == []
    assert harness.endpoints.endpoints == []
    assert harness.get("/demo/status").json()["publishing"] is False


def test_simulator_failure_is_reported_and_nothing_is_published(harness, monkeypatch) -> None:
    import httpx

    async def unreachable(settings):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(demo, "configure_simulator", unreachable)

    res = harness.post("/demo/scenario", json={"scenario": "normal"})

    assert res.status_code == 502
    assert harness.get("/demo/status").json()["publishing"] is False
    assert harness.calls["published"] == []


def test_publish_failure_is_counted_and_does_not_stop_the_demo(harness, monkeypatch) -> None:
    async def broken(event, repo):
        raise RuntimeError("kafka down")

    monkeypatch.setattr(demo, "persist_and_publish", broken)

    harness.post("/demo/scenario", json={"scenario": "normal"})
    harness.portal.call(asyncio.sleep, 0.1)
    body = harness.get("/demo/status").json()

    assert body["publishing"] is True
    assert body["publish_failures"] >= 1
    assert "kafka down" in body["last_error"]
    assert body["events_published"] == 0


def test_stop_ends_publishing(harness) -> None:
    harness.post("/demo/scenario", json={"scenario": "normal"})

    body = harness.post("/demo/stop").json()

    assert body["publishing"] is False
    assert body["active_scenario"] is None
    harness.portal.call(asyncio.sleep, 0.05)
    count = len(harness.calls["published"])
    harness.portal.call(asyncio.sleep, 0.05)
    assert len(harness.calls["published"]) == count


def test_demo_router_is_mounted_only_when_enabled(monkeypatch) -> None:
    monkeypatch.delenv("RELAY_DEMO_ENABLED", raising=False)
    assert demo.demo_enabled() is False

    monkeypatch.setenv("RELAY_DEMO_ENABLED", "true")
    assert demo.demo_enabled() is True

    from relay.api.main import app

    mounted = any(getattr(r, "path", "").startswith("/demo") for r in app.routes)
    assert mounted is False, "demo routes are exposed without RELAY_DEMO_ENABLED"
