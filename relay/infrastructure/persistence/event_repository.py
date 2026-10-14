"""
Repository for the `Event` entity.

Translates between the frozen Pydantic `Event` domain model
(relay/kernel/entities/event.py, owned by Person B) and rows in the
`events` table (relay/infrastructure/persistence/models.py). This is the
only module that should import both.

Append-only by design: only `save()` (INSERT) and `get_by_id()` (SELECT)
exist. There is no update/delete method, mirroring the DB-level trigger
from ADR 0011 -- the events table is never mutated, in code or in SQL.
"""
from typing import Optional

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncEngine

from relay.infrastructure.persistence.engine import get_engine
from relay.infrastructure.persistence.models import events_table
from relay.kernel.entities.event import Event
from relay.kernel.value_objects.identifiers import CausationID, CorrelationID


class EventRepository:
    def __init__(self, engine: Optional[AsyncEngine] = None) -> None:
        self._engine = engine or get_engine()

    async def save(self, event: Event) -> None:
        """
        Persist an Event. Idempotent on `event_id`: if the same Event is
        saved twice (e.g. a caller retries after a Kafka publish failure
        that occurred post-persist), the second call is a no-op rather
        than raising a unique-violation or mutating the existing row.
        """
        stmt = (
            pg_insert(events_table)
            .values(
                event_id=event.event_id,
                correlation_id=event.correlation_id.value,
                causation_id=event.causation_id.value,
                event_type=event.event_type,
                payload=event.payload,
                occurred_at=event.occurred_at,
                channels=list(event.channels),
            )
            .on_conflict_do_nothing(index_elements=["event_id"])
        )
        async with self._engine.begin() as conn:
            await conn.execute(stmt)

    async def get_by_id(self, event_id: str) -> Optional[Event]:
        """Load a single Event by its ID. Returns None if it does not exist."""
        stmt = select(events_table).where(events_table.c.event_id == event_id)
        async with self._engine.connect() as conn:
            result = await conn.execute(stmt)
            row = result.fetchone()

        if row is None:
            return None

        return Event(
            event_id=row.event_id,
            correlation_id=CorrelationID(value=row.correlation_id),
            causation_id=CausationID(value=row.causation_id),
            event_type=row.event_type,
            payload=row.payload,
            occurred_at=row.occurred_at,
            channels=list(row.channels or []),
        )
