"""
Thin Kafka producer wrapper. This is the ONLY module in the codebase that
may import `aiokafka` (ADR 0001, ADR 0003). The ingestion API and the
smoke script call `publish()` and never touch the Kafka client directly,
so the messaging backbone can be swapped without touching calling code.

Async, matching the async EventRepository (ADR 0006) -- both are awaited
from the same ingestion path.
"""
from typing import Optional

from aiokafka import AIOKafkaProducer

from relay.infrastructure.messaging.admin import ensure_topics
from relay.infrastructure.messaging.config import get_kafka_bootstrap_servers

EVENTS_BUSINESS_TOPIC = "events.business"

_producer: Optional[AIOKafkaProducer] = None


async def _get_producer() -> AIOKafkaProducer:
    """Lazily start the process-wide producer on first use."""
    global _producer
    if _producer is None:
        await ensure_topics()
        _producer = AIOKafkaProducer(bootstrap_servers=get_kafka_bootstrap_servers())
        await _producer.start()
    return _producer


async def publish(topic: str, key: str, value: bytes) -> None:
    """
    Publish a message to a Kafka topic.

    Callers never construct Kafka client objects themselves -- they only
    ever call this function. `key` is typically `event_id`, which keeps
    per-event ordering (same key -> same partition).
    """
    producer = await _get_producer()
    await producer.send_and_wait(topic, key=key.encode("utf-8"), value=value)


async def close() -> None:
    """Flush and stop the producer. Call on application shutdown."""
    global _producer
    if _producer is not None:
        await _producer.stop()
        _producer = None
