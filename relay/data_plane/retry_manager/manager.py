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
from relay.kernel.entities.event import Event
from relay.kernel.policies.delivery_policy import (
    DeliveryPolicy,
    IsolationAction,
    RetryPolicy,
)

# Matches the Dispatcher: only QUARANTINE stops HTTP (ADR 0022).
_SKIP_HTTP = frozenset({IsolationAction.QUARANTINE})


class RetryWork(BaseModel):
    """
    Kafka payload for `events.internal.delivery`. Not a kernel type.

    Carries the Event because the retry POST must reproduce the exact
    same request body as the first attempt, and the Dispatcher consuming
    this message has no other handle on it.
    """

    model_config = ConfigDict(frozen=True)

    next_attempt_at: datetime
    attempt: DeliveryAttempt
    delivery: Delivery
    policy: DeliveryPolicy
    event: Event


def exponential_delay_seconds(
    retry_policy: RetryPolicy, failed_attempt_number: int
) -> float:
    delay = retry_policy.initial_delay_seconds * (
        retry_policy.backoff_multiplier ** (failed_attempt_number - 1)
    )
    return min(delay, retry_policy.max_delay_seconds)


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


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
        event: Event,
    ) -> Optional[RetryWork]:
        """
        After a worker attempt: SUCCESS, schedule a retry, or mark FAILED.
        QUARANTINE leaves the Delivery durable (PENDING) with no HTTP.
        """
        if completed.success:
            await self._set_status(delivery, "SUCCESS")
            return None

        if policy.isolation_policy.action in _SKIP_HTTP:
            return None

        if completed.attempt_number >= policy.retry_policy.max_retries:
            await self._set_status(delivery, "FAILED")
            return None

        now = datetime.now(timezone.utc)
        delay = exponential_delay_seconds(
            policy.retry_policy, completed.attempt_number
        )
        next_attempt_at = now + timedelta(seconds=delay)

        # deadline_seconds is an absolute budget across all retries and
        # overrides max_retries when it expires first.
        deadline_seconds = policy.retry_policy.deadline_seconds
        if deadline_seconds is not None:
            expires_at = _as_utc(delivery.created_at) + timedelta(
                seconds=deadline_seconds
            )
            if next_attempt_at > expires_at:
                await self._set_status(delivery, "FAILED")
                return None

        return await self._schedule(
            completed, delivery, policy, event, next_attempt_at
        )

    async def _schedule(
        self,
        completed: DeliveryAttempt,
        delivery: Delivery,
        policy: DeliveryPolicy,
        event: Event,
        next_attempt_at: datetime,
    ) -> Optional[RetryWork]:
        next_number = completed.attempt_number + 1
        candidate = DeliveryAttempt(
            delivery_id=delivery.delivery_id,
            attempt_number=next_number,
            started_at=next_attempt_at,
        )
        next_attempt = await self._attempts.insert_if_absent(candidate)
        if next_attempt is None:
            # A redelivery of the same failed attempt already claimed this
            # slot. Reuse it if it is still in flight; stand down if it
            # already ran, so the same retry is not executed twice.
            existing = await self._attempts.get_by_delivery_and_number(
                delivery.delivery_id, next_number
            )
            if existing is None or existing.completed_at is not None:
                return None
            next_attempt = existing

        work = RetryWork(
            next_attempt_at=next_attempt_at,
            attempt=next_attempt,
            delivery=delivery,
            policy=policy,
            event=event,
        )
        await publish(
            EVENTS_INTERNAL_DELIVERY_TOPIC,
            key=delivery.event_id,
            value=work.model_dump_json().encode("utf-8"),
        )
        return work

    async def _set_status(self, delivery: Delivery, status: str) -> None:
        await self._deliveries.save(delivery.model_copy(update={"status": status}))
