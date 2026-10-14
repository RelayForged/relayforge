"""Unit tests for the inbound-webhook boilerplate (relay/sdk/inbound.py)."""
import hashlib
import hmac
import json
from typing import Any, Dict, List, Optional, Tuple

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from relay.sdk import (
    AsyncRelayClient,
    InMemoryDedupe,
    RelayAPIError,
    RelayConnectionError,
    handle_inbound,
    hmac_sha256_hex,
    inbound_router,
)
from relay.sdk.models import EventResponse

SECRET = "whsec_test"
SIG_HEADER = "X-Razorpay-Signature"


def sign(body: bytes, secret: str = SECRET) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


RAZORPAY_BODY: Dict[str, Any] = {
    "event": "payment.captured",
    "payload": {
        "payment": {
            "entity": {"id": "pay_123", "amount": 49900, "currency": "INR", "order_id": "order_9"}
        }
    },
}


def raw(body: Any = None) -> bytes:
    return json.dumps(RAZORPAY_BODY if body is None else body).encode()


def map_razorpay(body: Dict[str, Any], headers) -> Optional[Tuple[str, Dict[str, Any]]]:
    if body["event"] != "payment.captured":
        return None
    entity = body["payload"]["payment"]["entity"]
    return body["event"], {
        "payment_id": entity["id"],
        "amount": entity["amount"],
        "currency": entity["currency"],
    }


class FakeRelay:
    """Records publishes; can be told to fail."""

    def __init__(self, error: Optional[Exception] = None) -> None:
        self.calls: List[Tuple[str, Dict[str, Any]]] = []
        self.error = error

    async def publish_event(self, event_type: str, payload: Dict[str, Any]) -> EventResponse:
        if self.error is not None:
            raise self.error
        self.calls.append((event_type, payload))
        return EventResponse(event_id=f"evt-{len(self.calls)}")


def signed_headers(body: bytes) -> Dict[str, str]:
    return {SIG_HEADER: sign(body)}


async def run(body: bytes, headers: Dict[str, str], relay: FakeRelay, **overrides):
    kwargs: Dict[str, Any] = dict(
        verify=hmac_sha256_hex(SECRET, SIG_HEADER), map_event=map_razorpay, relay=relay
    )
    kwargs.update(overrides)
    return await handle_inbound(body, headers, **kwargs)


# ---------- hmac_sha256_hex ----------


def test_verifier_accepts_a_correct_signature():
    body = raw()
    assert hmac_sha256_hex(SECRET, SIG_HEADER)(body, {SIG_HEADER.lower(): sign(body)})


def test_verifier_rejects_wrong_missing_and_tampered():
    verify = hmac_sha256_hex(SECRET, SIG_HEADER)
    body = raw()
    assert not verify(body, {SIG_HEADER.lower(): sign(body, "other-secret")})
    assert not verify(body, {})
    assert not verify(body, {SIG_HEADER.lower(): ""})
    assert not verify(body + b" ", {SIG_HEADER.lower(): sign(body)})


def test_verifier_handles_prefix():
    verify = hmac_sha256_hex(SECRET, "X-Hub-Signature-256", prefix="sha256=")
    body = raw()
    header = "x-hub-signature-256"
    assert verify(body, {header: "sha256=" + sign(body)})
    assert not verify(body, {header: sign(body)})  # prefix missing
    assert not verify(body, {header: "sha1=" + sign(body)})


def test_verifier_does_not_raise_on_non_ascii_header():
    verify = hmac_sha256_hex(SECRET, SIG_HEADER)
    assert verify(raw(), {SIG_HEADER.lower(): "sïgnature"}) is False


def test_verifier_rejects_empty_secret():
    with pytest.raises(ValueError):
        hmac_sha256_hex("", SIG_HEADER)


# ---------- handle_inbound: the fixed steps ----------


async def test_happy_path_publishes_mapped_event_and_returns_200():
    relay = FakeRelay()
    body = raw()

    result = await run(body, signed_headers(body), relay)

    assert result.status_code == 200
    assert result.body == {"ok": True, "event_id": "evt-1"}
    assert relay.calls == [
        ("payment.captured", {"payment_id": "pay_123", "amount": 49900, "currency": "INR"})
    ]


async def test_header_names_are_matched_case_insensitively():
    relay = FakeRelay()
    body = raw()

    result = await run(body, {SIG_HEADER.upper(): sign(body)}, relay)

    assert result.status_code == 200


async def test_bad_signature_is_401_and_nothing_runs():
    relay = FakeRelay()
    mapper_calls = []

    def mapper(body, headers):
        mapper_calls.append(body)
        return map_razorpay(body, headers)

    body = raw()
    result = await run(body, {SIG_HEADER: sign(body, "wrong")}, relay, map_event=mapper)

    assert result.status_code == 401
    assert relay.calls == []
    assert mapper_calls == []


async def test_signature_over_different_bytes_is_401():
    relay = FakeRelay()
    signed_for = raw()
    tampered = raw({**RAZORPAY_BODY, "event": "payment.failed"})

    result = await run(tampered, signed_headers(signed_for), relay)

    assert result.status_code == 401
    assert relay.calls == []


async def test_verifier_that_raises_fails_closed():
    relay = FakeRelay()

    def broken(raw_body, headers):
        raise RuntimeError("boom")

    result = await run(raw(), {}, relay, verify=broken)

    assert result.status_code == 401
    assert relay.calls == []


@pytest.mark.parametrize("body", [b"not json", b'["a", "list"]', b'"a string"', b"\xff\xfe"])
async def test_non_object_json_is_400(body):
    relay = FakeRelay()

    result = await run(body, signed_headers(body), relay, verify=lambda b, h: True)

    assert result.status_code == 400
    assert relay.calls == []


async def test_mapper_raising_is_422():
    relay = FakeRelay()
    body = raw({"unexpected": "shape"})

    result = await run(body, signed_headers(body), relay)  # map_razorpay -> KeyError

    assert result.status_code == 422
    assert relay.calls == []


@pytest.mark.parametrize(
    "bad",
    ["payment.captured", ("only-one",), ("", {"a": 1}), ("t", "not-a-dict"), (1, {}), ["t", {}]],
)
async def test_invalid_mapper_result_is_422(bad):
    relay = FakeRelay()
    body = raw()

    result = await run(body, signed_headers(body), relay, map_event=lambda b, h: bad)

    assert result.status_code == 422
    assert relay.calls == []


async def test_mapper_returning_none_is_200_and_publishes_nothing():
    relay = FakeRelay()
    body = raw({**RAZORPAY_BODY, "event": "payment.failed"})

    result = await run(body, signed_headers(body), relay)

    assert result.status_code == 200
    assert result.body == {"ok": True, "ignored": True}
    assert relay.calls == []


async def test_relay_api_error_is_502():
    relay = FakeRelay(RelayAPIError(503, "Kafka publish failed"))
    body = raw()

    result = await run(body, signed_headers(body), relay)

    assert result.status_code == 502
    assert "event_id" not in result.body


async def test_relay_connection_error_is_502():
    relay = FakeRelay(RelayConnectionError("refused"))
    body = raw()

    result = await run(body, signed_headers(body), relay)

    assert result.status_code == 502


async def test_oversized_body_is_413_before_any_verification():
    relay = FakeRelay()
    verify_calls = []

    def verify(raw_body, headers):
        verify_calls.append(1)
        return True

    result = await run(b"x" * 11, {}, relay, verify=verify, max_body_bytes=10)

    assert result.status_code == 413
    assert verify_calls == []


async def test_error_bodies_never_echo_request_data():
    relay = FakeRelay(RelayAPIError(401, "Invalid API key"))
    body = raw()

    result = await run(body, signed_headers(body), relay)

    text = json.dumps(result.body)
    assert "pay_123" not in text and SECRET not in text and "Invalid API key" not in text


# ---------- dedupe ----------


def provider_event_id(body, headers):
    return headers.get("x-razorpay-event-id")


async def test_duplicate_provider_event_is_published_once():
    relay = FakeRelay()
    dedupe = InMemoryDedupe()
    body = raw()
    headers = {**signed_headers(body), "X-Razorpay-Event-Id": "evt_abc"}

    first = await run(body, headers, relay, dedupe_key=provider_event_id, dedupe=dedupe)
    second = await run(body, headers, relay, dedupe_key=provider_event_id, dedupe=dedupe)

    assert first.status_code == 200 and "duplicate" not in first.body
    assert second.status_code == 200 and second.body == {"ok": True, "duplicate": True}
    assert len(relay.calls) == 1


async def test_failed_publish_is_not_remembered_so_the_retry_goes_through():
    dedupe = InMemoryDedupe()
    body = raw()
    headers = {**signed_headers(body), "X-Razorpay-Event-Id": "evt_abc"}

    failing = FakeRelay(RelayAPIError(503, "down"))
    first = await run(body, headers, failing, dedupe_key=provider_event_id, dedupe=dedupe)
    assert first.status_code == 502

    healthy = FakeRelay()
    retry = await run(body, headers, healthy, dedupe_key=provider_event_id, dedupe=dedupe)

    assert retry.status_code == 200 and "duplicate" not in retry.body
    assert len(healthy.calls) == 1


async def test_missing_dedupe_key_skips_the_check():
    relay = FakeRelay()
    dedupe = InMemoryDedupe()
    body = raw()
    headers = signed_headers(body)  # no event-id header -> key is None

    await run(body, headers, relay, dedupe_key=provider_event_id, dedupe=dedupe)
    await run(body, headers, relay, dedupe_key=provider_event_id, dedupe=dedupe)

    assert len(relay.calls) == 2


async def test_dedupe_key_without_a_store_is_rejected():
    with pytest.raises(ValueError):
        await run(raw(), {}, FakeRelay(), dedupe_key=provider_event_id)


async def test_in_memory_dedupe_evicts_oldest_first():
    store = InMemoryDedupe(max_size=2)
    for key in ("a", "b", "c"):
        await store.remember(key)

    assert not await store.seen("a")
    assert await store.seen("b") and await store.seen("c")


def test_in_memory_dedupe_rejects_bad_size():
    with pytest.raises(ValueError):
        InMemoryDedupe(max_size=0)


# ---------- FastAPI router ----------


def make_client(relay, **kwargs) -> TestClient:
    app = FastAPI()
    app.include_router(
        inbound_router(
            path="/razorpay/webhook",
            verify=hmac_sha256_hex(SECRET, SIG_HEADER),
            map_event=map_razorpay,
            relay=relay,
            **kwargs,
        )
    )
    return TestClient(app)


def test_router_happy_path():
    relay = FakeRelay()
    body = raw()

    response = make_client(relay).post(
        "/razorpay/webhook", content=body, headers=signed_headers(body)
    )

    assert response.status_code == 200
    assert response.json() == {"ok": True, "event_id": "evt-1"}
    assert relay.calls[0][0] == "payment.captured"


def test_router_rejects_bad_signature():
    relay = FakeRelay()

    response = make_client(relay).post(
        "/razorpay/webhook", content=raw(), headers={SIG_HEADER: "nope"}
    )

    assert response.status_code == 401
    assert relay.calls == []


def test_router_rejects_missing_signature():
    response = make_client(FakeRelay()).post("/razorpay/webhook", content=raw())

    assert response.status_code == 401


def test_router_rejects_oversized_body_from_content_length():
    relay = FakeRelay()
    body = raw()

    response = make_client(relay, max_body_bytes=10).post(
        "/razorpay/webhook", content=body, headers=signed_headers(body)
    )

    assert response.status_code == 413
    assert relay.calls == []


def test_router_uses_in_memory_dedupe_when_only_a_key_function_is_given():
    relay = FakeRelay()
    client = make_client(relay, dedupe_key=provider_event_id)
    body = raw()
    headers = {**signed_headers(body), "X-Razorpay-Event-Id": "evt_abc"}

    first = client.post("/razorpay/webhook", content=body, headers=headers)
    second = client.post("/razorpay/webhook", content=body, headers=headers)

    assert first.json().get("duplicate") is None
    assert second.json() == {"ok": True, "duplicate": True}
    assert len(relay.calls) == 1


def test_router_end_to_end_with_async_relay_client_and_api_key():
    seen: Dict[str, Any] = {}

    def relay_api(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["api_key"] = request.headers.get("X-API-Key")
        seen["body"] = json.loads(request.content)
        return httpx.Response(202, json={"event_id": "evt-real"})

    relay = AsyncRelayClient(
        base_url="http://relay.test", transport=httpx.MockTransport(relay_api), api_key="team-a-key"
    )
    body = raw()

    response = make_client(relay).post(
        "/razorpay/webhook", content=body, headers=signed_headers(body)
    )

    assert response.status_code == 200
    assert response.json() == {"ok": True, "event_id": "evt-real"}
    assert seen["path"] == "/events"
    assert seen["api_key"] == "team-a-key"
    assert seen["body"] == {
        "event_type": "payment.captured",
        "payload": {"payment_id": "pay_123", "amount": 49900, "currency": "INR"},
    }


def test_router_returns_502_when_relay_rejects_the_event():
    def relay_api(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"detail": "Kafka publish failed"})

    relay = AsyncRelayClient(transport=httpx.MockTransport(relay_api))
    body = raw()

    response = make_client(relay).post(
        "/razorpay/webhook", content=body, headers=signed_headers(body)
    )

    assert response.status_code == 502
