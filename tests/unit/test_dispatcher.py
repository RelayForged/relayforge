import asyncio
import json
from datetime import datetime, timezone
import pytest

from relay.control_plane.aede.service import AEDEService
from relay.data_plane.dispatcher.main import Dispatcher
from relay.infrastructure.feature_flags import FeatureFlags
from relay.infrastructure.messaging.admin import EVENTS_INTERNAL_ENDPOINT_TOPIC
from relay.infrastructure.messaging.consumer import KafkaMessage
from relay.kernel.value_objects.endpoint_state import EndpointState


class DummyKafkaConsumer:
    def __init__(self, messages=None):
        self.messages = list(messages or [])
        self.committed = []

    async def start(self):
        pass

    async def close(self):
        pass

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self.messages:
            raise StopAsyncIteration
        return self.messages.pop(0)

    async def commit(self, message):
        self.committed.append(message)


class DummyRepo:
    def __init__(self):
        self.saved = []

    async def save(self, item):
        self.saved.append(item)
        return item

    async def get_by_id(self, item_id):
        return None


@pytest.mark.asyncio
async def test_dispatcher_accept_endpoint_restore():
    aede = AEDEService()
    endpoint_id = "ep-test-123"
    # Put endpoint in QUARANTINED state
    aede.set_endpoint_state(endpoint_id, EndpointState.QUARANTINED)
    assert aede.get_endpoint_state(endpoint_id) == EndpointState.QUARANTINED

    consumer = DummyKafkaConsumer()
    dispatcher = Dispatcher(
        consumer=consumer,
        subscriptions=DummyRepo(),
        deliveries=DummyRepo(),
        attempts=DummyRepo(),
        endpoints=DummyRepo(),
        feature_flags=FeatureFlags(aede_enabled=True),
        aede_service=aede,
    )

    # Simulate receiving an endpoint control restore message
    restore_msg = KafkaMessage(
        topic=EVENTS_INTERNAL_ENDPOINT_TOPIC,
        partition=0,
        offset=10,
        key=endpoint_id,
        value=json.dumps({"action": "restore", "endpoint_id": endpoint_id}).encode("utf-8"),
    )

    await dispatcher._accept(restore_msg)

    # Endpoint must have transitioned to RECOVERING
    assert aede.get_endpoint_state(endpoint_id) == EndpointState.RECOVERING
    # Kafka message committed
    assert len(consumer.committed) == 1
    assert consumer.committed[0].offset == 10


@pytest.mark.asyncio
async def test_dispatcher_wires_aede_to_webhook_worker():
    aede = AEDEService()
    dispatcher = Dispatcher(
        consumer=DummyKafkaConsumer(),
        subscriptions=DummyRepo(),
        deliveries=DummyRepo(),
        attempts=DummyRepo(),
        endpoints=DummyRepo(),
        feature_flags=FeatureFlags(aede_enabled=True),
        aede_service=aede,
    )

    # Worker must receive the aede_service
    assert dispatcher._worker._aede_service is aede
