"""
Regression tests for the Dispatcher: commit discipline, terminal states,
and policy enforcement.

The theme is persist-first (ADR 0002): a Kafka offset is acknowledged
only once the work behind it is durable. Several of these cases can only
happen under redelivery or partial failure, which the smoke script never
produces.
"""
import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from relay.data_plane.dispatcher.main import Dispatcher
from relay.data_plane.retry_manager.manager import RetryWork
from relay.infrastructure.feature_flags import FeatureFlags
from relay.infrastructure.messaging.consumer import (
    EVENTS_BUSINESS_TOPIC,
    EVENTS_INTERNAL_DELIVERY_TOPIC,
    KafkaMessage,
)
from relay.kernel.entities.delivery import Delivery, DeliveryAttempt
from relay.kernel.policies.delivery_policy import IsolationAction

from tests.data_plane._helpers import (
    FakeEndpointRepository,
    FakeSubscriptionRepository,
    make_event,
    make_policy,
    make_target,
)


class RecordingWorker:
    """Stands in for WebhookWorker, recording each POST it was asked to make."""

    def __init__(self, *, fail: bool = False) -> None:
        self.calls: list[dict] = []
        self.fail = fail
        self.concurrent = 0
        self.max_concurrent = 0
        self.closed = False

    async def execute(self, attempt, target_url, endpoint_id, **kwargs):
        self.concurrent += 1
        self.max_concurrent = max(self.max_concurrent, self.concurrent)
        try:
            await asyncio.sleep(0)
            self.calls.append(
                {
                    "attempt": attempt,
                    "target_url": target_url,
                    "endpoint_id": endpoint_id,
                    **kwargs,
                }
            )
            if self.fail:
                raise RuntimeError("worker blew up")
            return attempt, None
        finally:
            self.concurrent -= 1

    async def aclose(self) -> None:
        self.closed = True


def build_dispatcher(
    *,
    targets,
    deliveries,
    attempts,
    consumer,
    worker=None,
    endpoints=None,
    feature_flags=None,
    cache=None,
    fallback_manager=None,
):
    return Dispatcher(
        consumer=consumer,
        subscriptions=FakeSubscriptionRepository(targets),
        deliveries=deliveries,
        attempts=attempts,
        endpoints=endpoints or FakeEndpointRepository(),
        worker=worker or RecordingWorker(),
        feature_flags=feature_flags,
        cache=cache,
        fallback_manager=fallback_manager,
    )


def business_message(event, offset: int = 0) -> KafkaMessage:
    return KafkaMessage(
        topic=EVENTS_BUSINESS_TOPIC,
        partition=0,
        offset=offset,
        key=event.event_id,
        value=event.model_dump_json().encode("utf-8"),
    )


# --------------------------------------------------------------------------
# Fan-out
# --------------------------------------------------------------------------


async def test_one_delivery_per_subscription_not_per_event(
    deliveries, attempts, consumer
):
    """Three subscriptions on one event type means three Delivery rows."""
    event = make_event("OrderCreated")
    targets = [make_target("OrderCreated") for _ in range(3)]
    worker = RecordingWorker()
    dispatcher = build_dispatcher(
        targets=targets,
        deliveries=deliveries,
        attempts=attempts,
        consumer=consumer,
        worker=worker,
    )

    await dispatcher.handle_message(business_message(event))

    assert len(deliveries.rows) == 3
    assert len(worker.calls) == 3
    assert consumer.committed == [0]


async def test_non_matching_event_type_is_committed_with_no_work(
    deliveries, attempts, consumer
):
    event = make_event("SomethingElse")
    dispatcher = build_dispatcher(
        targets=[make_target("OrderCreated")],
        deliveries=deliveries,
        attempts=attempts,
        consumer=consumer,
    )

    await dispatcher.handle_message(business_message(event))

    assert deliveries.rows == {}
    assert consumer.committed == [0], "an event with no subscribers must not stall"


async def test_inactive_endpoint_is_excluded_from_fan_out(
    deliveries, attempts, consumer
):
    worker = RecordingWorker()
    dispatcher = build_dispatcher(
        targets=[make_target("OrderCreated", is_active=False)],
        deliveries=deliveries,
        attempts=attempts,
        consumer=consumer,
        worker=worker,
    )

    await dispatcher.handle_message(business_message(make_event("OrderCreated")))

    assert worker.calls == []


# --------------------------------------------------------------------------
# Commit discipline
# --------------------------------------------------------------------------


async def test_worker_failure_withholds_the_commit(deliveries, attempts, consumer):
    """
    The offset must stay uncommitted so the message is redelivered.
    Committing here would turn at-least-once into at-most-once.
    """
    dispatcher = build_dispatcher(
        targets=[make_target("OrderCreated")],
        deliveries=deliveries,
        attempts=attempts,
        consumer=consumer,
        worker=RecordingWorker(fail=True),
    )

    await dispatcher.handle_message(business_message(make_event("OrderCreated")))

    assert consumer.committed == [], "acknowledged a message whose work failed"


async def test_repository_failure_withholds_the_commit(
    deliveries, attempts, consumer, monkeypatch
):
    async def boom(*args, **kwargs):
        raise RuntimeError("postgres is down")

    monkeypatch.setattr(deliveries, "save", boom)
    dispatcher = build_dispatcher(
        targets=[make_target("OrderCreated")],
        deliveries=deliveries,
        attempts=attempts,
        consumer=consumer,
    )

    await dispatcher.handle_message(business_message(make_event("OrderCreated")))

    assert consumer.committed == []


async def test_partial_fan_out_failure_withholds_the_commit(
    deliveries, attempts, consumer
):
    """One bad target out of three must still force redelivery."""
    calls = {"n": 0}

    class FlakyWorker(RecordingWorker):
        async def execute(self, attempt, target_url, endpoint_id, **kwargs):
            calls["n"] += 1
            if calls["n"] == 2:
                raise RuntimeError("second target failed")
            return await super().execute(attempt, target_url, endpoint_id, **kwargs)

    dispatcher = build_dispatcher(
        targets=[make_target("OrderCreated") for _ in range(3)],
        deliveries=deliveries,
        attempts=attempts,
        consumer=consumer,
        worker=FlakyWorker(),
    )

    await dispatcher.handle_message(business_message(make_event("OrderCreated")))

    assert consumer.committed == []


async def test_unparseable_payload_is_committed_to_unblock_the_partition(
    deliveries, attempts, consumer
):
    """
    A malformed message can never succeed, so withholding the commit
    would wedge the partition forever.
    """
    dispatcher = build_dispatcher(
        targets=[make_target("OrderCreated")],
        deliveries=deliveries,
        attempts=attempts,
        consumer=consumer,
    )
    poison = KafkaMessage(
        topic=EVENTS_BUSINESS_TOPIC,
        partition=0,
        offset=7,
        key="k",
        value=b"{not valid json",
    )

    await dispatcher.handle_message(poison)

    assert consumer.committed == [7]
    assert deliveries.rows == {}


# --------------------------------------------------------------------------
# Redelivery idempotency
# --------------------------------------------------------------------------


async def test_redelivery_does_not_create_a_second_delivery_row(
    deliveries, attempts, consumer
):
    event = make_event("OrderCreated")
    targets = [make_target("OrderCreated")]
    worker = RecordingWorker()
    dispatcher = build_dispatcher(
        targets=targets,
        deliveries=deliveries,
        attempts=attempts,
        consumer=consumer,
        worker=worker,
    )

    await dispatcher.handle_message(business_message(event, offset=0))
    await dispatcher.handle_message(business_message(event, offset=0))

    assert len(deliveries.rows) == 1
    assert attempts.numbers() == [1]
    # Attempt 1 is still in flight (RecordingWorker never completes it),
    # so the second pass reuses the slot rather than duplicating it.
    assert len(worker.calls) == 2


async def test_redelivery_after_attempt_one_completed_does_not_repost(
    deliveries, attempts, consumer
):
    """The retry chain owns the Delivery once attempt 1 has finished."""
    event = make_event("OrderCreated")
    subscription, endpoint = make_target("OrderCreated")
    delivery = Delivery(
        event_id=event.event_id,
        subscription_id=subscription.subscription_id,
        endpoint_id=endpoint.endpoint_id,
    )
    await deliveries.save(delivery)
    await attempts.insert_if_absent(
        DeliveryAttempt(
            delivery_id=delivery.delivery_id,
            attempt_number=1,
            completed_at=datetime.now(timezone.utc),
            success=False,
            http_status=500,
        )
    )
    worker = RecordingWorker()
    dispatcher = build_dispatcher(
        targets=[(subscription, endpoint)],
        deliveries=deliveries,
        attempts=attempts,
        consumer=consumer,
        worker=worker,
    )

    await dispatcher.handle_message(business_message(event))

    assert worker.calls == [], "re-POSTed an attempt that already completed"
    assert consumer.committed == [0]


@pytest.mark.parametrize("status", ["SUCCESS", "FAILED", "DEAD_LETTER"])
async def test_terminal_delivery_is_not_reprocessed(
    status, deliveries, attempts, consumer
):
    event = make_event("OrderCreated")
    subscription, endpoint = make_target("OrderCreated")
    await deliveries.save(
        Delivery(
            event_id=event.event_id,
            subscription_id=subscription.subscription_id,
            endpoint_id=endpoint.endpoint_id,
            status=status,
        )
    )
    worker = RecordingWorker()
    dispatcher = build_dispatcher(
        targets=[(subscription, endpoint)],
        deliveries=deliveries,
        attempts=attempts,
        consumer=consumer,
        worker=worker,
    )

    await dispatcher.handle_message(business_message(event))

    assert worker.calls == []
    assert attempts.numbers() == []
    assert consumer.committed == [0]


# --------------------------------------------------------------------------
# Isolation policy
# --------------------------------------------------------------------------


@pytest.mark.parametrize("action", [IsolationAction.QUARANTINE])
async def test_quarantine_writes_no_attempt_row(
    action, deliveries, attempts, consumer, monkeypatch
):
    """
    An attempt row that can never complete would occupy the attempt-1
    slot and permanently block this Delivery from dispatching once the
    endpoint recovers.
    """
    subscription, endpoint = make_target("OrderCreated")
    worker = RecordingWorker()
    dispatcher = build_dispatcher(
        targets=[(subscription, endpoint)],
        deliveries=deliveries,
        attempts=attempts,
        consumer=consumer,
        worker=worker,
    )
    policy = make_policy(isolation=action)
    monkeypatch.setattr(
        dispatcher._strategy, "evaluate", lambda *a, **k: (policy, None)
    )

    await dispatcher.handle_message(business_message(make_event("OrderCreated")))

    assert worker.calls == [], "made an HTTP call under an isolation hold"
    assert attempts.numbers() == [], "wrote an attempt that can never complete"
    # The Delivery stays durable and retryable.
    assert deliveries.only().status == "PENDING"
    assert consumer.committed == [0]


async def test_pause_still_delivers(deliveries, attempts, consumer, monkeypatch):
    """
    PAUSE is UNSTABLE's throttled delivery, not a stop (ADR 0022). With no
    HTTP there is no telemetry, and an UNSTABLE endpoint could never leave
    UNSTABLE.
    """
    subscription, endpoint = make_target("OrderCreated")
    worker = RecordingWorker()
    dispatcher = build_dispatcher(
        targets=[(subscription, endpoint)],
        deliveries=deliveries,
        attempts=attempts,
        consumer=consumer,
        worker=worker,
    )
    policy = make_policy(isolation=IsolationAction.PAUSE)
    monkeypatch.setattr(
        dispatcher._strategy, "evaluate", lambda *a, **k: (policy, None)
    )

    await dispatcher.handle_message(business_message(make_event("OrderCreated")))

    assert len(worker.calls) == 1
    assert attempts.numbers() == [1]


# --------------------------------------------------------------------------
# Dead-lettering
# --------------------------------------------------------------------------


async def test_missing_endpoint_dead_letters_and_closes_the_attempt(
    deliveries, attempts, consumer
):
    """
    A missing endpoint is a configuration fact, not endpoint health, so
    retrying cannot fix it. Without this the row sits in PENDING forever
    behind an attempt that never completes.
    """
    event = make_event("OrderCreated")
    subscription, endpoint = make_target("OrderCreated")
    dispatcher = Dispatcher(
        consumer=consumer,
        subscriptions=FakeSubscriptionRepository([(subscription, endpoint)]),
        deliveries=deliveries,
        attempts=attempts,
        endpoints=FakeEndpointRepository([]),
        worker=RecordingWorker(),
    )
    delivery = Delivery(
        event_id=event.event_id,
        subscription_id=subscription.subscription_id,
        endpoint_id=endpoint.endpoint_id,
    )
    await deliveries.save(delivery)
    attempt = DeliveryAttempt(delivery_id=delivery.delivery_id, attempt_number=1)
    await attempts.insert_if_absent(attempt)

    await dispatcher._execute_attempt(
        attempt, delivery, make_policy(), event
    )

    assert deliveries.only().status == "DEAD_LETTER"
    stored = await attempts.get_by_id(attempt.attempt_id)
    assert stored.completed_at is not None, "left a dangling incomplete attempt"
    assert stored.success is False
    assert "undeliverable" in stored.error_message


async def test_deactivated_endpoint_dead_letters(deliveries, attempts, consumer):
    event = make_event("OrderCreated")
    subscription, endpoint = make_target("OrderCreated", is_active=False)
    dispatcher = Dispatcher(
        consumer=consumer,
        subscriptions=FakeSubscriptionRepository([]),
        deliveries=deliveries,
        attempts=attempts,
        endpoints=FakeEndpointRepository([endpoint]),
        worker=RecordingWorker(),
    )
    delivery = Delivery(
        event_id=event.event_id,
        subscription_id=subscription.subscription_id,
        endpoint_id=endpoint.endpoint_id,
    )
    await deliveries.save(delivery)
    attempt = DeliveryAttempt(delivery_id=delivery.delivery_id, attempt_number=1)

    await dispatcher._execute_attempt(attempt, delivery, make_policy(), event)

    assert deliveries.only().status == "DEAD_LETTER"


# --------------------------------------------------------------------------
# Concurrency
# --------------------------------------------------------------------------


async def test_max_parallel_one_serializes_calls_to_an_endpoint(
    deliveries, attempts, consumer, monkeypatch
):
    """Strategy A sets max_parallel=1, so one endpoint gets one at a time."""
    subscription_a, endpoint = make_target("OrderCreated")
    # Two subscriptions pointing at the same endpoint.
    from relay.kernel.entities.subscription import Subscription

    subscription_b = Subscription(
        endpoint_id=endpoint.endpoint_id,
        event_types=["OrderCreated"],
        status="ACTIVE",
    )

    class SlowWorker(RecordingWorker):
        async def execute(self, attempt, target_url, endpoint_id, **kwargs):
            self.concurrent += 1
            self.max_concurrent = max(self.max_concurrent, self.concurrent)
            try:
                await asyncio.sleep(0.02)
                self.calls.append({"attempt": attempt})
                return attempt, None
            finally:
                self.concurrent -= 1

    worker = SlowWorker()
    dispatcher = build_dispatcher(
        targets=[(subscription_a, endpoint), (subscription_b, endpoint)],
        deliveries=deliveries,
        attempts=attempts,
        consumer=consumer,
        worker=worker,
        feature_flags=FeatureFlags(static_retry_mode=True),
    )

    await dispatcher.handle_message(business_message(make_event("OrderCreated")))

    assert len(worker.calls) == 2
    assert worker.max_concurrent == 1, "exceeded concurrency_policy.max_parallel"


async def test_distinct_endpoints_run_in_parallel(
    deliveries, attempts, consumer
):
    """max_parallel is per endpoint, not a global lock."""
    class SlowWorker(RecordingWorker):
        async def execute(self, attempt, target_url, endpoint_id, **kwargs):
            self.concurrent += 1
            self.max_concurrent = max(self.max_concurrent, self.concurrent)
            try:
                await asyncio.sleep(0.02)
                self.calls.append({"attempt": attempt})
                return attempt, None
            finally:
                self.concurrent -= 1

    worker = SlowWorker()
    dispatcher = build_dispatcher(
        targets=[make_target("OrderCreated") for _ in range(4)],
        deliveries=deliveries,
        attempts=attempts,
        consumer=consumer,
        worker=worker,
    )

    await dispatcher.handle_message(business_message(make_event("OrderCreated")))

    assert worker.max_concurrent > 1, "serialized independent endpoints"


# --------------------------------------------------------------------------
# Scheduled retries
# --------------------------------------------------------------------------


async def test_scheduled_retry_commits_only_after_execution(
    deliveries, attempts, consumer
):
    event = make_event("OrderCreated")
    subscription, endpoint = make_target("OrderCreated")
    delivery = Delivery(
        event_id=event.event_id,
        subscription_id=subscription.subscription_id,
        endpoint_id=endpoint.endpoint_id,
    )
    worker = RecordingWorker()
    dispatcher = Dispatcher(
        consumer=consumer,
        subscriptions=FakeSubscriptionRepository([]),
        deliveries=deliveries,
        attempts=attempts,
        endpoints=FakeEndpointRepository([endpoint]),
        worker=worker,
    )
    work = RetryWork(
        next_attempt_at=datetime.now(timezone.utc),
        attempt=DeliveryAttempt(
            delivery_id=delivery.delivery_id, attempt_number=2
        ),
        delivery=delivery,
        policy=make_policy(),
        event=event,
    )
    message = KafkaMessage(
        topic=EVENTS_INTERNAL_DELIVERY_TOPIC,
        partition=0,
        offset=3,
        key=event.event_id,
        value=work.model_dump_json().encode("utf-8"),
    )

    await dispatcher._run_scheduled(work, message)

    assert len(worker.calls) == 1
    assert worker.calls[0]["attempt"].attempt_number == 2
    assert consumer.committed == [3]


async def test_cancelled_backoff_does_not_commit(deliveries, attempts, consumer):
    """
    Shutdown during a 300 s backoff must leave the offset uncommitted so
    the retry is redelivered rather than lost from memory.
    """
    event = make_event("OrderCreated")
    subscription, endpoint = make_target("OrderCreated")
    delivery = Delivery(
        event_id=event.event_id,
        subscription_id=subscription.subscription_id,
        endpoint_id=endpoint.endpoint_id,
    )
    worker = RecordingWorker()
    dispatcher = Dispatcher(
        consumer=consumer,
        subscriptions=FakeSubscriptionRepository([]),
        deliveries=deliveries,
        attempts=attempts,
        endpoints=FakeEndpointRepository([endpoint]),
        worker=worker,
    )
    work = RetryWork(
        next_attempt_at=datetime.now(timezone.utc) + timedelta(seconds=300),
        attempt=DeliveryAttempt(
            delivery_id=delivery.delivery_id, attempt_number=2
        ),
        delivery=delivery,
        policy=make_policy(),
        event=event,
    )
    message = KafkaMessage(
        topic=EVENTS_INTERNAL_DELIVERY_TOPIC,
        partition=0,
        offset=5,
        key=event.event_id,
        value=work.model_dump_json().encode("utf-8"),
    )

    task = asyncio.create_task(dispatcher._run_scheduled(work, message))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert consumer.committed == [], "committed a retry that never executed"
    assert worker.calls == []


async def test_invalid_retry_payload_is_committed(deliveries, attempts, consumer):
    dispatcher = build_dispatcher(
        targets=[],
        deliveries=deliveries,
        attempts=attempts,
        consumer=consumer,
    )
    poison = KafkaMessage(
        topic=EVENTS_INTERNAL_DELIVERY_TOPIC,
        partition=0,
        offset=9,
        key="k",
        value=b"{}",
    )

    await dispatcher._accept_retry(poison)

    assert consumer.committed == [9]


# --------------------------------------------------------------------------
# Dynamic Strategy & Three-Tier Policy Fallback Tests (Task T-002)
# --------------------------------------------------------------------------


def test_dispatcher_strategy_selection_from_flags():
    """Verify Dispatcher initializes Strategy A, B, or C based on FeatureFlags."""
    # 1. Strategy C (default)
    d_c = Dispatcher(feature_flags=FeatureFlags())
    assert d_c._strategy.strategy_name == "STRATEGY_C"

    # 2. Strategy B (adaptive retry mode)
    d_b = Dispatcher(feature_flags=FeatureFlags(adaptive_retry_mode=True))
    assert d_b._strategy.strategy_name == "STRATEGY_B"

    # 3. Strategy A (static retry mode or aede disabled)
    d_a = Dispatcher(feature_flags=FeatureFlags(static_retry_mode=True))
    assert d_a._strategy.strategy_name == "STRATEGY_A"

    d_a2 = Dispatcher(feature_flags=FeatureFlags(aede_enabled=False))
    assert d_a2._strategy.strategy_name == "STRATEGY_A"


async def test_dispatcher_three_tier_fallback_hierarchy(deliveries, attempts, consumer):
    """Verify Three-Tier Fallback: Tier 1 (live) -> Tier 2 (cached) -> Tier 3 (static)."""
    from relay.infrastructure.cache import InMemoryPolicyCache, PolicyFallbackManager
    from relay.kernel.value_objects.endpoint_state import EndpointState

    cache = InMemoryPolicyCache()
    fallback_mgr = PolicyFallbackManager(cache=cache)
    subscription, endpoint = make_target("OrderCreated")

    dispatcher = build_dispatcher(
        targets=[(subscription, endpoint)],
        deliveries=deliveries,
        attempts=attempts,
        consumer=consumer,
        cache=cache,
        fallback_manager=fallback_mgr,
    )

    now = datetime.now(timezone.utc)

    # Tier 1: normal Strategy C evaluation populates cache
    policy_tier1 = dispatcher._get_delivery_policy(
        endpoint_id=endpoint.endpoint_id,
        endpoint_state=EndpointState.AVAILABLE,
        computed_at=now,
    )
    assert policy_tier1 is not None
    assert cache.get_policy(endpoint.endpoint_id) is not None

    # Tier 2: simulate Tier 1 failure when cache is populated
    class BrokenStrategy:
        strategy_name = "BROKEN"

        def evaluate(self, *a, **k):
            raise RuntimeError("AEDE Service Unreachable")

    dispatcher._strategy = BrokenStrategy()
    policy_tier2 = dispatcher._get_delivery_policy(
        endpoint_id=endpoint.endpoint_id,
        endpoint_state=EndpointState.AVAILABLE,
        computed_at=now,
    )
    assert policy_tier2 is not None
    assert policy_tier2.policy_id == policy_tier1.policy_id

    # Tier 3: simulate Tier 1 failure and cache cold/expired
    cache.clear()
    policy_tier3 = dispatcher._get_delivery_policy(
        endpoint_id=endpoint.endpoint_id,
        endpoint_state=EndpointState.AVAILABLE,
        computed_at=now,
    )
    assert policy_tier3 is not None
    assert policy_tier3.concurrency_policy.max_parallel == 1
    assert policy_tier3.retry_policy.initial_delay_seconds == 5.0
    assert policy_tier3.retry_policy.max_retries == 10


async def test_dispatcher_quarantine_retains_delivery_pending(deliveries, attempts, consumer):
    """Verify an endpoint in QUARANTINED state (via cached policy) retains delivery in PENDING without HTTP calls."""
    from relay.infrastructure.cache import InMemoryPolicyCache
    from relay.kernel.policies.delivery_policy import IsolationAction

    cache = InMemoryPolicyCache()
    subscription, endpoint = make_target("OrderCreated")
    quarantined_policy = make_policy(isolation=IsolationAction.QUARANTINE)
    cache.set_policy(endpoint.endpoint_id, quarantined_policy)

    worker = RecordingWorker()
    dispatcher = build_dispatcher(
        targets=[(subscription, endpoint)],
        deliveries=deliveries,
        attempts=attempts,
        consumer=consumer,
        worker=worker,
        cache=cache,
    )

    # When live AEDE fails, Tier 2 cached QUARANTINE policy is served and enforced
    class OfflineAEDE:
        strategy_name = "OFFLINE"

        def evaluate(self, *a, **k):
            raise RuntimeError("AEDE Offline")

    dispatcher._strategy = OfflineAEDE()

    event = make_event("OrderCreated")
    await dispatcher.handle_message(business_message(event))

    assert worker.calls == [], "made HTTP calls for a quarantined endpoint"
    assert attempts.numbers() == [], "wrote attempt row for a quarantined endpoint"
    assert deliveries.only().status == "PENDING"
    assert consumer.committed == [0]

