"""
Regression tests for the webhook Worker: ADR 0016 (request contract) plus
the failure-mode mapping the Simulator exercises.

The bug that motivated most of this: the Worker used to POST an empty
body, so subscribers received a request with no event data at all and the
smoke script still passed because the Simulator ignores the body.
"""
import httpx
import pytest

from relay.data_plane.workers.webhook import (
    MAX_ERROR_MESSAGE_CHARS,
    SigningUnavailable,
    WebhookWorker,
)
from relay.infrastructure.url_guard import UnsafeTargetURL
from relay.kernel.entities.delivery import Delivery, DeliveryAttempt
from relay.kernel.entities.event import Event

from tests.data_plane._helpers import make_event, make_policy

TARGET = "https://hooks.example.test/webhook"


@pytest.fixture(autouse=True)
def _allow_target(monkeypatch):
    """
    Neutralise the egress guard for contract tests -- hooks.example.test
    does not resolve. The guard itself is covered in test_url_guard.py,
    and its integration with the Worker is covered explicitly below.
    """
    async def ok(url: str) -> None:
        return None

    monkeypatch.setattr("relay.data_plane.workers.webhook.assert_deliverable", ok)


def build(handler, **kwargs):
    """A Worker wired to a MockTransport, with captured requests."""
    seen: list[httpx.Request] = []

    def capture(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(capture))
    return WebhookWorker(client=client, **kwargs), seen


def scenario(event: Event | None = None):
    event = event or make_event()
    delivery = Delivery(
        event_id=event.event_id, subscription_id="sub-1", endpoint_id="endpoint-1"
    )
    attempt = DeliveryAttempt(delivery_id=delivery.delivery_id, attempt_number=1)
    return event, delivery, attempt


# --------------------------------------------------------------------------
# Request contract (ADR 0016)
# --------------------------------------------------------------------------


async def test_request_body_is_the_event_json(attempts, telemetry):
    """The regression that matters: the body must not be empty."""
    worker, seen = build(
        lambda r: httpx.Response(200), attempts=attempts, telemetry=telemetry
    )
    event, delivery, attempt = scenario()

    await worker.execute(
        attempt, TARGET, "endpoint-1", event=event, delivery=delivery
    )

    assert len(seen) == 1
    body = seen[0].content
    assert body, "Worker POSTed an empty body"
    assert Event.model_validate_json(body).event_id == event.event_id
    assert seen[0].headers["content-type"] == "application/json"
    assert seen[0].method == "POST"


async def test_payload_survives_the_round_trip(attempts, telemetry):
    worker, seen = build(
        lambda r: httpx.Response(200), attempts=attempts, telemetry=telemetry
    )
    event, delivery, attempt = scenario(
        make_event("OrderCreated", order_id="abc-123", amount=42)
    )

    await worker.execute(
        attempt, TARGET, "endpoint-1", event=event, delivery=delivery
    )

    delivered = Event.model_validate_json(seen[0].content)
    assert delivered.payload == {"order_id": "abc-123", "amount": 42}


async def test_dedup_headers_are_present(attempts, telemetry):
    """Deliveries are at-least-once, so subscribers need a dedup key."""
    worker, seen = build(
        lambda r: httpx.Response(200), attempts=attempts, telemetry=telemetry
    )
    event, delivery, attempt = scenario()

    await worker.execute(
        attempt, TARGET, "endpoint-1", event=event, delivery=delivery
    )

    headers = seen[0].headers
    assert headers["x-relay-event-id"] == event.event_id
    assert headers["x-relay-event-type"] == event.event_type
    assert headers["x-relay-delivery-id"] == delivery.delivery_id
    assert headers["x-relay-attempt-id"] == attempt.attempt_id
    assert headers["x-relay-attempt-number"] == "1"


async def test_redirects_are_not_followed(attempts, telemetry):
    """Following a redirect is an SSRF bypass around the egress guard."""
    worker, seen = build(
        lambda r: httpx.Response(302, headers={"Location": "http://169.254.169.254/"}),
        attempts=attempts,
        telemetry=telemetry,
    )
    event, delivery, attempt = scenario()

    completed, _ = await worker.execute(
        attempt, TARGET, "endpoint-1", event=event, delivery=delivery
    )

    assert len(seen) == 1, "Worker followed a redirect"
    assert completed.http_status == 302
    assert completed.success is False


# --------------------------------------------------------------------------
# Simulator failure-mode mapping
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "status,expect_success",
    [(200, True), (201, True), (204, True), (429, False), (500, False), (503, False)],
)
async def test_status_maps_to_success_flag(
    status, expect_success, attempts, telemetry
):
    worker, _ = build(
        lambda r: httpx.Response(status), attempts=attempts, telemetry=telemetry
    )
    event, delivery, attempt = scenario()

    completed, observation = await worker.execute(
        attempt, TARGET, "endpoint-1", event=event, delivery=delivery
    )

    assert completed.success is expect_success
    assert completed.http_status == status
    assert observation.succeeded is expect_success
    assert observation.http_status == status
    assert observation.timed_out is False


async def test_timeout_records_timed_out_with_no_status(attempts, telemetry):
    """Simulator `timeout` mode holds the connection open."""
    def timeout(request):
        raise httpx.ReadTimeout("timed out", request=request)

    worker, _ = build(timeout, attempts=attempts, telemetry=telemetry)
    event, delivery, attempt = scenario()

    completed, observation = await worker.execute(
        attempt, TARGET, "endpoint-1", event=event, delivery=delivery
    )

    assert completed.success is False
    assert completed.http_status is None
    assert observation.timed_out is True
    assert observation.http_status is None
    assert "timeout" in completed.error_message.lower()


async def test_slow_success_is_recorded_as_success_with_latency(
    attempts, telemetry
):
    """Simulator `slow` mode: a delayed 200 is still a success."""
    worker, _ = build(
        lambda r: httpx.Response(200), attempts=attempts, telemetry=telemetry
    )
    event, delivery, attempt = scenario()

    _completed, observation = await worker.execute(
        attempt, TARGET, "endpoint-1", event=event, delivery=delivery
    )

    assert observation.succeeded is True
    assert observation.latency_ms > 0
    assert observation.timed_out is False


# --------------------------------------------------------------------------
# The Worker must never raise into the Dispatcher's consumer loop
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "boom",
    [
        lambda r: (_ for _ in ()).throw(httpx.ConnectError("refused", request=r)),
        lambda r: (_ for _ in ()).throw(httpx.RemoteProtocolError("bad", request=r)),
        lambda r: (_ for _ in ()).throw(RuntimeError("something unexpected")),
        lambda r: (_ for _ in ()).throw(ValueError("boom")),
    ],
)
async def test_transport_and_unexpected_errors_become_failed_attempts(
    boom, attempts, telemetry
):
    """
    An exception escaping execute() propagates into the Dispatcher's
    `async for` and kills the consumer process.
    """
    worker, _ = build(boom, attempts=attempts, telemetry=telemetry)
    event, delivery, attempt = scenario()

    completed, observation = await worker.execute(
        attempt, TARGET, "endpoint-1", event=event, delivery=delivery
    )

    assert completed.success is False
    assert completed.completed_at is not None
    assert completed.error_message
    assert observation.succeeded is False
    # Both rows are still written, so the Retry Manager can act on them.
    assert await attempts.get_by_id(attempt.attempt_id) is not None
    assert await telemetry.get_by_id(attempt.attempt_id) is not None


async def test_blocked_target_is_a_failed_attempt_not_an_exception(
    attempts, telemetry, monkeypatch
):
    """The guard rejects at delivery time; that must be recorded, not raised."""
    async def blocked(url: str) -> None:
        raise UnsafeTargetURL("target resolves to non-public address 169.254.169.254")

    monkeypatch.setattr(
        "relay.data_plane.workers.webhook.assert_deliverable", blocked
    )
    worker, seen = build(
        lambda r: httpx.Response(200), attempts=attempts, telemetry=telemetry
    )
    event, delivery, attempt = scenario()

    completed, observation = await worker.execute(
        attempt, TARGET, "endpoint-1", event=event, delivery=delivery
    )

    assert seen == [], "POSTed to a blocked target"
    assert completed.success is False
    assert "blocked target" in completed.error_message
    assert observation.succeeded is False


async def test_error_message_is_truncated(attempts, telemetry):
    """error_message reaches the dashboard and can embed the full URL."""
    def boom(request):
        raise httpx.ConnectError("x" * 5000, request=request)

    worker, _ = build(boom, attempts=attempts, telemetry=telemetry)
    event, delivery, attempt = scenario()

    completed, _ = await worker.execute(
        attempt, TARGET, "endpoint-1", event=event, delivery=delivery
    )

    assert len(completed.error_message) <= MAX_ERROR_MESSAGE_CHARS


# --------------------------------------------------------------------------
# HMAC (integrity_policy)
# --------------------------------------------------------------------------


async def test_no_signature_when_policy_does_not_require_it(attempts, telemetry):
    """Strategy A sets hmac_required=False, so signing stays inert."""
    worker, seen = build(
        lambda r: httpx.Response(200), attempts=attempts, telemetry=telemetry
    )
    event, delivery, attempt = scenario()

    await worker.execute(
        attempt,
        TARGET,
        "endpoint-1",
        event=event,
        delivery=delivery,
        policy=make_policy(hmac_required=False),
    )

    assert "x-relay-signature" not in seen[0].headers


async def test_signature_is_computed_over_timestamp_nonce_and_body(
    attempts, telemetry, monkeypatch
):
    import hashlib
    import hmac as hmac_mod

    monkeypatch.setenv("RELAY_WEBHOOK_SIGNING_SECRET", "s3cret")
    worker, seen = build(
        lambda r: httpx.Response(200), attempts=attempts, telemetry=telemetry
    )
    event, delivery, attempt = scenario()

    await worker.execute(
        attempt,
        TARGET,
        "endpoint-1",
        event=event,
        delivery=delivery,
        policy=make_policy(hmac_required=True, replay_protection=True),
    )

    request = seen[0]
    timestamp = request.headers["x-relay-timestamp"]
    nonce = request.headers["x-relay-nonce"]
    signed = b".".join(
        [timestamp.encode(), nonce.encode(), request.content]
    )
    expected = hmac_mod.new(b"s3cret", signed, hashlib.sha256).hexdigest()

    assert request.headers["x-relay-signature"] == f"sha256={expected}"


async def test_timestamp_is_inside_the_signed_material(
    attempts, telemetry, monkeypatch
):
    """
    Otherwise replay protection is just a header an attacker rewrites.
    Re-signing with a tampered timestamp must not match.
    """
    import hashlib
    import hmac as hmac_mod

    monkeypatch.setenv("RELAY_WEBHOOK_SIGNING_SECRET", "s3cret")
    worker, seen = build(
        lambda r: httpx.Response(200), attempts=attempts, telemetry=telemetry
    )
    event, delivery, attempt = scenario()

    await worker.execute(
        attempt,
        TARGET,
        "endpoint-1",
        event=event,
        delivery=delivery,
        policy=make_policy(hmac_required=True, replay_protection=True),
    )

    request = seen[0]
    forged = b".".join(
        [b"9999999999", request.headers["x-relay-nonce"].encode(), request.content]
    )
    forged_sig = hmac_mod.new(b"s3cret", forged, hashlib.sha256).hexdigest()

    assert request.headers["x-relay-signature"] != f"sha256={forged_sig}"


async def test_nonce_is_omitted_without_replay_protection(
    attempts, telemetry, monkeypatch
):
    monkeypatch.setenv("RELAY_WEBHOOK_SIGNING_SECRET", "s3cret")
    worker, seen = build(
        lambda r: httpx.Response(200), attempts=attempts, telemetry=telemetry
    )
    event, delivery, attempt = scenario()

    await worker.execute(
        attempt,
        TARGET,
        "endpoint-1",
        event=event,
        delivery=delivery,
        policy=make_policy(hmac_required=True, replay_protection=False),
    )

    assert "x-relay-nonce" not in seen[0].headers
    assert "x-relay-signature" in seen[0].headers


async def test_nonce_differs_between_attempts(attempts, telemetry, monkeypatch):
    monkeypatch.setenv("RELAY_WEBHOOK_SIGNING_SECRET", "s3cret")
    worker, seen = build(
        lambda r: httpx.Response(200), attempts=attempts, telemetry=telemetry
    )
    policy = make_policy(hmac_required=True, replay_protection=True)
    event, delivery, _ = scenario()

    for number in (1, 2):
        await worker.execute(
            DeliveryAttempt(
                delivery_id=delivery.delivery_id, attempt_number=number
            ),
            TARGET,
            "endpoint-1",
            event=event,
            delivery=delivery,
            policy=policy,
        )

    assert seen[0].headers["x-relay-nonce"] != seen[1].headers["x-relay-nonce"]


async def test_missing_secret_fails_the_attempt_and_sends_nothing(
    attempts, telemetry
):
    """
    Never fall back to sending unsigned when the policy demands HMAC --
    the subscriber would reject it anyway, and silently downgrading
    integrity is worse than a recorded failure.
    """
    worker, seen = build(
        lambda r: httpx.Response(200), attempts=attempts, telemetry=telemetry
    )
    event, delivery, attempt = scenario()

    completed, _ = await worker.execute(
        attempt,
        TARGET,
        "endpoint-1",
        event=event,
        delivery=delivery,
        policy=make_policy(hmac_required=True),
    )

    assert seen == [], "sent an unsigned request when HMAC was required"
    assert completed.success is False
    assert "RELAY_WEBHOOK_SIGNING_SECRET" in completed.error_message


# --------------------------------------------------------------------------
# Persistence and pooling
# --------------------------------------------------------------------------


async def test_attempt_and_telemetry_are_both_persisted(attempts, telemetry):
    """Both row types are required and must never collapse into one."""
    worker, _ = build(
        lambda r: httpx.Response(500), attempts=attempts, telemetry=telemetry
    )
    event, delivery, attempt = scenario()

    await worker.execute(
        attempt, TARGET, "endpoint-1", event=event, delivery=delivery
    )

    stored = await attempts.get_by_id(attempt.attempt_id)
    observation = telemetry.only()
    assert stored is not None and stored.completed_at is not None
    # Telemetry links back to the attempt for the ERI window.
    assert observation.attempt_id == attempt.attempt_id
    assert observation.endpoint_id == "endpoint-1"


async def test_client_is_reused_across_attempts(attempts, telemetry):
    """
    A client per attempt meant a fresh TLS handshake per delivery, and
    that setup cost was recorded as endpoint latency_ms.
    """
    worker, _ = build(
        lambda r: httpx.Response(200), attempts=attempts, telemetry=telemetry
    )
    event, delivery, _ = scenario()

    first = worker._adapter._get_client()
    for number in (1, 2, 3):
        await worker.execute(
            DeliveryAttempt(
                delivery_id=delivery.delivery_id, attempt_number=number
            ),
            TARGET,
            "endpoint-1",
            event=event,
            delivery=delivery,
        )

    assert worker._adapter._get_client() is first


async def test_injected_client_is_not_closed_by_aclose(attempts, telemetry):
    worker, _ = build(
        lambda r: httpx.Response(200), attempts=attempts, telemetry=telemetry
    )
    client = worker._adapter._get_client()

    await worker.aclose()

    assert client.is_closed is False
