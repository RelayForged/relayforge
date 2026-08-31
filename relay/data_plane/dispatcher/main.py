"""
Kafka Dispatcher — data plane consumer process.

Reads `events.business` (ingest) and `events.internal.delivery` (retries).
Persist-first: Kafka offset is committed after Postgres, never after HTTP.
Never imports aiokafka. Week 3–4: Strategy A only.

Run:
    .venv/bin/python -m relay.data_plane.dispatcher.main
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Optional

import logging

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
from relay.kernel.entities.event import Event
from relay.kernel.entities.subscription import Subscription
from relay.kernel.policies.delivery_policy import DeliveryPolicy, IsolationAction
from relay.kernel.value_objects.endpoint_state import EndpointState
from relay.kernel.value_objects.eri import ERIScore

DISPATCHER_GROUP_ID = "relay-dispatcher"
logger = logging.getLogger(__name__)

_SKIP_HTTP = frozenset({IsolationAction.PAUSE, IsolationAction.QUARANTINE})


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
        self._scheduled: set[asyncio.Task[None]] = set()
        self._flags = FeatureFlags.from_env()
        self._strategy = StrategyA()

    async def run(self) -> None:
        await self._consumer.start()
        try:
            async for message in self._consumer:
                await self.handle_message(message)
        finally:
            await self._consumer.close()

    async def handle_message(self, message: KafkaMessage) -> None:
        try:
            if message.topic == EVENTS_INTERNAL_DELIVERY_TOPIC:
                await self.handle_retry(message)
                return
            event = Event.model_validate_json(message.value)
            await self._fan_out(event)
            await self._consumer.commit(message)
        except ValidationError:
            logger.warning(
                "Skipping invalid Kafka payload topic=%s partition=%s offset=%s",
                message.topic,
                message.partition,
                message.offset,
            )
            await self._consumer.commit(message)

    async def handle_retry(self, message: KafkaMessage) -> None:
        work = RetryWork.model_validate_json(message.value)
        await self._consumer.commit(message)
        task = asyncio.create_task(self._run_scheduled(work))
        self._scheduled.add(task)
        task.add_done_callback(self._scheduled.discard)

    async def _run_scheduled(self, work: RetryWork) -> None:
        delay = (work.next_attempt_at - datetime.now(timezone.utc)).total_seconds()
        if delay > 0:
            await asyncio.sleep(delay)
        await self._execute_attempt(work.attempt, work.delivery, work.policy)

    async def _fan_out(self, event: Event) -> None:
        subscriptions = await self._subscriptions.list_active_by_event_type(
            event.event_type
        )
        computed_at = datetime.now(timezone.utc)
        for subscription in subscriptions:
            await self._dispatch_one(event, subscription, computed_at)

    async def _dispatch_one(
        self,
        event: Event,
        subscription: Subscription,
        computed_at: datetime,
    ) -> None:
        delivery, attempt = await self._persist_initial(event, subscription, computed_at)

        eri = ERIScore.perfect(window_size=0, computed_at=computed_at)
        policy, _record = self._strategy.evaluate(
            subscription.endpoint_id,
            EndpointState.AVAILABLE,
            eri,
            computed_at,
        )

        if policy.isolation_policy.action in _SKIP_HTTP:
            return

        await self._execute_attempt(attempt, delivery, policy)

    async def _execute_attempt(
        self,
        attempt: DeliveryAttempt,
        delivery: Delivery,
        policy: DeliveryPolicy,
    ) -> None:
        endpoint = await self._endpoints.get_by_id(delivery.endpoint_id)
        if endpoint is None:
            return
        await self._worker.execute(
            attempt,
            endpoint.target_url,
            delivery.endpoint_id,
            delivery=delivery,
            policy=policy,
        )

    async def _persist_initial(
        self,
        event: Event,
        subscription: Subscription,
        computed_at: datetime,
    ) -> tuple[Delivery, DeliveryAttempt]:
        candidate = Delivery(
            event_id=event.event_id,
            subscription_id=subscription.subscription_id,
            endpoint_id=subscription.endpoint_id,
            created_at=computed_at,
            status="PENDING",
        )
        await self._deliveries.save(candidate)
        delivery = await self._deliveries.get_by_event_and_subscription(
            event.event_id, subscription.subscription_id
        )
        if delivery is None:
            delivery = candidate

        existing = await self._attempts.list_by_delivery_id(delivery.delivery_id)
        attempt = next((row for row in existing if row.attempt_number == 1), None)
        if attempt is None:
            attempt = DeliveryAttempt(
                delivery_id=delivery.delivery_id,
                attempt_number=1,
                started_at=computed_at,
            )
            await self._attempts.save(attempt)
        return delivery, attempt


async def run() -> None:
    await Dispatcher().run()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
