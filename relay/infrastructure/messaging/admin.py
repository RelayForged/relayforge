"""
Idempotent Kafka topic bootstrap.

The Dispatcher subscribes to retry topics at startup, so topics must exist
before AIOKafkaConsumer.start() — broker auto-create is not enough.
This module is the only supported way to create platform topics (not
docker exec, not the smoke script).
"""
import asyncio
from typing import Optional

from aiokafka.admin import AIOKafkaAdminClient, NewTopic
from aiokafka.errors import TopicAlreadyExistsError

from relay.infrastructure.messaging.config import (
    get_kafka_bootstrap_servers,
    get_kafka_topic_partitions,
    get_kafka_topic_replication_factor,
)

EVENTS_BUSINESS_TOPIC = "events.business"
EVENTS_INTERNAL_DELIVERY_TOPIC = "events.internal.delivery"
EVENTS_INTERNAL_POLICY_TOPIC = "events.internal.policy"
EVENTS_INTERNAL_ENDPOINT_TOPIC = "events.internal.endpoint"

PLATFORM_TOPICS = (
    EVENTS_BUSINESS_TOPIC,
    EVENTS_INTERNAL_DELIVERY_TOPIC,
    EVENTS_INTERNAL_POLICY_TOPIC,
    EVENTS_INTERNAL_ENDPOINT_TOPIC,
)

_ensured = False
_lock: Optional[asyncio.Lock] = None
_lock_loop: Optional[asyncio.AbstractEventLoop] = None


def _get_lock() -> asyncio.Lock:
    global _lock, _lock_loop
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if _lock is None or _lock_loop is not loop or (loop is not None and loop.is_closed()):
        _lock = asyncio.Lock()
        _lock_loop = loop
    return _lock


async def ensure_topics() -> None:
    """Create named platform topics if they are missing. Safe to call often."""
    global _ensured
    async with _get_lock():
        if _ensured:
            return
        admin = AIOKafkaAdminClient(bootstrap_servers=get_kafka_bootstrap_servers())
        await admin.start()
        try:
            existing = set(await admin.list_topics())
            missing = [name for name in PLATFORM_TOPICS if name not in existing]
            if missing:
                partitions = get_kafka_topic_partitions()
                replication = get_kafka_topic_replication_factor()
                new_topics = [
                    NewTopic(
                        name=name,
                        num_partitions=partitions,
                        replication_factor=replication,
                    )
                    for name in missing
                ]
                try:
                    await admin.create_topics(new_topics)
                except TopicAlreadyExistsError:
                    pass
            _ensured = True
        finally:
            await admin.close()
