"""
Kafka Dispatcher — data plane consumer process.

Reads `events.business` (ingest) and `events.internal.delivery` (retries).
Persist-first: Kafka offset is committed after Postgres, never after HTTP.
Never imports aiokafka. Week 3–4: Strategy A only.

At-least-once (ADR 0002) is enforced by *when* offsets are committed:
a message is acknowledged only once its work has completed and persisted.
Anything still in flight at shutdown or at a crash stays uncommitted and
is redelivered. Repository writes are idempotent, so redelivery is safe.

Run:
    .venv/bin/python -m relay.data_plane.dispatcher.main
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Optional

from pydantic import ValidationError

from relay.control_plane.aede.strategies.static import StrategyA
from relay.data_plane.retry_manager.manager import RetryManager, RetryWork
from relay.data_plane.workers.webhook import WebhookWorker
from relay.infrastructure.feature_flags import FeatureFlags
from relay.infrastructure.messaging.consumer import (
    EVENTS_BUSINESS_TOPIC,
    EVENTS_INTERNAL_DELIVERY_TOPIC,
    KafkaConsumer,
    KafkaMessage,
)
from relay.infrastructure.persistence.delivery_attempt_repository import (
    DeliveryAttemptRepository,
)
from relay.infrastructure.persistence.delivery_repository import DeliveryRepository
from relay.infrastructure.persistence.endpoint_repository import EndpointRepository
from relay.infrastructure.persistence.subscription_repository import (
    SubscriptionRepository,
)
from relay.kernel.entities.delivery import Delivery, DeliveryAttempt
from relay.kernel.entities.endpoint import Endpoint
from relay.kernel.entities.event import Event
from relay.kernel.entities.subscription import Subscription
from relay.kernel.policies.delivery_policy import DeliveryPolicy, IsolationAction
from relay.kernel.value_objects.endpoint_state import EndpointState
from relay.kernel.value_objects.eri import ERIScore

DISPATCHER_GROUP_ID = "relay-dispatcher"

# Business messages processed concurrently. Bounded so a fan-out burst
# cannot open more DB connections than the pool has, and so blocking on
# the semaphore applies backpressure to polling.
DEFAULT_MAX_CONCURRENT_MESSAGES = 16
# Retries waiting for next_attempt_at. Each holds a RetryWork in memory
# for up to max_delay_seconds.
DEFAULT_MAX_IN_FLIGHT_RETRIES = 500

logger = logging.getLogger(__name__)

_SKIP_HTTP = frozenset({IsolationAction.PAUSE, IsolationAction.QUARANTINE})
_TERMINAL_STATUSES = frozenset({"SUCCESS", "FAILED", "DEAD_LETTER"})


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except ValueError:
        return default


class Dispatcher:
    def __init__(
        self,
        *,
        consumer: Optional[KafkaConsumer] = None,
        subscriptions: Optional[SubscriptionRepository] = None,
        deliveries: Optional[DeliveryRepository] = None,
        attempts: Optional[DeliveryAttemptRepository] = None,
        endpoints: Optional[EndpointRepository] = None,
        worker: Optional[WebhookWorker] = None,
        retry_manager: Optional[RetryManager] = None,
    ) -> None:
        self._consumer = consumer or KafkaConsumer(
            topics=(EVENTS_BUSINESS_TOPIC, EVENTS_INTERNAL_DELIVERY_TOPIC),
            group_id=DISPATCHER_GROUP_ID,
        )
        self._subscriptions = subscriptions or SubscriptionRepository()
        self._deliveries = deliveries or DeliveryRepository()
        self._attempts = attempts or DeliveryAttemptRepository()
        self._endpoints = endpoints or EndpointRepository()
        retries = retry_manager or RetryManager(
            deliveries=self._deliveries,
            attempts=self._attempts,
        )
        self._retry_manager = retries
        self._worker = worker or WebhookWorker(
            attempts=self._attempts,
            retry_manager=retries,
        )

        self._messages: set[asyncio.Task[None]] = set()
        self._scheduled: set[asyncio.Task[None]] = set()
        self._message_slots = asyncio.Semaphore(
            _int_env("DISPATCHER_MAX_CONCURRENT_MESSAGES", DEFAULT_MAX_CONCURRENT_MESSAGES)
        )
        self._retry_slots = asyncio.Semaphore(
            _int_env("DISPATCHER_MAX_IN_FLIGHT_RETRIES", DEFAULT_MAX_IN_FLIGHT_RETRIES)
        )
        self._endpoint_slots: dict[str, asyncio.Semaphore] = {}

        self._strategy = StrategyA()
        self._flags = FeatureFlags.from_env()
        if self._flags.active_strategy_name != self._strategy.strategy_name:
            logger.warning(
                "Feature flags select %s but Week 3-4 forces %s; "
                "set STATIC_RETRY_MODE=true to make the configuration explicit",
                self._flags.active_strategy_name,
                self._strategy.strategy_name,
            )

    async def run(self) -> None:
        await self._consumer.start()
        try:
            async for message in self._consumer:
                await self._accept(message)
        finally:
            await self._drain()
            await self._consumer.close()
            await self._worker.aclose()

    async def _accept(self, message: KafkaMessage) -> None:
        if message.topic == EVENTS_INTERNAL_DELIVERY_TOPIC:
            await self._accept_retry(message)
            return
        await self._message_slots.acquire()
        task = asyncio.create_task(self._run_message(message))
        self._messages.add(task)
        task.add_done_callback(self._messages.discard)

    async def _run_message(self, message: KafkaMessage) -> None:
        try:
            await self.handle_message(message)
        finally:
            self._message_slots.release()

    async def handle_message(self, message: KafkaMessage) -> None:
        try:
            event = Event.model_validate_json(message.value)
        except ValidationError:
            logger.warning(
                "Skipping invalid Kafka payload topic=%s partition=%s offset=%s",
                message.topic,
                message.partition,
                message.offset,
            )
            # Unparseable forever: acknowledge so the partition advances.
            await self._consumer.commit(message)
            return

        try:
            await self._fan_out(event)
        except Exception:
            # Deliberately not committed. The watermark stalls at this
            # offset and the message is redelivered, which is the whole
            # point of at-least-once; losing it would be at-most-once.
            logger.exception(
                "Fan-out failed event_id=%s topic=%s partition=%s offset=%s",
                event.event_id,
                message.topic,
                message.partition,
                message.offset,
            )
            return

        await self._consumer.commit(message)

    async def _accept_retry(self, message: KafkaMessage) -> None:
        try:
            work = RetryWork.model_validate_json(message.value)
        except ValidationError:
            logger.warning(
                "Skipping invalid retry payload partition=%s offset=%s",
                message.partition,
                message.offset,
            )
            await self._consumer.commit(message)
            return

        await self._retry_slots.acquire()
        task = asyncio.create_task(self._run_scheduled(work, message))
        self._scheduled.add(task)
        task.add_done_callback(self._scheduled.discard)

    async def _run_scheduled(self, work: RetryWork, message: KafkaMessage) -> None:
        """
        Wait out the backoff, then execute.

        Sleeping here is sanctioned for the Dispatcher only — the Worker
        and the Retry Manager never sleep. The offset is committed *after*
        the attempt has been executed and persisted, so a crash or a
        shutdown during the sleep redelivers the retry instead of dropping
        it in memory.
        """
        try:
            delay = (work.next_attempt_at - datetime.now(timezone.utc)).total_seconds()
            if delay > 0:
                await asyncio.sleep(delay)
            await self._execute_attempt(
                work.attempt, work.delivery, work.policy, work.event
            )
        except asyncio.CancelledError:
            # Shutdown mid-backoff: leave the offset uncommitted.
            raise
        except Exception:
            logger.exception(
                "Scheduled attempt failed delivery_id=%s attempt_number=%s",
                work.delivery.delivery_id,
                work.attempt.attempt_number,
            )
            return
        else:
            await self._consumer.commit(message)
        finally:
            self._retry_slots.release()

    async def _drain(self) -> None:
        """
        Cancel outstanding work without committing anything.

        Uncommitted offsets are redelivered on restart. Waiting out a
        300 s backoff here would stall shutdown; acknowledging it would
        silently drop a retry.
        """
        tasks = [*self._messages, *self._scheduled]
        if not tasks:
            return
        pending = self._consumer.pending_offsets()
        logger.info(
            "Draining %d in-flight task(s); uncommitted offsets: %s",
            len(tasks),
            pending or "none",
        )
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _fan_out(self, event: Event) -> None:
        targets = await self._subscriptions.list_active_targets_by_event_type(
            event.event_type
        )
        if not targets:
            return
        computed_at = datetime.now(timezone.utc)
        results = await asyncio.gather(
            *(
                self._dispatch_one(event, subscription, endpoint, computed_at)
                for subscription, endpoint in targets
            ),
            return_exceptions=True,
        )
        failures = [r for r in results if isinstance(r, BaseException)]
        if failures:
            # Re-raise so the offset is withheld. Targets that already
            # succeeded are no-ops on redelivery (idempotent saves and the
            # terminal-status guard in _dispatch_one).
            raise failures[0]

    async def _dispatch_one(
        self,
        event: Event,
        subscription: Subscription,
        endpoint: Endpoint,
        computed_at: datetime,
    ) -> None:
        delivery = await self._persist_delivery(event, subscription, computed_at)
        if delivery.status in _TERMINAL_STATUSES:
            # Already resolved by an earlier delivery of this message.
            return

        eri = ERIScore.perfect(window_size=0, computed_at=computed_at)
        policy, _record = self._strategy.evaluate(
            subscription.endpoint_id,
            EndpointState.AVAILABLE,
            eri,
            computed_at,
        )

        if policy.isolation_policy.action in _SKIP_HTTP:
            # Delivery row stays durable in PENDING. No attempt row is
            # written: an attempt that can never complete would block the
            # attempt-1 slot and stop this Delivery from ever dispatching.
            return

        attempt = await self._persist_attempt(delivery.delivery_id, 1, computed_at)
        if attempt is None:
            # Attempt 1 already ran; the retry chain owns this Delivery.
            return

        await self._execute_attempt(
            attempt, delivery, policy, event, endpoint=endpoint
        )

    async def _persist_delivery(
        self,
        event: Event,
        subscription: Subscription,
        computed_at: datetime,
    ) -> Delivery:
        candidate = Delivery(
            event_id=event.event_id,
            subscription_id=subscription.subscription_id,
            endpoint_id=subscription.endpoint_id,
            created_at=computed_at,
            status="PENDING",
        )
        stored = await self._deliveries.save(candidate)
        if stored is not None:
            return stored
        # No-op upsert: the canonical row carries a different delivery_id.
        # One extra read, and only on a redelivery.
        existing = await self._deliveries.get_by_event_and_subscription(
            event.event_id, subscription.subscription_id
        )
        return existing or candidate

    async def _persist_attempt(
        self,
        delivery_id: str,
        attempt_number: int,
        started_at: datetime,
    ) -> Optional[DeliveryAttempt]:
        """
        Claim an attempt slot. Returns None when the attempt already
        completed and must not be re-executed.
        """
        candidate = DeliveryAttempt(
            delivery_id=delivery_id,
            attempt_number=attempt_number,
            started_at=started_at,
        )
        inserted = await self._attempts.insert_if_absent(candidate)
        if inserted is not None:
            return inserted
        existing = await self._attempts.get_by_delivery_and_number(
            delivery_id, attempt_number
        )
        if existing is None or existing.completed_at is not None:
            return None
        return existing

    async def _execute_attempt(
        self,
        attempt: DeliveryAttempt,
        delivery: Delivery,
        policy: DeliveryPolicy,
        event: Event,
        *,
        endpoint: Optional[Endpoint] = None,
    ) -> None:
        if endpoint is None:
            endpoint = await self._endpoints.get_by_id(delivery.endpoint_id)
        if endpoint is None or not endpoint.is_active:
            reason = "endpoint missing" if endpoint is None else "endpoint inactive"
            await self._dead_letter(attempt, delivery, reason)
            return

        slot = self._endpoint_slot(
            delivery.endpoint_id, policy.concurrency_policy.max_parallel
        )
        async with slot:
            await self._worker.execute(
                attempt,
                endpoint.target_url,
                delivery.endpoint_id,
                event=event,
                delivery=delivery,
                policy=policy,
            )

    async def _dead_letter(
        self,
        attempt: DeliveryAttempt,
        delivery: Delivery,
        reason: str,
    ) -> None:
        """
        Terminate a Delivery that no retry can fix.

        A missing or deactivated endpoint is a configuration fact, not an
        endpoint-health signal, so retrying forever would leave the row
        stranded in PENDING with an attempt that never completes.
        """
        logger.warning(
            "Dead-lettering delivery_id=%s: %s", delivery.delivery_id, reason
        )
        await self._attempts.save(
            attempt.model_copy(
                update={
                    "completed_at": datetime.now(timezone.utc),
                    "success": False,
                    "error_message": f"undeliverable: {reason}",
                }
            )
        )
        await self._deliveries.save(
            delivery.model_copy(update={"status": "DEAD_LETTER"})
        )

    def _endpoint_slot(self, endpoint_id: str, max_parallel: int) -> asyncio.Semaphore:
        """
        Honor `concurrency_policy.max_parallel` (Strategy A: 1).

        The bound is fixed at the first policy seen for an endpoint —
        asyncio.Semaphore cannot be resized without dropping held slots.
        Strategy A is constant, so this is exact for Week 3-4.
        """
        slot = self._endpoint_slots.get(endpoint_id)
        if slot is None:
            slot = asyncio.Semaphore(max(1, max_parallel))
            self._endpoint_slots[endpoint_id] = slot
        return slot


async def run() -> None:
    await Dispatcher().run()


def main() -> None:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    asyncio.run(run())


if __name__ == "__main__":
    main()
