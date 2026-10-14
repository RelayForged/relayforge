"""Unit tests for AsyncRelayClient and the api_key option on both clients.

Uses httpx.MockTransport, so no server is needed.
"""
import json

import httpx
import pytest

from relay.sdk import (
    AsyncRelayClient,
    EndpointResponse,
    EventDetail,
    EventResponse,
    RelayAPIError,
    RelayClient,
    RelayConnectionError,
    ReplayResponse,
    SubscriptionResponse,
)


async def test_publish_event_success():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/events"
        body = json.loads(request.content)
        assert body == {"event_type": "payment.captured", "payload": {"payment_id": "pay_1"}}
        return httpx.Response(202, json={"event_id": "evt-1"})

    async with AsyncRelayClient(transport=httpx.MockTransport(handler)) as client:
        response = await client.publish_event("payment.captured", {"payment_id": "pay_1"})

    assert response == EventResponse(event_id="evt-1")


async def test_publish_event_non_202_raises_api_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"detail": {"message": "Kafka publish failed"}})

    async with AsyncRelayClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(RelayAPIError) as exc_info:
            await client.publish_event("x", {})

    assert exc_info.value.status_code == 503
    assert "Kafka publish failed" in str(exc_info.value.detail)


async def test_connection_failure_raises_connection_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    async with AsyncRelayClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(RelayConnectionError):
            await client.publish_event("x", {})


async def test_non_json_success_body_raises_api_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(202, text="not json")

    async with AsyncRelayClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(RelayAPIError):
            await client.publish_event("x", {})


async def test_get_event_unwraps_id_objects():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/events/evt-1"
        return httpx.Response(
            200,
            json={
                "event_id": "evt-1",
                "event_type": "OrderCreated",
                "payload": {"a": 1},
                "occurred_at": "2026-01-01T00:00:00Z",
                "correlation_id": {"value": "corr-1"},
                "causation_id": {"value": "cause-1"},
            },
        )

    async with AsyncRelayClient(transport=httpx.MockTransport(handler)) as client:
        detail = await client.get_event("evt-1")

    assert detail == EventDetail(
        event_id="evt-1",
        event_type="OrderCreated",
        payload={"a": 1},
        occurred_at="2026-01-01T00:00:00Z",
        correlation_id="corr-1",
        causation_id="cause-1",
    )


async def test_register_endpoint_and_subscription_and_replay():
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if request.url.path == "/endpoints":
            assert body == {"target_url": "https://hooks.example.com/x", "owner_id": "team-a"}
            return httpx.Response(
                201,
                json={
                    "endpoint_id": "ep-1",
                    "target_url": body["target_url"],
                    "owner_id": "team-a",
                    "created_at": "t",
                    "is_active": True,
                },
            )
        if request.url.path == "/subscriptions":
            assert body == {"endpoint_id": "ep-1", "event_types": ["payment.captured"]}
            return httpx.Response(
                201,
                json={
                    "subscription_id": "sub-1",
                    "endpoint_id": "ep-1",
                    "event_types": ["payment.captured"],
                    "created_at": "t",
                    "status": "ACTIVE",
                },
            )
        assert request.url.path == "/replay"
        assert body == {"limit": 5, "endpoint_id": "ep-1"}
        return httpx.Response(202, json={"replay_id": "r-1", "requested": 2, "skipped": []})

    async with AsyncRelayClient(transport=httpx.MockTransport(handler)) as client:
        endpoint = await client.register_endpoint("https://hooks.example.com/x", "team-a")
        subscription = await client.create_subscription("ep-1", ["payment.captured"])
        replay = await client.replay_dead_letters(endpoint_id="ep-1", limit=5)

    assert isinstance(endpoint, EndpointResponse) and endpoint.endpoint_id == "ep-1"
    assert isinstance(subscription, SubscriptionResponse) and subscription.status == "ACTIVE"
    assert replay == ReplayResponse(replay_id="r-1", requested=2, skipped=[])


async def test_async_client_sends_api_key_header():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["key"] = request.headers.get("X-API-Key")
        return httpx.Response(202, json={"event_id": "evt-1"})

    async with AsyncRelayClient(
        transport=httpx.MockTransport(handler), api_key="team-a-secret"
    ) as client:
        await client.publish_event("x", {})

    assert seen["key"] == "team-a-secret"


async def test_async_client_sends_no_key_header_by_default():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["has_key"] = "x-api-key" in request.headers
        return httpx.Response(202, json={"event_id": "evt-1"})

    async with AsyncRelayClient(transport=httpx.MockTransport(handler)) as client:
        await client.publish_event("x", {})

    assert seen["has_key"] is False


def test_sync_client_sends_api_key_header():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["key"] = request.headers.get("X-API-Key")
        return httpx.Response(202, json={"event_id": "evt-1"})

    with RelayClient(transport=httpx.MockTransport(handler), api_key="team-a-secret") as client:
        client.publish_event("x", {})

    assert seen["key"] == "team-a-secret"


def test_sync_client_sends_no_key_header_by_default():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["has_key"] = "x-api-key" in request.headers
        return httpx.Response(202, json={"event_id": "evt-1"})

    with RelayClient(transport=httpx.MockTransport(handler)) as client:
        client.publish_event("x", {})

    assert seen["has_key"] is False
