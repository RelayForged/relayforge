"""
Simulated AEDE outage (ADR 0024): the operator switch that demonstrates the
three-tier policy fallback on a running Dispatcher.
"""
import json

from relay.infrastructure.cache import InMemoryPolicyCache, PolicyFallbackManager
from relay.infrastructure.messaging.admin import (
    EVENTS_INTERNAL_ENDPOINT_TOPIC,
    EVENTS_INTERNAL_POLICY_TOPIC,
)
from relay.infrastructure.messaging.consumer import KafkaMessage

from tests.data_plane._helpers import make_event, make_target
from tests.data_plane.test_dispatcher_states import (
    RecordingWorker,
    build_dispatcher,
    business_message,
)


def control(payload: dict, offset: int = 0) -> KafkaMessage:
    return KafkaMessage(
        topic=EVENTS_INTERNAL_ENDPOINT_TOPIC,
        partition=0,
        offset=offset,
        key="aede",
        value=json.dumps(payload).encode("utf-8"),
    )


class FakeAEDE:
    def __init__(self) -> None:
        self.restores: list[str] = []

    async def restore_and_publish(self, endpoint_id, restored_at):
        self.restores.append(endpoint_id)


def outage_harness(deliveries, attempts, consumer):
    cache = InMemoryPolicyCache()
    subscription, endpoint = make_target("OrderCreated")
    worker = RecordingWorker()
    dispatcher = build_dispatcher(
        targets=[(subscription, endpoint)],
        deliveries=deliveries,
        attempts=attempts,
        consumer=consumer,
        worker=worker,
        cache=cache,
        fallback_manager=PolicyFallbackManager(cache=cache),
    )
    published = []

    async def fake_publish(topic, key, value):
        published.append((topic, key, json.loads(value)))

    dispatcher._tier_publisher = fake_publish
    return dispatcher, cache, endpoint, published


async def test_outage_walks_down_the_tiers_and_back(deliveries, attempts, consumer):
    dispatcher, cache, endpoint, published = outage_harness(deliveries, attempts, consumer)

    await dispatcher.handle_message(business_message(make_event("OrderCreated"), 0))
    assert published == [], "normal Tier 1 service is not announced"

    await dispatcher._accept(control({"action": "aede_availability", "available": False}))
    await dispatcher.handle_message(business_message(make_event("OrderCreated"), 1))

    cache.clear()  # the cached policy has expired
    await dispatcher.handle_message(business_message(make_event("OrderCreated"), 2))

    await dispatcher._accept(control({"action": "aede_availability", "available": True}, 1))
    await dispatcher.handle_message(business_message(make_event("OrderCreated"), 3))

    assert [p[2]["tier"] for p in published] == [2, 3, 1]
    assert {p[0] for p in published} == {EVENTS_INTERNAL_POLICY_TOPIC}
    assert {p[2]["endpoint_id"] for p in published} == {endpoint.endpoint_id}
    assert all(p[2]["type"] == "policy_tier" for p in published)


async def test_deliveries_continue_during_the_outage(deliveries, attempts, consumer):
    """Tier 3 is degraded delivery, not a platform halt."""
    dispatcher, cache, _, _ = outage_harness(deliveries, attempts, consumer)
    await dispatcher._accept(control({"action": "aede_availability", "available": False}))

    await dispatcher.handle_message(business_message(make_event("OrderCreated"), 0))

    assert len(dispatcher._worker.calls) == 1


async def test_outage_cuts_aede_off_from_telemetry(deliveries, attempts, consumer):
    dispatcher, _, _, _ = outage_harness(deliveries, attempts, consumer)
    aede = FakeAEDE()
    dispatcher._aede_service = aede
    dispatcher._worker._aede_service = aede

    await dispatcher._accept(control({"action": "aede_availability", "available": False}))
    assert dispatcher._worker._aede_service is None

    await dispatcher._accept(control({"action": "aede_availability", "available": True}, 1))
    assert dispatcher._worker._aede_service is aede


async def test_restore_is_ignored_while_aede_is_down(deliveries, attempts, consumer):
    dispatcher, _, _, _ = outage_harness(deliveries, attempts, consumer)
    aede = FakeAEDE()
    dispatcher._aede_service = aede

    await dispatcher._accept(control({"action": "aede_availability", "available": False}))
    await dispatcher._accept(control({"action": "restore", "endpoint_id": "ep-1"}, 1))

    assert aede.restores == []
    assert consumer.committed == [0, 1]


def test_cache_ttl_comes_from_the_environment(monkeypatch):
    from relay.data_plane.dispatcher.main import Dispatcher

    monkeypatch.setenv("RELAY_POLICY_CACHE_TTL_SECONDS", "30")
    assert Dispatcher()._policy_cache_ttl == 30


def test_aede_writes_the_cache_with_the_same_ttl(monkeypatch):
    """AEDE's own last cache write must expire on the outage schedule too."""
    from relay.control_plane.aede.service import AEDEService

    monkeypatch.setenv("RELAY_POLICY_CACHE_TTL_SECONDS", "30")
    assert AEDEService(publish_events=False)._policy_cache_ttl == 30
