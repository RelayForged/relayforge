"""
Event ingestion routes.

Persist-first (ADR 0001, ADR 0002): validate -> Postgres -> Kafka -> 202.
Kafka is never published unless the row is committed. This handler does
not retry HTTP or Kafka (ADR 0005) -- a failed publish after persist is
returned as 503; recovery is a later concern.
"""
from fastapi import APIRouter, Depends, HTTPException, status

from relay.api.deps import get_event_repository
from relay.api.schemas import IngestEventRequest, IngestEventResponse
from relay.api.security import require_owner
from relay.infrastructure.messaging.producer import EVENTS_BUSINESS_TOPIC, publish
from relay.infrastructure.persistence.event_repository import EventRepository
from relay.kernel.entities.event import Event
from relay.kernel.value_objects.identifiers import CausationID, CorrelationID

router = APIRouter(tags=["events"])


def build_event(event_type: str, payload: dict) -> Event:
    correlation_id = CorrelationID()
    return Event(
        correlation_id=correlation_id,
        causation_id=CausationID(value=correlation_id.value),
        event_type=event_type,
        payload=payload,
    )


async def persist_and_publish(event: Event, repo: EventRepository) -> None:
    """
    The one ingest path: Postgres first, then Kafka. Shared by the HTTP
    route and the demo auto-publisher so no caller can put an event on
    the log that was never persisted.
    """
    await repo.save(event)
    await publish(
        EVENTS_BUSINESS_TOPIC,
        key=event.event_id,
        value=event.model_dump_json().encode("utf-8"),
    )


@router.post(
    "/events",
    response_model=IngestEventResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def ingest_event(
    body: IngestEventRequest,
    _owner_id: str = Depends(require_owner),
    repo: EventRepository = Depends(get_event_repository),
) -> IngestEventResponse:
    event = build_event(body.event_type, body.payload)

    await repo.save(event)

    try:
        await publish(
            EVENTS_BUSINESS_TOPIC,
            key=event.event_id,
            value=event.model_dump_json().encode("utf-8"),
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "message": "Event persisted but Kafka publish failed. Do not treat as accepted.",
                "event_id": event.event_id,
            },
        ) from exc

    return IngestEventResponse(event_id=event.event_id)


@router.get("/events/{event_id}")
async def get_event(
    event_id: str,
    _owner_id: str = Depends(require_owner),
    repo: EventRepository = Depends(get_event_repository),
) -> Event:
    event = await repo.get_by_id(event_id)
    if event is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Event {event_id} not found",
        )
    return event
