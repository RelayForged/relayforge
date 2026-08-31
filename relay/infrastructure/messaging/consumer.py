"""
Thin Kafka consumer wrapper. Together with producer.py, this package is
the only place that may import `aiokafka` (ADR 0001, ADR 0003). The
Dispatcher and the ingestion API call this wrapper and never construct
an AIOKafkaConsumer.

Offsets are committed only when the caller asks -- persist-first (ADR
0002): commit after Postgres, not after HTTP, never via auto-commit.
"""
from dataclasses import dataclass
from typing import AsyncIterator, Optional, Sequence

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
            yield KafkaMessage(
                topic=record.topic,
                partition=record.partition,
                offset=record.offset,
                key=key,
                value=record.value,
            )

    def __aiter__(self) -> AsyncIterator[KafkaMessage]:
        return self.messages()

    async def commit(self, message: KafkaMessage) -> None:
        """
        Advance the committed offset past `message`. Call only after the
        corresponding Postgres write has committed.
        """
        consumer = await self._ensure_started()
        tp = TopicPartition(message.topic, message.partition)
        await consumer.commit({tp: message.offset + 1})

    async def close(self) -> None:
        if self._consumer is not None:
            await self._consumer.stop()
            self._consumer = None

    async def _ensure_started(self) -> AIOKafkaConsumer:
        if self._consumer is None:
            await self.start()
        assert self._consumer is not None
        return self._consumer
