"""
Replay (ADR 0021): the Replay Manager selects and requests, the Dispatcher
executes through its normal path, and history is never rewritten.
"""
from datetime import datetime, timezone

import pytest

from relay.data_plane.dispatcher.main import Dispatcher
from relay.data_plane.replay.manager import ReplayManager, ReplayWork
from relay.infrastructure.cache import InMemoryPolicyCache
from relay.infrastructure.messaging.admin import (
    EVENTS_INTERNAL_REPLAY_TOPIC,
    PLATFORM_TOPICS,
)
from relay.infrastructure.messaging.consumer import KafkaMessage
from relay.infrastructure.persistence.delivery_repository import PARKED, DeadLetter
from relay.kernel.entities.delivery import Delivery, DeliveryAttempt
from relay.kernel.policies.delivery_policy import IsolationAction
from relay.kernel.value_objects.identifiers import replay_event_id

from tests.data_plane._helpers import (
    FakeEndpointRepository,
    FakeSubscriptionRepository,
    make_event,
    make_policy,
    make_target,
)
from tests.data_plane.test_dispatcher_states import RecordingWorker

NOW = datetime(2026, 10, 12, 9, 0, 0, tzinfo=timezone.utc)


class FakeEventRepository:
    def __init__(self, events=()) -> None:
        self.rows = {e.event_id: e for e in events}
        self.saves = 0

    async def save(self, event) -> None:
        self.saves += 1
        self.rows.setdefault(event.event_id, event)

    async def get_by_id(self, event_id: str):
        return self.rows.get(event_id)


class FakeDeadLetterSource:
    """Stands in for DeliveryRepository.get_dead_letters."""

    def __init__(self, dead_letters: list[DeadLetter]) -> None:
        self.dead_letters = dead_letters
        self.queries: list[dict] = []

    async def get_dead_letters(self, *, now, endpoint_id=None, delivery_ids=None, limit=50):
        self.queries.append(
            {"endpoint_id": endpoint_id, "delivery_ids": delivery_ids, "limit": limit}
        )
        items = self.dead_letters
        if endpoint_id is not None:
            items = [i for i in items if i.delivery.endpoint_id == endpoint_id]
        if delivery_ids is not None:
            items = [i for i in items if i.delivery.delivery_id in delivery_ids]
        return items[:limit]


def dead_letter(event, *, reason: str, endpoint_id: str = "endpoint-1") -> DeadLetter:
    status = "PENDING" if reason == PARKED else reason
    return DeadLetter(
        delivery=Delivery(
            event_id=event.event_id,
            subscription_id="subscription-1",
            endpoint_id=endpoint_id,
            created_at=NOW,
            status=status,
        ),
        event_type=event.event_type,
        reason=reason,
        attempt_count=0 if reason == PARKED else 5,
        last_error=None if reason == PARKED else "HTTP 500",
        failed_at=NOW,
    )


def manager_for(dead_letters, events):
    published: list[tuple[str, str, ReplayWork]] = []

    async def publish(topic, *, key, value):
        published.append((topic, key, ReplayWork.model_validate_json(value)))

    event_repo = FakeEventRepository(events)
    manager = ReplayManager(
        deliveries=FakeDeadLetterSource(dead_letters),
        events=event_repo,
        publisher=publish,
    )
    return manager, published, event_repo


# --------------------------------------------------------------------------
# Replay Manager
# --------------------------------------------------------------------------


def test_replay_topic_is_a_platform_topic() -> None:
    assert EVENTS_INTERNAL_REPLAY_TOPIC in PLATFORM_TOPICS


def test_replay_event_id_is_stable_and_distinct() -> None:
    assert replay_event_id("delivery-a") == replay_event_id("delivery-a")
    assert replay_event_id("delivery-a") != replay_event_id("delivery-b")


async def test_parked_delivery_replays_the_original_event() -> None:
    event = make_event("OrderCreated")
    item = dead_letter(event, reason=PARKED)
    manager, published, event_repo = manager_for([item], [event])

    result = await manager.replay_dead_letters(now=NOW)

    assert result.requested == 1 and result.skipped == []
    topic, key, work = published[0]
    assert topic == EVENTS_INTERNAL_REPLAY_TOPIC
    assert key == item.delivery.endpoint_id
    assert work.event == event
    assert work.subscription_id == item.delivery.subscription_id
    assert work.source_delivery_id == item.delivery.delivery_id
    assert event_repo.saves == 0, "a parked replay must not create a new event"


@pytest.mark.parametrize("reason", ["FAILED", "DEAD_LETTER"])
async def test_failed_delivery_replays_as_a_new_event_caused_by_the_original(reason) -> None:
    event = make_event("OrderCreated", order_id="42")
    item = dead_letter(event, reason=reason)
    manager, published, event_repo = manager_for([item], [event])

    await manager.replay_dead_letters(now=NOW)

    replayed = published[0][2].event
    assert replayed.event_id == replay_event_id(item.delivery.delivery_id)
    assert replayed.event_id != event.event_id
    assert replayed.causation_id.value == event.event_id
    assert replayed.correlation_id == event.correlation_id
    assert replayed.event_type == event.event_type
    assert replayed.payload == event.payload
    assert replayed.occurred_at == NOW
    # Persisted before it was published, and the original is untouched.
    assert event_repo.rows[replayed.event_id] == replayed
    assert event_repo.rows[event.event_id] == event


async def test_replaying_twice_produces_the_same_event() -> None:
    event = make_event("OrderCreated")
    item = dead_letter(event, reason="FAILED")
    manager, published, event_repo = manager_for([item], [event])

    await manager.replay_dead_letters(now=NOW)
    await manager.replay_dead_letters(now=NOW)

    assert published[0][2].event.event_id == published[1][2].event.event_id
    assert len(event_repo.rows) == 2


async def test_unknown_delivery_ids_are_reported_as_skipped() -> None:
    event = make_event("OrderCreated")
    item = dead_letter(event, reason="FAILED")
    manager, published, _ = manager_for([item], [event])

    result = await manager.replay_dead_letters(
        now=NOW, delivery_ids=[item.delivery.delivery_id, "not-a-dead-letter"]
    )

    assert result.requested == 1
    assert [s.delivery_id for s in result.skipped] == ["not-a-dead-letter"]
    assert len(published) == 1


async def test_missing_source_event_is_skipped_not_published() -> None:
    event = make_event("OrderCreated")
    item = dead_letter(event, reason="FAILED")
    manager, published, _ = manager_for([item], [])

    result = await manager.replay_dead_letters(now=NOW)

    assert result.requested == 0
    assert result.skipped[0].why == "source event not found"
    assert published == []


async def test_limit_and_endpoint_filter_are_passed_to_the_query() -> None:
    events = [make_event("OrderCreated") for _ in range(3)]
    items = [dead_letter(e, reason=PARKED) for e in events]
    manager, published, _ = manager_for(items, events)

    result = await manager.replay_dead_letters(now=NOW, endpoint_id="endpoint-1", limit=2)

    assert result.requested == 2
    assert manager._deliveries.queries == [
        {"endpoint_id": "endpoint-1", "delivery_ids": None, "limit": 2}
    ]


# --------------------------------------------------------------------------
# Dispatcher
# --------------------------------------------------------------------------


class ReplaySubscriptionRepository(FakeSubscriptionRepository):
    async def get_by_id(self, subscription_id: str):
        return next(
            (s for s, _ in self.targets if s.subscription_id == subscription_id), None
        )


def replay_message(work: ReplayWork, offset: int = 7) -> KafkaMessage:
    return KafkaMessage(
        topic=EVENTS_INTERNAL_REPLAY_TOPIC,
        partition=0,
        offset=offset,
        key=work.source_delivery_id,
        value=work.model_dump_json().encode("utf-8"),
    )


def dispatcher_for(target, deliveries, attempts, consumer, worker, cache=None):
    subscription, endpoint = target
    return Dispatcher(
        consumer=consumer,
        subscriptions=ReplaySubscriptionRepository([target]),
        deliveries=deliveries,
        attempts=attempts,
        endpoints=FakeEndpointRepository([endpoint]),
        worker=worker,
        cache=cache,
    )


async def _drain(dispatcher: Dispatcher) -> None:
    for task in list(dispatcher._messages):
        await task


async def test_parked_delivery_is_dispatched_on_its_existing_row(
    deliveries, attempts, consumer
):
    target = make_target("OrderCreated")
    subscription, endpoint = target
    event = make_event("OrderCreated")
    parked = Delivery(
        event_id=event.event_id,
        subscription_id=subscription.subscription_id,
        endpoint_id=endpoint.endpoint_id,
        status="PENDING",
    )
    await deliveries.save(parked)
    worker = RecordingWorker()
    dispatcher = dispatcher_for(target, deliveries, attempts, consumer, worker)

    work = ReplayWork(
        replay_id="r-1",
        source_delivery_id=parked.delivery_id,
        subscription_id=subscription.subscription_id,
        event=event,
    )
    await dispatcher._accept(replay_message(work))
    await _drain(dispatcher)

    assert len(deliveries.rows) == 1, "replaying a parked delivery must not create another"
    assert len(worker.calls) == 1
    assert worker.calls[0]["delivery"].delivery_id == parked.delivery_id
    assert worker.calls[0]["attempt"].attempt_number == 1
    assert consumer.committed == [7]


async def test_failed_delivery_replay_leaves_original_rows_unchanged(
    deliveries, attempts, consumer
):
    target = make_target("OrderCreated")
    subscription, endpoint = target
    original = make_event("OrderCreated")
    failed = Delivery(
        event_id=original.event_id,
        subscription_id=subscription.subscription_id,
        endpoint_id=endpoint.endpoint_id,
        status="FAILED",
    )
    await deliveries.save(failed)
    old_attempt = DeliveryAttempt(
        delivery_id=failed.delivery_id,
        attempt_number=1,
        completed_at=NOW,
        http_status=500,
        success=False,
        error_message="HTTP 500",
    )
    await attempts.insert_if_absent(old_attempt)

    manager, published, _ = manager_for(
        [
            DeadLetter(
                delivery=failed,
                event_type=original.event_type,
                reason="FAILED",
                attempt_count=1,
                last_error="HTTP 500",
                failed_at=NOW,
            )
        ],
        [original],
    )
    await manager.replay_dead_letters(now=NOW)
    work = published[0][2]

    worker = RecordingWorker()
    dispatcher = dispatcher_for(target, deliveries, attempts, consumer, worker)
    await dispatcher._accept(replay_message(work))
    await _drain(dispatcher)

    # History is intact.
    assert deliveries.rows[(original.event_id, subscription.subscription_id)] == failed
    assert attempts.rows[(failed.delivery_id, 1)] == old_attempt
    # The replay is a separate Delivery with its own attempt 1.
    replayed = deliveries.rows[(work.event.event_id, subscription.subscription_id)]
    assert replayed.delivery_id != failed.delivery_id
    assert worker.calls[0]["delivery"].delivery_id == replayed.delivery_id
    assert worker.calls[0]["attempt"].attempt_number == 1
    assert worker.calls[0]["event"].causation_id.value == original.event_id


async def test_redelivered_replay_message_does_not_send_twice(
    deliveries, attempts, consumer
):
    target = make_target("OrderCreated")
    subscription, _ = target
    event = make_event("OrderCreated")

    class CompletingWorker(RecordingWorker):
        """Records the outcome like the real Worker; an attempt left in
        flight is legitimately re-executed on redelivery."""

        async def execute(self, attempt, target_url, endpoint_id, **kwargs):
            result = await super().execute(attempt, target_url, endpoint_id, **kwargs)
            await attempts.save(
                attempt.model_copy(update={"completed_at": NOW, "success": True})
            )
            return result

    worker = CompletingWorker()
    dispatcher = dispatcher_for(target, deliveries, attempts, consumer, worker)
    work = ReplayWork(
        replay_id="r-1",
        source_delivery_id="source",
        subscription_id=subscription.subscription_id,
        event=event,
    )

    await dispatcher._accept(replay_message(work, offset=1))
    await _drain(dispatcher)
    await dispatcher._accept(replay_message(work, offset=2))
    await _drain(dispatcher)

    assert len(worker.calls) == 1
    assert consumer.committed == [1, 2]


async def test_replay_to_quarantined_endpoint_parks_instead_of_sending(
    deliveries, attempts, consumer
):
    target = make_target("OrderCreated")
    subscription, endpoint = target
    cache = InMemoryPolicyCache()
    cache.set_policy(
        endpoint.endpoint_id, make_policy(isolation=IsolationAction.QUARANTINE)
    )
    worker = RecordingWorker()
    dispatcher = dispatcher_for(target, deliveries, attempts, consumer, worker, cache)

    class OfflineAEDE:
        strategy_name = "OFFLINE"

        def evaluate(self, *a, **k):
            raise RuntimeError("AEDE Offline")

    dispatcher._strategy = OfflineAEDE()

    event = make_event("OrderCreated")
    work = ReplayWork(
        replay_id="r-1",
        source_delivery_id="source",
        subscription_id=subscription.subscription_id,
        event=event,
    )
    await dispatcher._accept(replay_message(work))
    await _drain(dispatcher)

    assert worker.calls == []
    assert attempts.numbers() == []
    assert deliveries.only().status == "PENDING"
    assert consumer.committed == [7]


async def test_replay_for_missing_subscription_is_dropped_and_committed(
    deliveries, attempts, consumer
):
    target = make_target("OrderCreated")
    worker = RecordingWorker()
    dispatcher = dispatcher_for(target, deliveries, attempts, consumer, worker)
    work = ReplayWork(
        replay_id="r-1",
        source_delivery_id="source",
        subscription_id="gone",
        event=make_event("OrderCreated"),
    )

    await dispatcher._accept(replay_message(work))
    await _drain(dispatcher)

    assert worker.calls == []
    assert consumer.committed == [7]


async def test_failed_replay_dispatch_is_not_committed(deliveries, attempts, consumer):
    target = make_target("OrderCreated")
    subscription, _ = target
    dispatcher = dispatcher_for(
        target, deliveries, attempts, consumer, RecordingWorker(fail=True)
    )
    work = ReplayWork(
        replay_id="r-1",
        source_delivery_id="source",
        subscription_id=subscription.subscription_id,
        event=make_event("OrderCreated"),
    )

    await dispatcher._accept(replay_message(work))
    await _drain(dispatcher)

    assert consumer.committed == []


async def test_invalid_replay_payload_is_committed_and_skipped(
    deliveries, attempts, consumer
):
    target = make_target("OrderCreated")
    worker = RecordingWorker()
    dispatcher = dispatcher_for(target, deliveries, attempts, consumer, worker)

    await dispatcher._accept(
        KafkaMessage(
            topic=EVENTS_INTERNAL_REPLAY_TOPIC,
            partition=0,
            offset=3,
            key="x",
            value=b"{not json",
        )
    )

    assert worker.calls == []
    assert consumer.committed == [3]


async def test_named_deliveries_are_not_truncated_by_the_default_limit() -> None:
    events = [make_event("OrderCreated") for _ in range(60)]
    items = [dead_letter(e, reason="FAILED") for e in events]
    manager, published, _ = manager_for(items, events)
    ids = [i.delivery.delivery_id for i in items]

    result = await manager.replay_dead_letters(now=NOW, delivery_ids=ids)

    assert result.requested == 60
    assert result.skipped == []
    assert len(published) == 60
