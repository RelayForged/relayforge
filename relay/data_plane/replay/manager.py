"""
Replay Manager — put undeliverable deliveries back into the pipeline
without rewriting what already happened (ADR 0021).

It selects deliveries and publishes one ReplayWork each to
`events.internal.replay`. It does not build policies, send HTTP, or
decide whether an endpoint may receive traffic: the Dispatcher consumes
the work through its normal dispatch path, so the Control Plane still
supplies the policy and a quarantined endpoint is still left alone.

Two cases, by why the delivery is stuck:

- PARKED (PENDING, never attempted, skipped under QUARANTINE; under PAUSE
  before ADR 0022):
  the original Event is dispatched again to the same subscription. The
  Delivery row already exists and its attempt 1 is unclaimed.
- FAILED / DEAD_LETTER: a new Event is created with the same type,
  payload and correlation id, caused by the original. It gets its own
  Delivery and attempt 1. The original rows are never touched.
"""
from datetime import datetime
import logging
from typing import Any, Awaitable, Callable, Optional, Sequence
from uuid import uuid4

from pydantic import BaseModel, ConfigDict

from relay.infrastructure.messaging.admin import EVENTS_INTERNAL_REPLAY_TOPIC
from relay.infrastructure.messaging.producer import publish
from relay.infrastructure.persistence.delivery_repository import (
    PARKED,
    DeadLetter,
    DeliveryRepository,
)
from relay.infrastructure.persistence.event_repository import EventRepository
from relay.kernel.entities.event import Event
from relay.kernel.value_objects.identifiers import CausationID, replay_event_id

logger = logging.getLogger(__name__)

DEFAULT_REPLAY_LIMIT = 50
MAX_REPLAY_LIMIT = 500


class ReplayWork(BaseModel):
    """
    Kafka payload for `events.internal.replay`. Not a kernel type.

    Carries the Event to dispatch and the one subscription it is for, so
    a replay never fans out to subscribers that already received it.
    """

    model_config = ConfigDict(frozen=True)

    replay_id: str
    source_delivery_id: str
    subscription_id: str
    event: Event


class SkippedReplay(BaseModel):
    delivery_id: str
    why: str


class ReplayResult(BaseModel):
    replay_id: str
    requested: int
    skipped: list[SkippedReplay]


class ReplayManager:
    def __init__(
        self,
        *,
        deliveries: Optional[DeliveryRepository] = None,
        events: Optional[EventRepository] = None,
        publisher: Optional[Callable[..., Awaitable[Any]]] = None,
    ) -> None:
        self._deliveries = deliveries or DeliveryRepository()
        self._events = events or EventRepository()
        self._publish = publisher or publish

    async def replay_dead_letters(
        self,
        *,
        now: datetime,
        endpoint_id: Optional[str] = None,
        delivery_ids: Optional[Sequence[str]] = None,
        limit: int = DEFAULT_REPLAY_LIMIT,
    ) -> ReplayResult:
        """
        Request replay of up to `limit` dead letters, oldest first.

        `limit` pages an open-ended request. When the caller names the
        deliveries, every named one is considered: truncating the list
        would report valid dead letters as skipped.

        Safe to call twice for the same delivery: the replay Event's id is
        derived from the source delivery id, so a repeat produces the same
        Event and the Dispatcher's idempotent writes absorb it.
        """
        replay_id = str(uuid4())
        if delivery_ids is not None:
            delivery_ids = list(dict.fromkeys(delivery_ids))[:MAX_REPLAY_LIMIT]
            limit = len(delivery_ids)
        candidates = await self._deliveries.get_dead_letters(
            now=now,
            endpoint_id=endpoint_id,
            delivery_ids=delivery_ids,
            limit=max(1, min(limit, MAX_REPLAY_LIMIT)),
        )

        skipped: list[SkippedReplay] = []
        if delivery_ids is not None:
            found = {c.delivery.delivery_id for c in candidates}
            skipped.extend(
                SkippedReplay(
                    delivery_id=delivery_id,
                    why="not a dead letter, already replayed, or not on this endpoint",
                )
                for delivery_id in delivery_ids
                if delivery_id not in found
            )

        requested = 0
        for candidate in candidates:
            delivery = candidate.delivery
            original = await self._events.get_by_id(delivery.event_id)
            if original is None:
                skipped.append(
                    SkippedReplay(
                        delivery_id=delivery.delivery_id, why="source event not found"
                    )
                )
                continue

            event = await self._event_to_dispatch(candidate, original, now)
            work = ReplayWork(
                replay_id=replay_id,
                source_delivery_id=delivery.delivery_id,
                subscription_id=delivery.subscription_id,
                event=event,
            )
            await self._publish(
                EVENTS_INTERNAL_REPLAY_TOPIC,
                key=delivery.endpoint_id,
                value=work.model_dump_json().encode("utf-8"),
            )
            requested += 1
            logger.info(
                "replay requested replay_id=%s source_delivery_id=%s reason=%s "
                "endpoint_id=%s event_id=%s correlation_id=%s",
                replay_id,
                delivery.delivery_id,
                candidate.reason,
                delivery.endpoint_id,
                event.event_id,
                event.correlation_id.value,
            )

        logger.info(
            "replay batch replay_id=%s endpoint_id=%s requested=%d skipped=%d",
            replay_id,
            endpoint_id,
            requested,
            len(skipped),
        )
        return ReplayResult(replay_id=replay_id, requested=requested, skipped=skipped)

    async def _event_to_dispatch(
        self, candidate: DeadLetter, original: Event, now: datetime
    ) -> Event:
        if candidate.reason == PARKED:
            return original

        replayed = Event(
            event_id=replay_event_id(candidate.delivery.delivery_id),
            correlation_id=original.correlation_id,
            causation_id=CausationID(value=original.event_id),
            event_type=original.event_type,
            payload=original.payload,
            occurred_at=now,
            # Same tenant routing as the original (ADR 0025).
            channels=list(original.channels),
        )
        # Persist-first, as for any event: nothing reaches Kafka that is
        # not already in Postgres. save() is a no-op if it already exists.
        await self._events.save(replayed)
        return replayed
