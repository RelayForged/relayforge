"""
Thin Kafka producer wrapper. This is the ONLY module in the codebase that
may import `aiokafka` (ADR 0001, ADR 0003). The ingestion API and the
smoke script call `publish()` and never touch the Kafka client directly,
so the messaging backbone can be swapped without touching calling code.

Async, matching the async EventRepository (ADR 0006) -- both are awaited
from the same ingestion path.
"""
import asyncio
from typing import Optional

from aiokafka import AIOKafkaProducer

from relay.infrastructure.messaging.admin import ensure_topics
from relay.infrastructure.messaging.config import get_kafka_bootstrap_servers

EVENTS_BUSINESS_TOPIC = "events.business"

_producer: Optional[AIOKafkaProducer] = None
_producer_lock: Optional[asyncio.Lock] = None
_producer_loop: Optional[asyncio.AbstractEventLoop] = None


def _get_producer_lock() -> asyncio.Lock:
    global _producer_lock, _producer_loop
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if _producer_lock is None or _producer_loop is not loop or (loop is not None and loop.is_closed()):
        _producer_lock = asyncio.Lock()
        _producer_loop = loop
    return _producer_lock


async def _get_producer() -> AIOKafkaProducer:
    """
    Lazily start the process-wide producer on first use.

    Guarded by a lock: there are two await points between the None check
    and the assignment, so concurrent first-time callers would otherwise
    each start a producer and orphan one -- its buffered records would
    never be flushed.

    acks="all" + idempotence is required by the durability contract (ADR
    0002). With the default acks=1 a leader failover between ack and
    replication silently drops an event the API already accepted with 202.
    """
    global _producer
    loop = asyncio.get_running_loop()
    if (
        _producer is not None
        and getattr(_producer, "_loop", None) is loop
        and not loop.is_closed()
    ):
        return _producer

    async with _get_producer_lock():
        if (
            _producer is None
            or getattr(_producer, "_loop", None) is not loop
            or loop.is_closed()
        ):
            await ensure_topics()
            producer = AIOKafkaProducer(
                bootstrap_servers=get_kafka_bootstrap_servers(),
                acks="all",
                enable_idempotence=True,
            )
            await producer.start()
            _producer = producer
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


async def warmup() -> None:
    """
    Eagerly start the process-wide producer at application startup.

    `_get_producer()` normally runs lazily on the first `publish()` call --
    fine in principle, except topic admin checks (`ensure_topics()`) plus
    `AIOKafkaProducer.start()`'s metadata bootstrap measured ~8s combined
    against a real broker (4s each), cold. Callers with their own timeout
    around a `publish()` call (e.g. AEDEService's record_telemetry) can
    silently time out on exactly that first call if nothing warmed the
    producer first. Call this once from the process's startup hook so the
    ~8s cost is paid once, eagerly, well before any real event needs it.
    """
    await _get_producer()
