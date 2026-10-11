"""
The Worker talks to a DeliveryAdapter, not to an HTTP library, and the
webhook adapter maps HTTP outcomes onto that contract.
"""
import httpx
import pytest

from relay.adapters.webhook import WebhookDeliveryAdapter
from relay.data_plane.workers.webhook import WebhookWorker
from relay.kernel.entities.delivery import Delivery, DeliveryAttempt
from relay.kernel.interfaces.delivery_adapter import (
    DeliveryAdapter,
    DeliveryResponse,
    DeliveryTimeout,
    DeliveryTransportError,
)

from tests.data_plane._helpers import make_event

TARGET = "https://hooks.example.test/webhook"


@pytest.fixture(autouse=True)
def _allow_target(monkeypatch):
    async def ok(url: str) -> None:
        return None

    monkeypatch.setattr("relay.data_plane.workers.webhook.assert_deliverable", ok)


class FakeAdapter:
    """A transport with no HTTP in it at all."""

    def __init__(self, outcome) -> None:
        self.outcome = outcome
        self.sent: list[tuple[str, bytes, dict]] = []
        self.closed = False

    async def deliver(self, target_url, body, headers) -> DeliveryResponse:
        self.sent.append((target_url, body, dict(headers)))
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome

    async def aclose(self) -> None:
        self.closed = True


def scenario():
    event = make_event("OrderCreated")
    delivery = Delivery(
        event_id=event.event_id, subscription_id="sub-1", endpoint_id="endpoint-1"
    )
    attempt = DeliveryAttempt(delivery_id=delivery.delivery_id, attempt_number=1)
    return event, delivery, attempt


async def run(adapter, attempts, telemetry):
    worker = WebhookWorker(adapter=adapter, attempts=attempts, telemetry=telemetry)
    event, delivery, attempt = scenario()
    completed, observation = await worker.execute(
        attempt, TARGET, "endpoint-1", event=event, delivery=delivery
    )
    return worker, event, completed, observation


def test_adapters_satisfy_the_protocol() -> None:
    assert isinstance(WebhookDeliveryAdapter(), DeliveryAdapter)
    assert isinstance(FakeAdapter(DeliveryResponse(status_code=200)), DeliveryAdapter)


async def test_worker_sends_through_the_injected_adapter(attempts, telemetry) -> None:
    adapter = FakeAdapter(DeliveryResponse(status_code=204))

    _, event, completed, observation = await run(adapter, attempts, telemetry)

    target, body, headers = adapter.sent[0]
    assert target == TARGET
    assert body == event.model_dump_json().encode("utf-8")
    assert headers["X-Relay-Event-Id"] == event.event_id
    assert completed.success is True and completed.http_status == 204
    assert observation.succeeded is True


async def test_error_status_is_a_response_not_an_exception(attempts, telemetry) -> None:
    _, _, completed, observation = await run(
        FakeAdapter(DeliveryResponse(status_code=503)), attempts, telemetry
    )

    assert completed.success is False
    assert completed.http_status == 503
    assert completed.error_message == "HTTP 503"
    assert observation.timed_out is False


async def test_adapter_timeout_is_recorded_as_timed_out(attempts, telemetry) -> None:
    _, _, completed, observation = await run(
        FakeAdapter(DeliveryTimeout("client timeout")), attempts, telemetry
    )

    assert completed.success is False
    assert completed.http_status is None
    assert observation.timed_out is True


async def test_adapter_transport_error_is_recorded_not_raised(attempts, telemetry) -> None:
    _, _, completed, observation = await run(
        FakeAdapter(DeliveryTransportError("ConnectError: refused")), attempts, telemetry
    )

    assert completed.success is False
    assert completed.error_message == "ConnectError: refused"
    assert observation.timed_out is False


async def test_worker_aclose_closes_the_adapter(attempts, telemetry) -> None:
    adapter = FakeAdapter(DeliveryResponse(status_code=200))
    worker, _, _, _ = await run(adapter, attempts, telemetry)

    await worker.aclose()

    assert adapter.closed is True


# --------------------------------------------------------------------------
# Webhook adapter
# --------------------------------------------------------------------------


def webhook_adapter(handler) -> WebhookDeliveryAdapter:
    return WebhookDeliveryAdapter(
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )


async def test_webhook_adapter_posts_body_and_headers() -> None:
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(202)

    response = await webhook_adapter(handler).deliver(
        TARGET, b'{"a":1}', {"X-Relay-Event-Id": "e-1"}
    )

    assert response == DeliveryResponse(status_code=202)
    assert seen[0].method == "POST"
    assert seen[0].content == b'{"a":1}'
    assert seen[0].headers["X-Relay-Event-Id"] == "e-1"


async def test_webhook_adapter_maps_timeouts() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    with pytest.raises(DeliveryTimeout):
        await webhook_adapter(handler).deliver(TARGET, b"{}", {})


@pytest.mark.parametrize(
    "error", [httpx.ConnectError("refused"), httpx.RemoteProtocolError("reset")]
)
async def test_webhook_adapter_maps_transport_errors(error) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise error

    with pytest.raises(DeliveryTransportError) as raised:
        await webhook_adapter(handler).deliver(TARGET, b"{}", {})

    assert type(error).__name__ in str(raised.value)


async def test_webhook_adapter_does_not_follow_redirects() -> None:
    adapter = WebhookDeliveryAdapter()
    try:
        assert adapter._get_client().follow_redirects is False
    finally:
        await adapter.aclose()


def test_worker_module_does_not_import_httpx_at_runtime() -> None:
    import relay.data_plane.workers.webhook as worker_module

    assert not hasattr(worker_module, "httpx")
