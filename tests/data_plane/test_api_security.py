"""
Regression tests for ADR 0018 (API authentication and endpoint ownership)
and the request boundary limits.

Repositories and the Kafka producer are replaced with fakes, so these run
with no Postgres and no broker. The point is the security boundary, not
persistence -- that is covered by scripts/api_smoke.sh.
"""
import pytest
from fastapi.testclient import TestClient

from relay.api import deps, security
from relay.api.main import app
from relay.api.schemas import MAX_PAYLOAD_BYTES
from relay.api.security import DEV_OWNER_ID


class FakeEventRepository:
    def __init__(self) -> None:
        self.saved = []

    async def save(self, event) -> None:
        self.saved.append(event)

    async def get_by_id(self, event_id: str):
        return next((e for e in self.saved if e.event_id == event_id), None)


class FakeEndpointRepository:
    def __init__(self) -> None:
        self.saved = []

    async def save(self, endpoint) -> None:
        self.saved.append(endpoint)


class FakeSubscriptionRepository:
    def __init__(self) -> None:
        self.saved = []

    async def save(self, subscription) -> None:
        self.saved.append(subscription)


@pytest.fixture
def api(monkeypatch):
    """
    A TestClient with repositories and Kafka stubbed out.

    The app lifespan calls ensure_topics(), which bootstraps a real Kafka
    connection, so it has to be stubbed too -- otherwise entering the
    TestClient context raises KafkaConnectionError and the whole module
    silently depends on `docker compose up`.

    _configured_keys is lru_cached, so it must be cleared around every
    test that changes RELAY_API_KEYS -- otherwise the first test's key set
    leaks into the rest of the module.
    """
    events = FakeEventRepository()
    endpoints = FakeEndpointRepository()
    subscriptions = FakeSubscriptionRepository()

    async def noop(*args, **kwargs):
        return None

    monkeypatch.setattr("relay.api.events.publish", noop)
    monkeypatch.setattr("relay.api.main.ensure_topics", noop)
    monkeypatch.setattr("relay.api.main.close_producer", noop)
    app.dependency_overrides[deps.get_event_repository] = lambda: events
    app.dependency_overrides[deps.get_endpoint_repository] = lambda: endpoints
    app.dependency_overrides[deps.get_subscription_repository] = lambda: subscriptions
    security._configured_keys.cache_clear()

    with TestClient(app) as client:
        client.events = events
        client.endpoints = endpoints
        yield client

    app.dependency_overrides.clear()
    security._configured_keys.cache_clear()


def configure_keys(monkeypatch, value: str, env: str = "production"):
    monkeypatch.setenv("RELAY_API_KEYS", value)
    monkeypatch.setenv("RELAY_ENV", env)
    security._configured_keys.cache_clear()


# --------------------------------------------------------------------------
# Authentication
# --------------------------------------------------------------------------


def test_dev_mode_without_keys_stays_open(api, monkeypatch):
    """scripts/api_smoke.sh and local uvicorn depend on this."""
    monkeypatch.delenv("RELAY_API_KEYS", raising=False)
    monkeypatch.setenv("RELAY_ENV", "dev")
    security._configured_keys.cache_clear()

    response = api.post(
        "/events", json={"event_type": "OrderCreated", "payload": {"a": 1}}
    )

    assert response.status_code == 202


def test_production_without_keys_refuses_traffic(api, monkeypatch):
    """
    Failing closed with 503 is the point: a deploy that forgets
    RELAY_API_KEYS must not silently expose the ingestion surface.
    """
    monkeypatch.delenv("RELAY_API_KEYS", raising=False)
    monkeypatch.setenv("RELAY_ENV", "production")
    security._configured_keys.cache_clear()

    response = api.post(
        "/events", json={"event_type": "OrderCreated", "payload": {"a": 1}}
    )

    assert response.status_code == 503


def test_missing_api_key_is_rejected(api, monkeypatch):
    configure_keys(monkeypatch, "acme:supersecret")

    response = api.post(
        "/events", json={"event_type": "OrderCreated", "payload": {"a": 1}}
    )

    assert response.status_code == 401
    assert response.headers.get("www-authenticate") == "ApiKey"


@pytest.mark.parametrize(
    "bad_key", ["wrong", "", "supersecre", "supersecrett", "SUPERSECRET"]
)
def test_invalid_api_key_is_rejected(bad_key, api, monkeypatch):
    configure_keys(monkeypatch, "acme:supersecret")

    response = api.post(
        "/events",
        json={"event_type": "OrderCreated", "payload": {"a": 1}},
        headers={"X-API-Key": bad_key},
    )

    assert response.status_code == 401


def test_valid_api_key_is_accepted(api, monkeypatch):
    configure_keys(monkeypatch, "acme:supersecret")

    response = api.post(
        "/events",
        json={"event_type": "OrderCreated", "payload": {"a": 1}},
        headers={"X-API-Key": "supersecret"},
    )

    assert response.status_code == 202


def test_every_ingestion_route_requires_authentication(api, monkeypatch):
    """A new route added without require_owner should fail this test."""
    configure_keys(monkeypatch, "acme:supersecret")

    unauthenticated = [
        api.post("/events", json={"event_type": "X", "payload": {}}),
        api.get("/events/some-id"),
        api.post("/endpoints", json={"target_url": "https://example.com/h"}),
        api.post(
            "/subscriptions",
            json={"endpoint_id": "e-1", "event_types": ["X"]},
        ),
    ]

    assert [r.status_code for r in unauthenticated] == [401, 401, 401, 401]


def test_malformed_key_configuration_is_a_hard_error(api, monkeypatch):
    """Silently ignoring a malformed entry could leave the API wide open."""
    configure_keys(monkeypatch, "no-colon-here")

    with pytest.raises(RuntimeError):
        security._configured_keys()


def test_multiple_owners_resolve_to_their_own_ids(api, monkeypatch):
    configure_keys(monkeypatch, "acme:key-a,globex:key-b")

    api.post(
        "/endpoints",
        json={"target_url": "https://a.example.com/hook"},
        headers={"X-API-Key": "key-a"},
    )
    api.post(
        "/endpoints",
        json={"target_url": "https://b.example.com/hook"},
        headers={"X-API-Key": "key-b"},
    )

    assert [e.owner_id for e in api.endpoints.saved] == ["acme", "globex"]


# --------------------------------------------------------------------------
# Ownership cannot be spoofed
# --------------------------------------------------------------------------


def test_owner_id_comes_from_the_key_not_the_body(api, monkeypatch):
    """
    The vulnerability this closes: any client could claim to own an
    endpoint by putting someone else's owner_id in the body.
    """
    configure_keys(monkeypatch, "acme:key-a")

    response = api.post(
        "/endpoints",
        json={
            "target_url": "https://a.example.com/hook",
            "owner_id": "victim-corp",
        },
        headers={"X-API-Key": "key-a"},
    )

    assert response.status_code == 201
    assert response.json()["owner_id"] == "acme"
    assert api.endpoints.saved[0].owner_id == "acme"


def test_body_owner_id_is_honoured_only_in_open_dev_mode(api, monkeypatch):
    monkeypatch.delenv("RELAY_API_KEYS", raising=False)
    monkeypatch.setenv("RELAY_ENV", "dev")
    security._configured_keys.cache_clear()

    response = api.post(
        "/endpoints",
        json={"target_url": "https://a.example.com/hook", "owner_id": "local-dev"},
    )

    assert response.json()["owner_id"] == "local-dev"


def test_dev_mode_defaults_the_owner_when_body_omits_it(api, monkeypatch):
    monkeypatch.delenv("RELAY_API_KEYS", raising=False)
    monkeypatch.setenv("RELAY_ENV", "dev")
    security._configured_keys.cache_clear()

    response = api.post(
        "/endpoints", json={"target_url": "https://a.example.com/hook"}
    )

    assert response.json()["owner_id"] == DEV_OWNER_ID


# --------------------------------------------------------------------------
# SSRF at registration
# --------------------------------------------------------------------------


@pytest.fixture
def dev_api(api, monkeypatch):
    monkeypatch.delenv("RELAY_API_KEYS", raising=False)
    monkeypatch.setenv("RELAY_ENV", "dev")
    security._configured_keys.cache_clear()
    return api


@pytest.mark.parametrize(
    "target_url",
    [
        "http://169.254.169.254/latest/meta-data/",
        "http://127.0.0.1:8000/webhook",
        "http://10.0.0.1/internal",
        "http://100.100.100.200/latest/",
        "file:///etc/passwd",
        "https://user:pass@example.com/hook",
    ],
)
def test_unsafe_target_url_is_rejected_at_registration(target_url, dev_api):
    response = dev_api.post("/endpoints", json={"target_url": target_url})

    assert response.status_code == 422
    assert dev_api.endpoints.saved == []


def test_safe_target_url_is_accepted(dev_api):
    response = dev_api.post(
        "/endpoints", json={"target_url": "https://hooks.example.com/webhook"}
    )

    assert response.status_code == 201


# --------------------------------------------------------------------------
# Request boundary limits
# --------------------------------------------------------------------------


def test_oversized_payload_is_rejected(dev_api):
    """
    An unbounded payload lands in JSONB and then produces a Kafka record
    larger than max.request.size -- a persisted event that can never be
    published.
    """
    payload = {"blob": "x" * (MAX_PAYLOAD_BYTES + 1024)}

    response = dev_api.post(
        "/events", json={"event_type": "OrderCreated", "payload": payload}
    )

    assert response.status_code in (413, 422)
    assert dev_api.events.saved == []


def test_payload_just_under_the_limit_is_accepted(dev_api):
    payload = {"blob": "x" * (MAX_PAYLOAD_BYTES - 1024)}

    response = dev_api.post(
        "/events", json={"event_type": "OrderCreated", "payload": payload}
    )

    assert response.status_code == 202


def test_body_size_middleware_rejects_before_parsing(dev_api):
    """
    The middleware caps the request by Content-Length so a huge body is
    refused without being parsed into memory first.
    """
    huge = b'{"event_type":"X","payload":{"b":"' + b"x" * (2 * 1024 * 1024) + b'"}}'

    response = dev_api.post(
        "/events", content=huge, headers={"Content-Type": "application/json"}
    )

    assert response.status_code == 413


def test_invalid_content_length_is_rejected(dev_api):
    response = dev_api.post(
        "/events",
        content=b'{"event_type":"X","payload":{}}',
        headers={"Content-Type": "application/json", "Content-Length": "abc"},
    )

    assert response.status_code == 400


@pytest.mark.parametrize(
    "body",
    [
        {"event_type": "", "payload": {}},
        {"event_type": "x" * 300, "payload": {}},
        {"payload": {}},
        {"event_type": "OrderCreated"},
        {"event_type": "OrderCreated", "payload": "not-an-object"},
    ],
)
def test_malformed_ingest_requests_are_rejected(body, dev_api):
    response = dev_api.post("/events", json=body)

    assert response.status_code == 422


def test_too_many_event_types_is_rejected(dev_api):
    response = dev_api.post(
        "/subscriptions",
        json={"endpoint_id": "e-1", "event_types": [f"T{i}" for i in range(200)]},
    )

    assert response.status_code == 422


def test_empty_event_type_in_subscription_is_rejected(dev_api):
    response = dev_api.post(
        "/subscriptions",
        json={"endpoint_id": "e-1", "event_types": ["Valid", ""]},
    )

    assert response.status_code == 422


def test_overlong_target_url_is_rejected(dev_api):
    response = dev_api.post(
        "/endpoints",
        json={"target_url": "https://example.com/" + "a" * 4000},
    )

    assert response.status_code == 422
