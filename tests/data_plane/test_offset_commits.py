"""
Regression tests for ADR 0015 (watermark offset commits).

The Dispatcher processes messages concurrently, so committing
`message.offset + 1` per message would acknowledge a later message while
an earlier one is still in flight -- silently dropping it on restart.
These tests pin the watermark rule: never commit past the oldest
unfinished message.

Failures here mean at-least-once delivery is broken, which is not
something the smoke script can detect (it never crashes mid-batch).
"""
import pytest
from aiokafka import TopicPartition

from relay.infrastructure.messaging.consumer import (
    KafkaConsumer,
    KafkaMessage,
    _PartitionProgress,
)


def msg(offset: int, *, partition: int = 0, topic: str = "events.business"):
    return KafkaMessage(
        topic=topic, partition=partition, offset=offset, key="k", value=b"{}"
    )


# --------------------------------------------------------------------------
# The watermark rule in isolation
# --------------------------------------------------------------------------


def test_nothing_tracked_yields_no_watermark():
    assert _PartitionProgress().watermark() is None


def test_in_flight_message_blocks_everything_after_it():
    """The crux: offset 5 unfinished must pin the commit at 5, not 8."""
    progress = _PartitionProgress(in_flight={5}, completed={6, 7})

    assert progress.watermark() == 5


def test_all_complete_commits_past_the_highest():
    progress = _PartitionProgress(completed={0, 1, 2})

    assert progress.watermark() == 3


def test_oldest_of_several_in_flight_wins():
    progress = _PartitionProgress(in_flight={4, 9, 11}, completed={12})

    assert progress.watermark() == 4


# --------------------------------------------------------------------------
# commit() against an injected consumer
# --------------------------------------------------------------------------


class SpyAioKafkaConsumer:
    """Records commit({tp: offset}) calls in order."""

    def __init__(self) -> None:
        self.commits: list[dict] = []

    async def commit(self, offsets: dict) -> None:
        self.commits.append(dict(offsets))

    async def stop(self) -> None:
        pass


@pytest.fixture
def wired() -> tuple[KafkaConsumer, SpyAioKafkaConsumer]:
    """
    Bypass start() so no broker is required. Injecting the private
    attribute is deliberate: the watermark arithmetic is the unit under
    test, and it is unreachable without a live consumer otherwise.
    """
    consumer = KafkaConsumer(["events.business"], group_id="test-group")
    spy = SpyAioKafkaConsumer()
    consumer._consumer = spy
    return consumer, spy


def offsets_committed(spy: SpyAioKafkaConsumer, tp: TopicPartition) -> list[int]:
    return [c[tp] for c in spy.commits if tp in c]


async def test_out_of_order_completion_does_not_skip_the_slow_message(wired):
    """
    Messages 0,1,2 dispatched; 2 finishes first. Committing 3 there would
    lose 0 and 1 on restart, so the commit must stay pinned at 0 until 0
    itself lands. Committing offset 0 acknowledges nothing, so it is
    harmless -- what matters is that it never advances past the oldest
    unfinished message.
    """
    consumer, spy = wired
    tp = TopicPartition("events.business", 0)
    for offset in (0, 1, 2):
        consumer._track(msg(offset))

    await consumer.commit(msg(2))
    assert offsets_committed(spy, tp) == [0], "advanced past in-flight offset 0"

    await consumer.commit(msg(1))
    assert offsets_committed(spy, tp) == [0], "advanced past in-flight offset 0"

    await consumer.commit(msg(0))
    assert offsets_committed(spy, tp)[-1] == 3, "all done, so commit past 2"


async def test_commit_never_advances_past_the_oldest_in_flight_offset(wired):
    """
    The invariant, stated directly: at no point may the committed offset
    exceed the oldest message still being processed.
    """
    consumer, spy = wired
    tp = TopicPartition("events.business", 0)
    for offset in range(6):
        consumer._track(msg(offset))
    progress = consumer._progress[tp]

    # Finish in a deliberately awkward order, leaving 0 for last.
    for offset in (3, 5, 1, 4, 2, 0):
        await consumer.commit(msg(offset))
        if progress.in_flight:
            assert progress.committed <= min(progress.in_flight)

    assert offsets_committed(spy, tp)[-1] == 6


async def test_in_order_completion_commits_each_offset(wired):
    consumer, spy = wired
    tp = TopicPartition("events.business", 0)
    for offset in (0, 1, 2):
        consumer._track(msg(offset))

    for offset in (0, 1, 2):
        await consumer.commit(msg(offset))

    assert offsets_committed(spy, tp) == [1, 2, 3]


async def test_watermark_never_moves_backwards(wired):
    """A duplicate commit must not re-commit a lower offset."""
    consumer, spy = wired
    for offset in (0, 1):
        consumer._track(msg(offset))
    await consumer.commit(msg(0))
    await consumer.commit(msg(1))
    before = len(spy.commits)

    await consumer.commit(msg(0))  # redelivered/duplicate ack

    assert len(spy.commits) == before, "re-committed an already-committed offset"


async def test_partitions_advance_independently(wired):
    """A stalled partition must not hold up a healthy one."""
    consumer, spy = wired
    consumer._track(msg(0, partition=0))
    consumer._track(msg(0, partition=1))

    await consumer.commit(msg(0, partition=1))

    tp1 = TopicPartition("events.business", 1)
    tp0 = TopicPartition("events.business", 0)
    assert offsets_committed(spy, tp1) == [1]
    assert offsets_committed(spy, tp0) == []


async def test_topics_are_tracked_separately(wired):
    """events.business and events.internal.delivery share a consumer."""
    consumer, spy = wired
    consumer._track(msg(7, topic="events.business"))
    consumer._track(msg(7, topic="events.internal.delivery"))

    await consumer.commit(msg(7, topic="events.internal.delivery"))

    retry_tp = TopicPartition("events.internal.delivery", 0)
    business_tp = TopicPartition("events.business", 0)
    assert offsets_committed(spy, retry_tp) == [8]
    assert offsets_committed(spy, business_tp) == []


async def test_uncommitted_message_stays_pending_for_redelivery(wired):
    """
    A message never passed to commit() must remain unacknowledged -- this
    is what makes a crash or a graceful shutdown mid-work redeliver
    instead of dropping the event.
    """
    consumer, _spy = wired
    consumer._track(msg(4))
    consumer._track(msg(5))

    await consumer.commit(msg(4))

    assert consumer.pending_offsets() == {"events.business[0]": 1}


async def test_completed_set_is_pruned_after_commit(wired):
    """Long-running partitions must not accumulate offsets without bound."""
    consumer, _spy = wired
    for offset in range(50):
        consumer._track(msg(offset))
    for offset in range(50):
        await consumer.commit(msg(offset))

    progress = consumer._progress[TopicPartition("events.business", 0)]
    assert progress.completed == set(), "completed offsets accumulate without bound"
    assert progress.in_flight == set()
    assert progress.committed == 50


async def test_close_clears_progress(wired):
    consumer, _spy = wired
    consumer._track(msg(0))

    await consumer.close()

    assert consumer.pending_offsets() == {}
