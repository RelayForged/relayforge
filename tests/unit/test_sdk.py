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


# --------------------------------------------------------------------------
# API key, channels, restore, receiver-side verification
# --------------------------------------------------------------------------


def _client(handler, **kwargs):
    return RelayClient(
        base_url="http://test-relay:8000", transport=httpx.MockTransport(handler), **kwargs
    )


def test_api_key_is_sent_on_every_request():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get("X-API-Key"))
        return httpx.Response(202, json={"event_id": "evt-1"})

    with _client(handler, api_key="payflow:s3cret") as client:
        client.publish_event("payment.captured", {"amount": 499})

    assert seen == ["payflow:s3cret"]


def test_no_api_key_header_by_default():
    def handler(request: httpx.Request) -> httpx.Response:
        assert "X-API-Key" not in request.headers
        return httpx.Response(202, json={"event_id": "evt-1"})

    with _client(handler) as client:
        client.publish_event("payment.captured", {"amount": 499})


def test_publish_and_subscribe_carry_channels():
    bodies = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        bodies.append(body)
        if request.url.path == "/events":
            return httpx.Response(202, json={"event_id": "evt-1"})
        return httpx.Response(
            201,
            json={
                "subscription_id": "sub-1",
                "endpoint_id": body["endpoint_id"],
                "event_types": body["event_types"],
                "created_at": "2026-10-14T00:00:00Z",
                "status": "ACTIVE",
                "channels": body["channels"],
            },
        )

    with _client(handler) as client:
        subscription = client.create_subscription("ep-1", ["payment.captured"], channels=["acme"])
        client.publish_event("payment.captured", {"amount": 499}, channels=["acme"])

    assert subscription.channels == ["acme"]
    assert [b["channels"] for b in bodies] == [["acme"], ["acme"]]


def test_restore_endpoint():
    from relay.sdk import RestoreResponse

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/endpoints/ep-1/restore"
        return httpx.Response(
            200,
            json={"status": "RESTORE_REQUESTED", "endpoint_id": "ep-1", "target_state": "RECOVERING"},
        )

    with _client(handler) as client:
        result = client.restore_endpoint("ep-1")

    assert result == RestoreResponse(
        status="RESTORE_REQUESTED", endpoint_id="ep-1", target_state="RECOVERING"
    )


def test_restore_unknown_endpoint_raises():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"detail": "Endpoint ep-x not found"})

    with _client(handler) as client, pytest.raises(RelayAPIError) as raised:
        client.restore_endpoint("ep-x")

    assert raised.value.status_code == 404


def _signed(secret="whsec", body=b'{"amount":499}', timestamp="1760400000", nonce="n-1"):
    from relay.infrastructure.auth.signing import compute_relay_signature

    return body, {
        "X-Relay-Signature": compute_relay_signature(secret, timestamp, nonce, body),
        "X-Relay-Timestamp": timestamp,
        "X-Relay-Nonce": nonce,
    }


def test_sdk_verifier_accepts_what_the_platform_signs():
    from relay.sdk import verify_signature

    body, headers = _signed()

    assert verify_signature("whsec", body, headers, now=1760400010)


def test_sdk_verifier_reads_lowercase_headers():
    from relay.sdk import verify_signature

    body, headers = _signed()
    lowered = {k.lower(): v for k, v in headers.items()}

    assert verify_signature("whsec", body, lowered, now=1760400010)


@pytest.mark.parametrize(
    "secret, body_override, now",
    [
        ("other", None, 1760400010),            # wrong secret
        ("whsec", b'{"amount":1}', 1760400010),  # body changed in transit
        ("whsec", None, 1760400000 + 301),       # too old
        ("", None, 1760400010),                  # no secret configured
    ],
)
def test_sdk_verifier_rejects(secret, body_override, now):
    from relay.sdk import verify_signature

    body, headers = _signed()

    assert not verify_signature(secret, body_override or body, headers, now=now)


def test_sdk_verifier_rejects_unsigned_requests():
    from relay.sdk import verify_signature

    assert not verify_signature("whsec", b"{}", {}, now=1760400010)
