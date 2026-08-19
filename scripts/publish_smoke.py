"""
Week 1 smoke script: proves the durable pipe works end-to-end.

    Event -> Postgres (EventRepository.save) -> Kafka (producer.publish)

No API involved -- this calls the real repository and producer wrapper
directly, exactly as `POST /events` will in Week 2. Run with:

    .venv/bin/python scripts/publish_smoke.py
"""
import asyncio

from relay.infrastructure.messaging.producer import EVENTS_BUSINESS_TOPIC, close, publish
from relay.infrastructure.persistence.event_repository import EventRepository
from relay.kernel.entities.event import Event
from relay.kernel.value_objects.identifiers import CausationID, CorrelationID


async def main() -> None:
    correlation_id = CorrelationID()
    causation_id = CausationID(value=correlation_id.value)

    event = Event(
        correlation_id=correlation_id,
        causation_id=causation_id,
        event_type="OrderCreated",
        payload={"order_id": "smoke-test-order", "amount": 42},
    )

    repo = EventRepository()
    await repo.save(event)

    await publish(
        EVENTS_BUSINESS_TOPIC,
        key=event.event_id,
        value=event.model_dump_json().encode("utf-8"),
    )
    await close()

    print(f"event_id={event.event_id}")


if __name__ == "__main__":
    asyncio.run(main())
