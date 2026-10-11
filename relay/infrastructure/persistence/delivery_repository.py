"""
Repository for the `Delivery` entity.

Translates between the frozen Pydantic `Delivery` domain model
(relay/kernel/entities/delivery.py, owned by Person B) and rows in the
`deliveries` table. One Delivery per (event_id, subscription_id).

`save()` is idempotent on that pair: a Kafka redelivery that constructs a
new delivery_id is a no-op. A status transition via
`delivery.model_copy(update={"status": ...})` with the *same* delivery_id
is applied (Delivery is a lifecycle aggregate, not an append-only fact —
see ADR 0014).
"""
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional, Sequence

from sqlalchemy import Row, and_, exists, func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncEngine

from relay.infrastructure.persistence.engine import get_engine
from relay.infrastructure.persistence.models import (
    deliveries_table,
    delivery_attempts_table,
    events_table,
)
from relay.kernel.entities.delivery import Delivery

# Why a delivery is in the dead-letter view. FAILED and DEAD_LETTER are
# its stored status; PARKED is a PENDING delivery that was never attempted.
PARKED = "PARKED"
# A PENDING delivery gets its first attempt row within milliseconds when it
# is going to be sent. Older than this with no attempt means it was skipped.
PARKED_GRACE_SECONDS = 5.0


@dataclass(frozen=True)
class DeadLetter:
    """A delivery that will not complete on its own, with why."""

    delivery: Delivery
    event_type: str
    reason: str
    attempt_count: int
    last_error: Optional[str]
    failed_at: datetime


def _to_delivery(row: Row) -> Delivery:
    return Delivery(
        delivery_id=row.delivery_id,
        event_id=row.event_id,
        subscription_id=row.subscription_id,
        endpoint_id=row.endpoint_id,
        created_at=row.created_at,
        status=row.status,
    )


class DeliveryRepository:
    def __init__(self, engine: Optional[AsyncEngine] = None) -> None:
        self._engine = engine or get_engine()

    async def save(self, delivery: Delivery) -> Optional[Delivery]:
        """
        Persist a Delivery. Idempotent on (event_id, subscription_id):
        a second insert for the same pair with a different delivery_id is
        a no-op. If the conflicting row has the same delivery_id, `status`
        is updated so Retry Manager can mark SUCCESS / FAILED / DEAD_LETTER
        via model_copy + re-save. created_at and identity FKs are never
        overwritten.

        Returns the row as written, or None when the upsert was a
        deliberate no-op (the canonical row has a different delivery_id).
        RETURNING removes the read-back the Dispatcher used to issue on
        every single fan-out target.
        """
        stmt = pg_insert(deliveries_table).values(
            delivery_id=delivery.delivery_id,
            event_id=delivery.event_id,
            subscription_id=delivery.subscription_id,
            endpoint_id=delivery.endpoint_id,
            created_at=delivery.created_at,
            status=delivery.status,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["event_id", "subscription_id"],
            set_={"status": stmt.excluded.status},
            where=deliveries_table.c.delivery_id == stmt.excluded.delivery_id,
        ).returning(deliveries_table)
        async with self._engine.begin() as conn:
            result = await conn.execute(stmt)
            row = result.fetchone()

        return _to_delivery(row) if row is not None else None

    async def get_by_id(self, delivery_id: str) -> Optional[Delivery]:
        """Load a single Delivery by its ID. Returns None if it does not exist."""
        stmt = select(deliveries_table).where(
            deliveries_table.c.delivery_id == delivery_id
        )
        async with self._engine.connect() as conn:
            result = await conn.execute(stmt)
            row = result.fetchone()

        return _to_delivery(row) if row is not None else None

    async def get_dead_letters(
        self,
        *,
        now: datetime,
        endpoint_id: Optional[str] = None,
        delivery_ids: Optional[Sequence[str]] = None,
        limit: int = 50,
    ) -> list[DeadLetter]:
        """
        Deliveries that need an operator: exhausted (FAILED), undeliverable
        (DEAD_LETTER), or parked (PENDING with no attempt, because the
        endpoint was paused or quarantined when the event arrived and
        nothing re-dispatches it). Oldest first. Read-only.

        A FAILED or DEAD_LETTER delivery that has already been replayed is
        left out: its replay is a new Event caused by this one, with its
        own Delivery for the same subscription (ADR 0021), and that
        Delivery is what appears here if it fails in turn.
        """
        d = deliveries_table
        a = delivery_attempts_table

        stats = (
            select(
                a.c.delivery_id,
                func.count().label("attempt_count"),
                func.max(a.c.completed_at).label("last_completed_at"),
            )
            .group_by(a.c.delivery_id)
            .subquery()
        )
        last_error = (
            select(a.c.error_message)
            .where(a.c.delivery_id == d.c.delivery_id, a.c.completed_at.is_not(None))
            .order_by(a.c.attempt_number.desc())
            .limit(1)
            .correlate(d)
            .scalar_subquery()
        )
        child_event = events_table.alias("child_event")
        child_delivery = deliveries_table.alias("child_delivery")
        replayed = exists(
            select(child_event.c.event_id)
            .select_from(
                child_event.join(
                    child_delivery,
                    child_delivery.c.event_id == child_event.c.event_id,
                )
            )
            .where(
                child_event.c.causation_id == d.c.event_id,
                child_delivery.c.subscription_id == d.c.subscription_id,
            )
        )
        parked_before = now - timedelta(seconds=PARKED_GRACE_SECONDS)

        stmt = (
            select(
                d,
                events_table.c.event_type,
                func.coalesce(stats.c.attempt_count, 0).label("attempt_count"),
                stats.c.last_completed_at,
                last_error.label("last_error"),
            )
            .join(events_table, events_table.c.event_id == d.c.event_id)
            .outerjoin(stats, stats.c.delivery_id == d.c.delivery_id)
            .where(
                or_(
                    and_(d.c.status.in_(("FAILED", "DEAD_LETTER")), ~replayed),
                    and_(
                        d.c.status == "PENDING",
                        stats.c.delivery_id.is_(None),
                        d.c.created_at < parked_before,
                    ),
                )
            )
            .order_by(d.c.created_at, d.c.delivery_id)
            .limit(limit)
        )
        if endpoint_id is not None:
            stmt = stmt.where(d.c.endpoint_id == endpoint_id)
        if delivery_ids is not None:
            stmt = stmt.where(d.c.delivery_id.in_(list(delivery_ids)))

        async with self._engine.connect() as conn:
            result = await conn.execute(stmt)
            rows = result.fetchall()

        return [
            DeadLetter(
                delivery=_to_delivery(row),
                event_type=row.event_type,
                reason=PARKED if row.status == "PENDING" else row.status,
                attempt_count=row.attempt_count,
                last_error=row.last_error,
                failed_at=row.last_completed_at or row.created_at,
            )
            for row in rows
        ]

    async def get_by_event_and_subscription(
        self, event_id: str, subscription_id: str
    ) -> Optional[Delivery]:
        """The canonical Delivery for a fan-out target, if one exists."""
        stmt = select(deliveries_table).where(
            deliveries_table.c.event_id == event_id,
            deliveries_table.c.subscription_id == subscription_id,
        )
        async with self._engine.connect() as conn:
            result = await conn.execute(stmt)
            row = result.fetchone()

        return _to_delivery(row) if row is not None else None
