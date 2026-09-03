"""
Thin Kafka consumer wrapper. Together with producer.py, this package is
the only place that may import `aiokafka` (ADR 0001, ADR 0003). The
Dispatcher and the ingestion API call this wrapper and never construct
an AIOKafkaConsumer.

Offsets are committed only when the caller asks -- persist-first (ADR
0002): commit after Postgres, not after HTTP, never via auto-commit.

Commits are *watermark* commits, not per-message commits. The Dispatcher
processes messages concurrently, so committing `message.offset + 1`
directly would acknowledge a later message while an earlier one is still
in flight -- losing it on restart. `complete()` records a message as done
and commits only the highest offset with no incomplete predecessor, which
is what makes redelivery-on-crash (at-least-once) actually hold.
"""
from dataclasses import dataclass, field
from typing import AsyncIterator, Dict, Optional, Sequence

from aiokafka import AIOKafkaConsumer, TopicPartition

from relay.infrastructure.messaging.admin import ensure_topics
from relay.infrastructure.messaging.config import get_kafka_bootstrap_servers

EVENTS_BUSINESS_TOPIC = "events.business"
EVENTS_INTERNAL_DELIVERY_TOPIC = "events.internal.delivery"


@dataclass(frozen=True)
class KafkaMessage:
    """aiokafka-free record so callers never import ConsumerRecord."""

    topic: str
    partition: int
    offset: int
    key: Optional[str]
    value: bytes


@dataclass
class _PartitionProgress:
    """In-flight and completed offsets for one TopicPartition."""

    in_flight: set = field(default_factory=set)
    completed: set = field(default_factory=set)
    committed: Optional[int] = None

    def watermark(self) -> Optional[int]:
        """
        The next offset safe to commit, or None if nothing is safe yet.

        An offset that is still in flight blocks everything after it: on
        restart we must resume from the oldest unfinished message.
        """
        if self.in_flight:
            return min(self.in_flight)
        if self.completed:
            return max(self.completed) + 1
        return None


class KafkaConsumer:
    """
    Generic async consumer. Subscribe to any topics; Dispatcher will pass
    `events.business` and `events.internal.delivery`.
    """

    def __init__(
        self,
        topics: Sequence[str],
        group_id: str,
        *,
        bootstrap_servers: Optional[str] = None,
    ) -> None:
        self._topics = tuple(topics)
        self._group_id = group_id
        self._bootstrap_servers = bootstrap_servers or get_kafka_bootstrap_servers()
        self._consumer: Optional[AIOKafkaConsumer] = None
        self._progress: Dict[TopicPartition, _PartitionProgress] = {}

    async def start(self) -> None:
        if self._consumer is not None:
            return
        await ensure_topics()
        consumer = AIOKafkaConsumer(
            *self._topics,
            bootstrap_servers=self._bootstrap_servers,
            group_id=self._group_id,
            enable_auto_commit=False,
            auto_offset_reset="earliest",
        )
        await consumer.start()
        self._consumer = consumer

    async def messages(self) -> AsyncIterator[KafkaMessage]:
        consumer = await self._ensure_started()
        async for record in consumer:
            key = record.key.decode("utf-8") if record.key is not None else None
            message = KafkaMessage(
                topic=record.topic,
                partition=record.partition,
                offset=record.offset,
                key=key,
                value=record.value,
            )
            self._track(message)
            yield message

    def __aiter__(self) -> AsyncIterator[KafkaMessage]:
        return self.messages()

    def _progress_for(self, message: KafkaMessage) -> _PartitionProgress:
        tp = TopicPartition(message.topic, message.partition)
        progress = self._progress.get(tp)
        if progress is None:
            progress = _PartitionProgress()
            self._progress[tp] = progress
        return progress

    def _track(self, message: KafkaMessage) -> None:
        self._progress_for(message).in_flight.add(message.offset)

    async def commit(self, message: KafkaMessage) -> None:
        """
        Mark `message` fully handled and commit the safe watermark.

        Call only after the corresponding Postgres write has committed. A
        message that is never passed here is never acknowledged, so it is
        redelivered after a restart -- that is the intended behaviour for
        both crashes and graceful shutdown mid-work.
        """
        consumer = await self._ensure_started()
        tp = TopicPartition(message.topic, message.partition)
        progress = self._progress_for(message)
        progress.in_flight.discard(message.offset)
        progress.completed.add(message.offset)

        watermark = progress.watermark()
        if watermark is None:
            return
        if progress.committed is not None and watermark <= progress.committed:
            return

        await consumer.commit({tp: watermark})
        progress.committed = watermark
        progress.completed = {
            offset for offset in progress.completed if offset >= watermark
        }

    def pending_offsets(self) -> Dict[str, int]:
        """Diagnostic: count of in-flight offsets per partition."""
        return {
            f"{tp.topic}[{tp.partition}]": len(progress.in_flight)
            for tp, progress in self._progress.items()
            if progress.in_flight
        }

    async def close(self) -> None:
        if self._consumer is not None:
            await self._consumer.stop()
            self._consumer = None
        self._progress.clear()

    async def _ensure_started(self) -> AIOKafkaConsumer:
        if self._consumer is None:
            await self.start()
        assert self._consumer is not None
        return self._consumer
