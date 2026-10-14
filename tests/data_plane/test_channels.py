"""
Channel routing (ADR 0025): one tenant's events reach only that tenant's
subscriptions, and subscriptions without channels behave as before.
"""
import os
from uuid import uuid4

import pytest
from pydantic import ValidationError

from relay.api.schemas import CreateSubscriptionRequest, IngestEventRequest
from relay.kernel.entities.event import Event
from relay.kernel.entities.subscription import Subscription
from relay.kernel.value_objects.identifiers import CausationID, CorrelationID

from tests.data_plane._helpers import make_target
from tests.data_plane.test_dispatcher_states import (
    RecordingWorker,
    build_dispatcher,
    business_message,
)


def event_on(*channels: str, event_type: str = "payment.captured") -> Event:
    correlation_id = CorrelationID()
    return Event(
        correlation_id=correlation_id,
        causation_id=CausationID(value=correlation_id.value),
        event_type=event_type,
        payload={"amount": 499},
        channels=list(channels),
    )


# --------------------------------------------------------------------------
# The rule
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "subscribed, event_channels, delivered",
    [
        ([], [], True),             # neither uses channels: unchanged behaviour
        ([], ["acme"], True),       # an unrestricted subscription gets everything
        (["acme"], ["acme"], True),
        (["acme"], ["bluebird"], False),
        (["acme"], [], False),      # a restricted subscription ignores untagged events
        (["acme", "ops"], ["ops"], True),
    ],
)
def test_receives(subscribed, event_channels, delivered) -> None:
    subscription = Subscription(
        endpoint_id="ep-1", event_types=["payment.captured"], channels=subscribed
    )
    assert subscription.receives(event_channels) is delivered


# --------------------------------------------------------------------------
# Fan-out
# --------------------------------------------------------------------------


def two_merchants():
    acme_sub, acme_ep = make_target(
        "payment.captured", target_url="https://acme.example.test/webhook"
    )
    blue_sub, blue_ep = make_target(
        "payment.captured", target_url="https://bluebird.example.test/webhook"
    )
    acme_sub = acme_sub.model_copy(update={"channels": ["acme"]})
    blue_sub = blue_sub.model_copy(update={"channels": ["bluebird"]})
    return [(acme_sub, acme_ep), (blue_sub, blue_ep)]


async def test_fan_out_delivers_only_to_the_matching_merchant(deliveries, attempts, consumer):
    worker = RecordingWorker()
    dispatcher = build_dispatcher(
        targets=two_merchants(),
        deliveries=deliveries,
        attempts=attempts,
        consumer=consumer,
        worker=worker,
    )

    await dispatcher.handle_message(business_message(event_on("acme")))

    assert [call["target_url"] for call in worker.calls] == [
        "https://acme.example.test/webhook"
    ]


async def test_untagged_event_skips_channel_subscriptions(deliveries, attempts, consumer):
    worker = RecordingWorker()
    open_sub, open_ep = make_target(
        "payment.captured", target_url="https://audit.example.test/webhook"
    )
    dispatcher = build_dispatcher(
        targets=two_merchants() + [(open_sub, open_ep)],
        deliveries=deliveries,
        attempts=attempts,
        consumer=consumer,
        worker=worker,
    )

    await dispatcher.handle_message(business_message(event_on()))

    assert [call["target_url"] for call in worker.calls] == [
        "https://audit.example.test/webhook"
    ]


# --------------------------------------------------------------------------
# API boundary
# --------------------------------------------------------------------------


def test_channels_are_optional_and_deduplicated() -> None:
    assert IngestEventRequest(event_type="x", payload={}).channels == []
    request = CreateSubscriptionRequest(
        endpoint_id="ep-1", event_types=["x"], channels=["acme", "acme", "ops"]
    )
    assert request.channels == ["acme", "ops"]


@pytest.mark.parametrize(
    "channels", [[""], ["has space"], ["a" * 65], [f"c{i}" for i in range(11)]]
)
def test_bad_channels_are_rejected(channels) -> None:
    with pytest.raises(ValidationError):
        IngestEventRequest(event_type="x", payload={}, channels=channels)


# --------------------------------------------------------------------------
# Replay keeps the routing
# --------------------------------------------------------------------------


async def test_replay_event_keeps_the_original_channels() -> None:
    from tests.data_plane.test_replay import NOW, dead_letter, manager_for

    original = event_on("acme")
    manager, published, _ = manager_for([dead_letter(original, reason="FAILED")], [original])

    await manager.replay_dead_letters(now=NOW)

    assert published[0][2].event.channels == ["acme"]


# --------------------------------------------------------------------------
# Postgres (skipped unless the compose database is reachable)
# --------------------------------------------------------------------------

TEST_DATABASE_URL = os.environ.get(
    "RELAY_TEST_DATABASE_URL",
    "postgresql+asyncpg://relay:relaypassword@127.0.0.1:5434/relaydb",
)


@pytest.fixture
async def engine():
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(TEST_DATABASE_URL)
    try:
        async with engine.connect() as conn:
            has_column = await conn.scalar(
                text(
                    "select count(*) from information_schema.columns "
                    "where table_name = 'subscriptions' and column_name = 'channels'"
                )
            )
    except Exception:
        await engine.dispose()
        pytest.skip("compose Postgres not reachable")
    if not has_column:
        await engine.dispose()
        pytest.skip("channels migration not applied")
    yield engine
    await engine.dispose()


async def test_postgres_fan_out_query_routes_by_channel(engine) -> None:
    from relay.infrastructure.persistence.endpoint_repository import EndpointRepository
    from relay.infrastructure.persistence.event_repository import EventRepository
    from relay.infrastructure.persistence.subscription_repository import (
        SubscriptionRepository,
    )
    from relay.kernel.entities.endpoint import Endpoint

    event_type = f"test.channels.{uuid4().hex[:8]}"
    endpoints = EndpointRepository(engine)
    subscriptions = SubscriptionRepository(engine)
    targets = {}
    saved = {}
    for name, channels in (("acme", ["acme"]), ("bluebird", ["bluebird"]), ("audit", [])):
        endpoint = Endpoint(target_url=f"https://{name}.example.test/webhook", owner_id="test")
        await endpoints.save(endpoint)
        subscription = Subscription(
            endpoint_id=endpoint.endpoint_id, event_types=[event_type], channels=channels
        )
        await subscriptions.save(subscription)
        targets[endpoint.endpoint_id] = name
        saved[name] = subscription.subscription_id

    async def routed(channels):
        rows = await subscriptions.list_active_targets_by_event_type(event_type, channels)
        return sorted(targets[endpoint.endpoint_id] for _, endpoint in rows)

    assert await routed(["acme"]) == ["acme", "audit"]
    assert await routed(["bluebird"]) == ["audit", "bluebird"]
    assert await routed([]) == ["audit"]

    assert (await subscriptions.get_by_id(saved["acme"])).channels == ["acme"]

    event = event_on("acme", event_type=event_type)
    events = EventRepository(engine)
    await events.save(event)
    assert (await events.get_by_id(event.event_id)).channels == ["acme"]
