"""
Stateless webhook Delivery Worker.

POSTs the Event to `endpoint.target_url` (Simulator `POST /webhook`), then
persists the completed DeliveryAttempt and a Telemetry observation. Does
not schedule retries, compute backoff, or select a strategy.

The send itself goes through an injected DeliveryAdapter
(relay/kernel/interfaces/delivery_adapter.py). The Worker builds, signs
and records; it does not know which transport carries the request.

The Worker executes `policy.integrity_policy` mechanically; it never
decides whether signing is required. Strategy A sets hmac_required=False,
so signing is inert in Week 3-4 and becomes active the moment a policy
turns it on.
"""
import logging
import secrets
from datetime import datetime, timezone
from time import perf_counter
from typing import Optional, TYPE_CHECKING

from relay.adapters.webhook.adapter import (
    DEFAULT_TIMEOUT_SECONDS,
    WebhookDeliveryAdapter,
)
from relay.infrastructure.auth.signing import (
    NONCE_HEADER,
    SIGNATURE_HEADER,
    SIGNING_SECRET_ENV,
    TIMESTAMP_HEADER,
    compute_relay_signature,
    signing_secret_from_env,
)
from relay.infrastructure.persistence.delivery_attempt_repository import (
    DeliveryAttemptRepository,
)
from relay.infrastructure.persistence.telemetry_repository import TelemetryRepository
from relay.infrastructure.url_guard import UnsafeTargetURL, assert_deliverable
from relay.kernel.entities.delivery import Delivery, DeliveryAttempt
from relay.kernel.entities.event import Event
from relay.kernel.interfaces.delivery_adapter import (
    DeliveryAdapter,
    DeliveryTimeout,
    DeliveryTransportError,
)
from relay.kernel.policies.delivery_policy import DeliveryPolicy
from relay.kernel.value_objects.telemetry import Telemetry

if TYPE_CHECKING:
    import httpx

    from relay.control_plane.aede.service import AEDEService
    from relay.data_plane.retry_manager.manager import RetryManager

logger = logging.getLogger(__name__)

# error_message is persisted and surfaced through the dashboard adapter;
# transport errors can embed the full target URL and resolved host.
MAX_ERROR_MESSAGE_CHARS = 500


class SigningUnavailable(RuntimeError):
    """policy.integrity_policy requires HMAC but no secret is configured."""


class WebhookWorker:
    def __init__(
        self,
        *,
        timeout_seconds: Optional[float] = None,
        attempts: Optional[DeliveryAttemptRepository] = None,
        telemetry: Optional[TelemetryRepository] = None,
        client: Optional["httpx.AsyncClient"] = None,
        adapter: Optional[DeliveryAdapter] = None,
        retry_manager: Optional["RetryManager"] = None,
        aede_service: Optional["AEDEService"] = None,
    ) -> None:
        # The Dispatcher injects the adapter. `timeout_seconds` and `client`
        # remain for callers that predate it and configure the default
        # webhook adapter; they are ignored when an adapter is given.
        self._adapter: DeliveryAdapter = adapter or WebhookDeliveryAdapter(
            timeout_seconds=timeout_seconds, client=client
        )
        self._attempts = attempts or DeliveryAttemptRepository()
        self._telemetry = telemetry or TelemetryRepository()
        self._retry_manager = retry_manager
        self._aede_service = aede_service

    async def execute(
        self,
        attempt: DeliveryAttempt,
        target_url: str,
        endpoint_id: str,
        *,
        event: Event,
        delivery: Optional[Delivery] = None,
        policy: Optional[DeliveryPolicy] = None,
    ) -> tuple[DeliveryAttempt, Telemetry]:
        """
        POST once, then persist completed attempt + Telemetry.

        Every failure mode is recorded as a failed attempt rather than
        raised: an exception escaping here would propagate into the
        Dispatcher's consumer loop and kill the process.

        Simulator mapping:
        - http_error (5xx) → succeeded=False
        - timeout (client timeout) → timed_out=True, http_status=None
        - slow (delayed 200) → succeeded=True, high latency_ms
        - rate_limit (429) → succeeded=False
        """
        http_status: Optional[int] = None
        timed_out = False
        error_message: Optional[str] = None
        started = perf_counter()

        try:
            body, headers = self._build_request(event, attempt, delivery)
            self._apply_signature(body, headers, policy)
            # Re-checked on every attempt, not just at registration: a
            # hostname that was public then can point at link-local
            # metadata now (CWE-918, DNS rebinding).
            await assert_deliverable(target_url)
            response = await self._adapter.deliver(target_url, body, headers)
            http_status = response.status_code
            if not (200 <= http_status < 300):
                error_message = f"HTTP {http_status}"
        except DeliveryTimeout:
            timed_out = True
            error_message = "client timeout"
        except UnsafeTargetURL as exc:
            error_message = f"blocked target: {exc}"
        except SigningUnavailable as exc:
            error_message = str(exc)
        except DeliveryTransportError as exc:
            error_message = str(exc)
        except Exception as exc:  # noqa: BLE001 - see docstring
            logger.exception(
                "Unexpected delivery error attempt_id=%s endpoint_id=%s",
                attempt.attempt_id,
                endpoint_id,
            )
            error_message = f"{type(exc).__name__}: {exc}"

        latency_ms = (perf_counter() - started) * 1000.0
        completed_at = datetime.now(timezone.utc)
        succeeded = http_status is not None and 200 <= http_status < 300
        if error_message is not None:
            error_message = error_message[:MAX_ERROR_MESSAGE_CHARS]

        completed = attempt.model_copy(
            update={
                "completed_at": completed_at,
                "http_status": http_status,
                "success": succeeded,
                "error_message": error_message,
            }
        )
        await self._attempts.save(completed)

        observation = Telemetry(
            attempt_id=attempt.attempt_id,
            endpoint_id=endpoint_id,
            succeeded=succeeded,
            http_status=http_status,
            latency_ms=latency_ms,
            timed_out=timed_out,
            observed_at=completed_at,
        )
        await self._telemetry.save(observation)

        if self._aede_service is not None:
            try:
                await self._aede_service.record_telemetry(observation)
            except Exception:
                logger.exception(
                    "AEDEService telemetry feedback failed for attempt %s endpoint %s",
                    attempt.attempt_id,
                    endpoint_id,
                )

        if (
            self._retry_manager is not None
            and delivery is not None
            and policy is not None
        ):
            await self._retry_manager.handle(completed, delivery, policy, event)
        return completed, observation

    def _build_request(
        self,
        event: Event,
        attempt: DeliveryAttempt,
        delivery: Optional[Delivery],
    ) -> tuple[bytes, dict[str, str]]:
        """
        The Event itself is the request body. Delivery metadata travels in
        headers so the subscriber can deduplicate on X-Relay-Event-Id
        without parsing the body (deliveries are at-least-once).
        """
        body = event.model_dump_json().encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "RelayForge/0.1",
            "X-Relay-Event-Id": event.event_id,
            "X-Relay-Event-Type": event.event_type,
            "X-Relay-Attempt-Id": attempt.attempt_id,
            "X-Relay-Attempt-Number": str(attempt.attempt_number),
        }
        if delivery is not None:
            headers["X-Relay-Delivery-Id"] = delivery.delivery_id
        return body, headers

    def _apply_signature(
        self,
        body: bytes,
        headers: dict[str, str],
        policy: Optional[DeliveryPolicy],
    ) -> None:
        """
        Sign when `integrity_policy.hmac_required`. The timestamp and
        nonce are inside the signed material, otherwise replay protection
        would be a header an attacker can rewrite.
        """
        if policy is None or not policy.integrity_policy.hmac_required:
            return
        secret = signing_secret_from_env()
        if not secret:
            raise SigningUnavailable(
                f"policy requires HMAC but {SIGNING_SECRET_ENV} is not set"
            )
        timestamp = str(int(datetime.now(timezone.utc).timestamp()))
        nonce = (
            secrets.token_hex(16)
            if policy.integrity_policy.replay_protection
            else ""
        )
        headers[TIMESTAMP_HEADER] = timestamp
        headers[SIGNATURE_HEADER] = compute_relay_signature(secret, timestamp, nonce, body)
        if nonce:
            headers[NONCE_HEADER] = nonce

    async def aclose(self) -> None:
        """Release the adapter's resources."""
        await self._adapter.aclose()
