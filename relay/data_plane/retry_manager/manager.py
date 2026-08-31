"""
Retry Manager — schedule the next DeliveryAttempt from DeliveryPolicy fields.

Does not select a strategy, compute ERI, or sleep. Exponential delay is
`initial_delay_seconds * backoff_multiplier ** (attempt_number - 1)`,
capped at `max_delay_seconds` (Strategy A fields, not a reimplementation
of Strategy A). The wait until `next_attempt_at` is encoded on the Kafka
payload; this module never calls asyncio.sleep.
"""
from datetime import datetime, timedelta, timezone
from typing import Optional

from pydantic import BaseModel, ConfigDict

from relay.infrastructure.messaging.consumer import EVENTS_INTERNAL_DELIVERY_TOPIC
from relay.infrastructure.messaging.producer import publish
from relay.infrastructure.persistence.delivery_attempt_repository import (
    DeliveryAttemptRepository,
)
from relay.infrastructure.persistence.delivery_repository import DeliveryRepository
from relay.kernel.entities.delivery import Delivery, DeliveryAttempt
from relay.kernel.policies.delivery_policy import (
    DeliveryPolicy,
    IsolationAction,
    RetryPolicy,
)

_SKIP_HTTP = frozenset({IsolationAction.PAUSE, IsolationAction.QUARANTINE})


class RetryWork(BaseModel):
    """Kafka payload for `events.internal.delivery`. Not a kernel type."""

    model_config = ConfigDict(frozen=True)

    next_attempt_at: datetime
    attempt: DeliveryAttempt
    delivery: Delivery
    policy: DeliveryPolicy


def exponential_delay_seconds(
    retry_policy: RetryPolicy, failed_attempt_number: int
) -> float:
    delay = retry_policy.initial_delay_seconds * (
        retry_policy.backoff_multiplier ** (failed_attempt_number - 1)
    )
    return min(delay, retry_policy.max_delay_seconds)


class RetryManager:
    def __init__(
        self,
        *,
        deliveries: Optional[DeliveryRepository] = None,
        attempts: Optional[DeliveryAttemptRepository] = None,
    ) -> None:
        self._deliveries = deliveries or DeliveryRepository()
        self._attempts = attempts or DeliveryAttemptRepository()

    async def handle(
        self,
        completed: DeliveryAttempt,
        delivery: Delivery,
        policy: DeliveryPolicy,
    ) -> Optional[RetryWork]:
        """
        After a worker attempt: SUCCESS, schedule a retry, or mark FAILED.
        PAUSE / QUARANTINE leaves the Delivery durable (PENDING) with no HTTP.
        """
        if completed.success:
            await self._set_status(delivery, "SUCCESS")
            return None

        if policy.isolation_policy.action in _SKIP_HTTP:
            return None

        if completed.attempt_number >= policy.retry_policy.max_retries:
            await self._set_status(delivery, "FAILED")
            return None

        return await self._schedule(completed, delivery, policy)

    async def _schedule(
        self,
        completed: DeliveryAttempt,
        delivery: Delivery,
        policy: DeliveryPolicy,
    ) -> RetryWork:
        next_number = completed.attempt_number + 1
        existing = await self._attempts.list_by_delivery_id(delivery.delivery_id)
        already = next((row for row in existing if row.attempt_number == next_number), None)
        now = datetime.now(timezone.utc)
        delay = exponential_delay_seconds(
            policy.retry_policy, completed.attempt_number
        )
        next_attempt_at = now + timedelta(seconds=delay)

        if already is None:
            next_attempt = DeliveryAttempt(
                delivery_id=delivery.delivery_id,
                attempt_number=next_number,
                started_at=next_attempt_at,
            )
            await self._attempts.save(next_attempt)
        else:
            next_attempt = already

        work = RetryWork(
            next_attempt_at=next_attempt_at,
            attempt=next_attempt,
            delivery=delivery,
            policy=policy,
        )
        await publish(
            EVENTS_INTERNAL_DELIVERY_TOPIC,
            key=delivery.event_id,
            value=work.model_dump_json().encode("utf-8"),
        )
        return work

    async def _set_status(self, delivery: Delivery, status: str) -> None:
        await self._deliveries.save(delivery.model_copy(update={"status": status}))
