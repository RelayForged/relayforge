"""
Week 3-4 sprint item 8: Simulator 5xx -> Retry Manager schedules with
Strategy A delays (5s, 10s, ... capped at 300s, max 10 attempts).

The delay sequence is a contract, not an implementation detail: it is
fixed by Strategy A's RetryPolicy fields and asserted here so a future
change to the Retry Manager cannot quietly alter it. Deliberately no
jitter -- see docs/small-decisions-personA/audit-recommendations-declined.md.
"""
from datetime import datetime, timedelta, timezone

import pytest

from relay.control_plane.aede.strategies.static import StrategyA
from relay.data_plane.retry_manager.manager import (
    RetryManager,
    RetryWork,
    exponential_delay_seconds,
)
from relay.infrastructure.messaging.consumer import EVENTS_INTERNAL_DELIVERY_TOPIC
from relay.kernel.entities.delivery import Delivery
from relay.kernel.policies.delivery_policy import IsolationAction
from relay.kernel.value_objects.endpoint_state import EndpointState
from relay.kernel.value_objects.eri import ERIScore

from tests.data_plane._helpers import completed_attempt, make_event, make_policy


def strategy_a_policy():
    """The real Strategy A policy, not a hand-built stand-in."""
    policy, _record = StrategyA().evaluate(
        "endpoint-1",
        EndpointState.AVAILABLE,
        ERIScore.perfect(window_size=0, computed_at=datetime.now(timezone.utc)),
        datetime.now(timezone.utc),
    )
    return policy


# --------------------------------------------------------------------------
# The delay function
# --------------------------------------------------------------------------


def test_strategy_a_retry_policy_fields_are_the_documented_ones():
    retry = strategy_a_policy().retry_policy

    assert retry.initial_delay_seconds == 5.0
    assert retry.backoff_multiplier == 2.0
    assert retry.max_delay_seconds == 300.0
    assert retry.max_retries == 10
    assert retry.backoff_strategy.value == "EXPONENTIAL"


@pytest.mark.parametrize(
    "failed_attempt_number,expected_delay",
    [
        (1, 5.0),
        (2, 10.0),
        (3, 20.0),
        (4, 40.0),
        (5, 80.0),
        (6, 160.0),
        (7, 300.0),  # 320 capped
        (8, 300.0),
        (9, 300.0),
    ],
)
def test_exponential_delay_sequence_and_cap(failed_attempt_number, expected_delay):
    retry = strategy_a_policy().retry_policy

    assert (
        exponential_delay_seconds(retry, failed_attempt_number) == expected_delay
    )


def test_delay_never_exceeds_max_delay_seconds():
    retry = strategy_a_policy().retry_policy

    for n in range(1, 30):
        assert exponential_delay_seconds(retry, n) <= retry.max_delay_seconds


# --------------------------------------------------------------------------
# Scheduling behaviour
# --------------------------------------------------------------------------


async def test_failed_attempt_schedules_next_with_five_second_delay(
    deliveries, attempts, captured_publishes
):
    manager = RetryManager(deliveries=deliveries, attempts=attempts)
    event = make_event()
    delivery = Delivery(
        event_id=event.event_id, subscription_id="sub-1", endpoint_id="endpoint-1"
    )
    await deliveries.save(delivery)
    failed = completed_attempt(delivery, 1)
    before = datetime.now(timezone.utc)

    work = await manager.handle(failed, delivery, strategy_a_policy(), event)

    assert work is not None
    assert work.attempt.attempt_number == 2
    delay = (work.next_attempt_at - before).total_seconds()
    assert 4.5 <= delay <= 5.5
    # The attempt row is persisted before the Kafka publish, so a crash
    # after publish cannot reference a row that does not exist.
    assert attempts.numbers() == [2]
    assert deliveries.only().status == "PENDING"


async def test_scheduled_work_is_published_to_the_retry_topic_keyed_by_event_id(
    deliveries, attempts, captured_publishes
):
    manager = RetryManager(deliveries=deliveries, attempts=attempts)
    event = make_event()
    delivery = Delivery(
        event_id=event.event_id, subscription_id="sub-1", endpoint_id="endpoint-1"
    )
    await deliveries.save(delivery)

    await manager.handle(
        completed_attempt(delivery, 1), delivery, strategy_a_policy(), event
    )

    assert len(captured_publishes) == 1
    topic, key, value = captured_publishes[0]
    assert topic == EVENTS_INTERNAL_DELIVERY_TOPIC
    assert key == delivery.event_id
    # Round-trips, and carries the Event so the retry reproduces the same
    # request body (ADR 0016).
    decoded = RetryWork.model_validate_json(value)
    assert decoded.event.event_id == event.event_id
    assert decoded.event.payload == event.payload
    assert decoded.attempt.attempt_number == 2
    assert decoded.policy.retry_policy.max_retries == 10


async def test_ten_attempts_then_failed_with_the_full_delay_sequence(
    deliveries, attempts, captured_publishes
):
    """
    Drive the whole chain: attempts 1..9 each schedule the next, attempt
    10 exhausts the budget and marks the Delivery FAILED. Ten HTTP
    attempts total, matching retry-manager.md Decision 1.
    """
    manager = RetryManager(deliveries=deliveries, attempts=attempts)
    policy = strategy_a_policy()
    event = make_event()
    delivery = Delivery(
        event_id=event.event_id, subscription_id="sub-1", endpoint_id="endpoint-1"
    )
    await deliveries.save(delivery)

    observed_delays = []
    for attempt_number in range(1, 11):
        before = datetime.now(timezone.utc)
        work = await manager.handle(
            completed_attempt(delivery, attempt_number), delivery, policy, event
        )
        if attempt_number < 10:
            assert work is not None, f"attempt {attempt_number} should schedule a retry"
            observed_delays.append(
                round((work.next_attempt_at - before).total_seconds())
            )
        else:
            assert work is None, "attempt 10 must not schedule an 11th"

    assert observed_delays == [5, 10, 20, 40, 80, 160, 300, 300, 300]
    assert deliveries.only().status == "FAILED"
    assert attempts.numbers() == [2, 3, 4, 5, 6, 7, 8, 9, 10]
    assert len(captured_publishes) == 9


async def test_success_marks_delivery_success_and_publishes_nothing(
    deliveries, attempts, captured_publishes
):
    manager = RetryManager(deliveries=deliveries, attempts=attempts)
    event = make_event()
    delivery = Delivery(
        event_id=event.event_id, subscription_id="sub-1", endpoint_id="endpoint-1"
    )
    await deliveries.save(delivery)

    work = await manager.handle(
        completed_attempt(delivery, 1, success=True, http_status=200),
        delivery,
        strategy_a_policy(),
        event,
    )

    assert work is None
    assert deliveries.only().status == "SUCCESS"
    assert captured_publishes == []


@pytest.mark.parametrize("action", [IsolationAction.QUARANTINE])
async def test_quarantine_leaves_delivery_pending_and_durable(
    action, deliveries, attempts, captured_publishes
):
    """Must not mark FAILED -- that would drop work the policy only deferred."""
    manager = RetryManager(deliveries=deliveries, attempts=attempts)
    event = make_event()
    delivery = Delivery(
        event_id=event.event_id, subscription_id="sub-1", endpoint_id="endpoint-1"
    )
    await deliveries.save(delivery)

    work = await manager.handle(
        completed_attempt(delivery, 1), delivery, make_policy(isolation=action), event
    )

    assert work is None
    assert deliveries.only().status == "PENDING"
    assert captured_publishes == []
    assert attempts.numbers() == []


async def test_pause_schedules_the_retry(deliveries, attempts, captured_publishes):
    """PAUSE throttles through the policy's backoff; it does not stop (ADR 0022)."""
    manager = RetryManager(deliveries=deliveries, attempts=attempts)
    event = make_event()
    delivery = Delivery(
        event_id=event.event_id, subscription_id="sub-1", endpoint_id="endpoint-1"
    )
    await deliveries.save(delivery)

    work = await manager.handle(
        completed_attempt(delivery, 1),
        delivery,
        make_policy(isolation=IsolationAction.PAUSE),
        event,
    )

    assert work is not None
    assert work.attempt.attempt_number == 2
    assert len(captured_publishes) == 1


async def test_deadline_seconds_overrides_remaining_retries(
    deliveries, attempts, captured_publishes
):
    """
    deadline_seconds is an absolute budget from Delivery.created_at and
    wins even when max_retries has attempts left.
    """
    manager = RetryManager(deliveries=deliveries, attempts=attempts)
    event = make_event()
    delivery = Delivery(
        event_id=event.event_id,
        subscription_id="sub-1",
        endpoint_id="endpoint-1",
        created_at=datetime.now(timezone.utc) - timedelta(seconds=100),
    )
    await deliveries.save(delivery)

    # attempt 1 of 10, so max_retries alone would schedule a retry.
    work = await manager.handle(
        completed_attempt(delivery, 1),
        delivery,
        make_policy(deadline_seconds=30.0),
        event,
    )

    assert work is None
    assert deliveries.only().status == "FAILED"
    assert captured_publishes == []


async def test_deadline_still_in_budget_schedules_normally(
    deliveries, attempts, captured_publishes
):
    manager = RetryManager(deliveries=deliveries, attempts=attempts)
    event = make_event()
    delivery = Delivery(
        event_id=event.event_id, subscription_id="sub-1", endpoint_id="endpoint-1"
    )
    await deliveries.save(delivery)

    work = await manager.handle(
        completed_attempt(delivery, 1),
        delivery,
        make_policy(deadline_seconds=3600.0),
        event,
    )

    assert work is not None
    assert deliveries.only().status == "PENDING"


async def test_redelivered_failure_reuses_the_pending_attempt_row(
    deliveries, attempts, captured_publishes
):
    """
    At-least-once means the same failed attempt can be handled twice. The
    second pass must reuse attempt 2 rather than violating
    uq_delivery_attempts_delivery_id_attempt_number.
    """
    manager = RetryManager(deliveries=deliveries, attempts=attempts)
    policy = strategy_a_policy()
    event = make_event()
    delivery = Delivery(
        event_id=event.event_id, subscription_id="sub-1", endpoint_id="endpoint-1"
    )
    await deliveries.save(delivery)
    failed = completed_attempt(delivery, 1)

    first = await manager.handle(failed, delivery, policy, event)
    second = await manager.handle(failed, delivery, policy, event)

    assert first is not None and second is not None
    assert first.attempt.attempt_id == second.attempt.attempt_id
    assert attempts.numbers() == [2]


async def test_stands_down_when_the_next_attempt_already_completed(
    deliveries, attempts, captured_publishes
):
    """A redelivery must not re-run a retry that already executed."""
    manager = RetryManager(deliveries=deliveries, attempts=attempts)
    event = make_event()
    delivery = Delivery(
        event_id=event.event_id, subscription_id="sub-1", endpoint_id="endpoint-1"
    )
    await deliveries.save(delivery)
    await attempts.insert_if_absent(completed_attempt(delivery, 2))

    work = await manager.handle(
        completed_attempt(delivery, 1), delivery, strategy_a_policy(), event
    )

    assert work is None
    assert captured_publishes == []


async def test_retry_manager_never_calls_the_strategy(
    deliveries, attempts, captured_publishes, monkeypatch
):
    """
    ADR 0003 / 0005: the Retry Manager executes a policy the Control
    Plane already produced. Re-evaluating a strategy here would be
    strategy selection inside the data plane.
    """
    def explode(*args, **kwargs):
        raise AssertionError("Retry Manager must not evaluate a strategy")

    monkeypatch.setattr(StrategyA, "evaluate", explode)

    manager = RetryManager(deliveries=deliveries, attempts=attempts)
    event = make_event()
    delivery = Delivery(
        event_id=event.event_id, subscription_id="sub-1", endpoint_id="endpoint-1"
    )
    await deliveries.save(delivery)

    work = await manager.handle(
        completed_attempt(delivery, 1), delivery, make_policy(), event
    )

    assert work is not None
