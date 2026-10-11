"""Unit tests for the Relay Python SDK.

Uses httpx.MockTransport to mock API interactions deterministically without requiring
a running server or external services.
"""
import json
import pytest
import httpx

from relay.sdk import (
    EndpointResponse,
    EventDetail,
    EventResponse,
    RelayAPIError,
    RelayClient,
    RelayConnectionError,
    SubscriptionResponse,
)


def test_publish_event_success():
    """Test successful event publication returns EventResponse with event_id."""
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/events"
        body = json.loads(request.content.decode("utf-8"))
        assert body["event_type"] == "OrderCreated"
        assert body["payload"] == {"order_id": "abc-123", "amount": 42}
        return httpx.Response(202, json={"event_id": "evt-550e8400"})

    transport = httpx.MockTransport(handler)
    client = RelayClient(base_url="http://test-relay:8000", transport=transport)
    response = client.publish_event("OrderCreated", {"order_id": "abc-123", "amount": 42})

    assert isinstance(response, EventResponse)
    assert response.event_id == "evt-550e8400"
    client.close()


def test_publish_event_kafka_failure():
    """Test that a 503 response (Postgres persisted, Kafka failed) raises RelayAPIError."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            503,
            json={
                "detail": {
                    "message": "Event persisted but Kafka publish failed. Do not treat as accepted.",
                    "event_id": "evt-failed-kafka",
                }
            },
        )

    transport = httpx.MockTransport(handler)
    client = RelayClient(transport=transport)

    with pytest.raises(RelayAPIError) as exc_info:
        client.publish_event("OrderCreated", {"order_id": "fail-test"})

    assert exc_info.value.status_code == 503
    assert "Kafka publish failed" in str(exc_info.value.detail)
    client.close()


def test_get_event_success():
    """Test retrieving full event details returns EventDetail instance."""
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/events/evt-123"
        return httpx.Response(
            200,
            json={
                "event_id": "evt-123",
                "event_type": "PaymentCompleted",
                "payload": {"amount": 100.0, "currency": "USD"},
                "occurred_at": "2026-08-24T12:00:00Z",
                "correlation_id": {"value": "corr-uuid-1"},
                "causation_id": {"value": "caus-uuid-1"},
            },
        )

    transport = httpx.MockTransport(handler)
    client = RelayClient(transport=transport)
    event = client.get_event("evt-123")

    assert isinstance(event, EventDetail)
    assert event.event_id == "evt-123"
    assert event.event_type == "PaymentCompleted"
    assert event.payload == {"amount": 100.0, "currency": "USD"}
    assert event.correlation_id == "corr-uuid-1"
    assert event.causation_id == "caus-uuid-1"
    client.close()


def test_get_event_not_found():
    """Test that requesting a non-existent event raises RelayAPIError with 404."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"detail": "Event unknown-id not found"})

    transport = httpx.MockTransport(handler)
    client = RelayClient(transport=transport)

    with pytest.raises(RelayAPIError) as exc_info:
        client.get_event("unknown-id")

    assert exc_info.value.status_code == 404
    assert "unknown-id not found" in exc_info.value.detail
    client.close()


def test_register_endpoint_success():
    """Test endpoint registration returns EndpointResponse."""
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/endpoints"
        body = json.loads(request.content.decode("utf-8"))
        assert body["target_url"] == "http://receiver:9000/webhook"
        assert body["owner_id"] == "team-billing"
        return httpx.Response(
            201,
            json={
                "endpoint_id": "ep-999",
                "target_url": "http://receiver:9000/webhook",
                "owner_id": "team-billing",
                "created_at": "2026-08-24T12:30:00Z",
                "is_active": True,
            },
        )

    transport = httpx.MockTransport(handler)
    client = RelayClient(transport=transport)
    endpoint = client.register_endpoint("http://receiver:9000/webhook", "team-billing")

    assert isinstance(endpoint, EndpointResponse)
    assert endpoint.endpoint_id == "ep-999"
    assert endpoint.target_url == "http://receiver:9000/webhook"
    assert endpoint.owner_id == "team-billing"
    assert endpoint.is_active is True
    client.close()


def test_create_subscription_success():
    """Test subscription creation returns SubscriptionResponse."""
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/subscriptions"
        body = json.loads(request.content.decode("utf-8"))
        assert body["endpoint_id"] == "ep-999"
        assert body["event_types"] == ["OrderCreated", "OrderShipped"]
        return httpx.Response(
            201,
            json={
                "subscription_id": "sub-111",
                "endpoint_id": "ep-999",
                "event_types": ["OrderCreated", "OrderShipped"],
                "created_at": "2026-08-24T12:35:00Z",
                "status": "ACTIVE",
            },
        )

    transport = httpx.MockTransport(handler)
    client = RelayClient(transport=transport)
    sub = client.create_subscription("ep-999", ["OrderCreated", "OrderShipped"])

    assert isinstance(sub, SubscriptionResponse)
    assert sub.subscription_id == "sub-111"
    assert sub.endpoint_id == "ep-999"
    assert sub.event_types == ["OrderCreated", "OrderShipped"]
    assert sub.status == "ACTIVE"
    client.close()


def test_create_subscription_endpoint_not_found():
    """Test creating subscription for non-existent endpoint raises RelayAPIError (404)."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"detail": "Endpoint invalid-ep not found"})

    transport = httpx.MockTransport(handler)
    client = RelayClient(transport=transport)

    with pytest.raises(RelayAPIError) as exc_info:
        client.create_subscription("invalid-ep", ["OrderCreated"])

    assert exc_info.value.status_code == 404
    assert "invalid-ep not found" in exc_info.value.detail
    client.close()


def test_connection_error():
    """Test connection or transport errors raise RelayConnectionError."""
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("Connection refused by target", request=request)

    transport = httpx.MockTransport(handler)
    client = RelayClient(base_url="http://unreachable-host:9999", transport=transport)

    with pytest.raises(RelayConnectionError) as exc_info:
        client.publish_event("OrderCreated", {})

    assert "Failed to connect to Relay API" in str(exc_info.value)
    client.close()


def test_context_manager():
    """Test that RelayClient operates cleanly as a context manager."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(202, json={"event_id": "ctx-evt-1"})

    transport = httpx.MockTransport(handler)
    with RelayClient(transport=transport) as client:
        res = client.publish_event("TestEvent", {})
        assert res.event_id == "ctx-evt-1"
        assert not client._http.is_closed

    assert client._http.is_closed


def test_custom_base_url():
    """Test that custom base_url is respected across requests."""
    requested_urls = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_urls.append(str(request.url))
        return httpx.Response(202, json={"event_id": "custom-url-evt"})

    transport = httpx.MockTransport(handler)
    client = RelayClient(base_url="http://custom-api.internal:8080/v1", transport=transport)
    client.publish_event("CustomEvent", {"k": "v"})

    assert len(requested_urls) == 1
    assert requested_urls[0] == "http://custom-api.internal:8080/v1/events"
    client.close()


def test_replay_dead_letters_posts_filters_and_parses_result():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            202,
            json={
                "replay_id": "replay-1",
                "requested": 3,
                "skipped": [{"delivery_id": "d-2", "why": "already replayed"}],
            },
        )

    client = RelayClient(transport=httpx.MockTransport(handler))
    result = client.replay_dead_letters(endpoint_id="ep-1", delivery_ids=["d-1", "d-2"], limit=5)

    assert seen == {
        "path": "/replay",
        "body": {"limit": 5, "endpoint_id": "ep-1", "delivery_ids": ["d-1", "d-2"]},
    }
    assert result.replay_id == "replay-1"
    assert result.requested == 3
    assert result.skipped == [{"delivery_id": "d-2", "why": "already replayed"}]


def test_replay_dead_letters_omits_unset_filters():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return httpx.Response(202, json={"replay_id": "r", "requested": 0, "skipped": []})

    RelayClient(transport=httpx.MockTransport(handler)).replay_dead_letters()

    assert seen["body"] == {"limit": 50}
