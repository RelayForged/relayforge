"""
Stateless webhook Delivery Worker.

POSTs to `endpoint.target_url` (Simulator `POST /webhook`), then persists
the completed DeliveryAttempt and a Telemetry observation. Does not
schedule retries, compute backoff, or select a strategy.
"""
from datetime import datetime, timezone
from time import perf_counter
from typing import Optional, TYPE_CHECKING

import httpx

from relay.infrastructure.persistence.delivery_attempt_repository import (
    DeliveryAttemptRepository,
)
from relay.infrastructure.persistence.telemetry_repository import TelemetryRepository
from relay.kernel.entities.delivery import Delivery, DeliveryAttempt
from relay.kernel.policies.delivery_policy import DeliveryPolicy
from relay.kernel.value_objects.telemetry import Telemetry

if TYPE_CHECKING:
    from relay.data_plane.retry_manager.manager import RetryManager

DEFAULT_TIMEOUT_SECONDS = 5.0


class WebhookWorker:
    def __init__(
        self,
        *,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        attempts: Optional[DeliveryAttemptRepository] = None,
        telemetry: Optional[TelemetryRepository] = None,
        client: Optional[httpx.AsyncClient] = None,
        retry_manager: Optional["RetryManager"] = None,
    ) -> None:
        self._timeout = httpx.Timeout(timeout_seconds)
        self._attempts = attempts or DeliveryAttemptRepository()
        self._telemetry = telemetry or TelemetryRepository()
        self._client = client
        self._retry_manager = retry_manager

    async def execute(
        self,
        attempt: DeliveryAttempt,
        target_url: str,
        endpoint_id: str,
        *,
        delivery: Optional[Delivery] = None,
        policy: Optional[DeliveryPolicy] = None,
    ) -> tuple[DeliveryAttempt, Telemetry]:
        """
        POST once, then persist completed attempt + Telemetry.

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
            response = await self._post(target_url)
            http_status = response.status_code
            if not (200 <= response.status_code < 300):
                error_message = f"HTTP {response.status_code}"
        except httpx.TimeoutException:
            timed_out = True
            error_message = "client timeout"
        except httpx.HTTPError as exc:
            error_message = str(exc)

        latency_ms = (perf_counter() - started) * 1000.0
        completed_at = datetime.now(timezone.utc)
        succeeded = http_status is not None and 200 <= http_status < 300

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
        if (
            self._retry_manager is not None
            and delivery is not None
            and policy is not None
        ):
            await self._retry_manager.handle(completed, delivery, policy)
        return completed, observation

    async def _post(self, target_url: str) -> httpx.Response:
        if self._client is not None:
            return await self._client.post(target_url, timeout=self._timeout)
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            return await client.post(target_url)
