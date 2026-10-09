import json
import pytest
from fastapi.testclient import TestClient

from relay.api import deps
from relay.api.main import app
from relay.api.security import DEV_OWNER_ID
from relay.kernel.entities.endpoint import Endpoint


class FakeEndpointRepository:
    def __init__(self) -> None:
        self.endpoints = {}

    async def save(self, endpoint: Endpoint) -> None:
        self.endpoints[endpoint.endpoint_id] = endpoint

    async def get_by_id(self, endpoint_id: str):
        return self.endpoints.get(endpoint_id)


@pytest.fixture
def client(monkeypatch):
    repo = FakeEndpointRepository()
    published = []

    async def fake_publish(topic, key, value):
        published.append((topic, key, value))

    monkeypatch.setattr("relay.api.endpoints.publish", fake_publish)
    monkeypatch.setattr("relay.api.main.warmup_producer", lambda: None)
    monkeypatch.setattr("relay.api.main.close_producer", lambda: None)
    app.dependency_overrides[deps.get_endpoint_repository] = lambda: repo

    test_client = TestClient(app)
    test_client.published = published
    test_client.repo = repo
    yield test_client
    app.dependency_overrides.clear()


def test_endpoint_lifecycle_and_restore(client):
    # 1. Create endpoint
    res = client.post("/endpoints", json={"target_url": "https://example.com/webhook", "owner_id": "test-owner"})
    assert res.status_code == 201
    ep = res.json()
    endpoint_id = ep["endpoint_id"]
    assert ep["target_url"] == "https://example.com/webhook"

    # 2. Get endpoint
    get_res = client.get(f"/endpoints/{endpoint_id}")
    assert get_res.status_code == 200
    assert get_res.json()["endpoint_id"] == endpoint_id

    # 3. Restore endpoint
    restore_res = client.post(f"/endpoints/{endpoint_id}/restore")
    assert restore_res.status_code == 200
    data = restore_res.json()
    assert data["status"] == "RESTORE_REQUESTED"
    assert data["endpoint_id"] == endpoint_id
    assert data["target_state"] == "RECOVERING"

    # 4. Verify Kafka control message was published
    assert len(client.published) == 1
    topic, key, val = client.published[0]
    assert topic == "events.internal.endpoint"
    assert key == endpoint_id
    msg = json.loads(val.decode("utf-8"))
    assert msg["action"] == "restore"
    assert msg["endpoint_id"] == endpoint_id


def test_restore_nonexistent_endpoint(client):
    res = client.post("/endpoints/nonexistent-id/restore")
    assert res.status_code == 404
