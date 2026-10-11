"""
Repository for the `DeliveryAttempt` entity.

Retries are new rows (new attempt_id / attempt_number). DELETE is
forbidden at the DB (ADR 0014). Completing an in-flight attempt is a
sanctioned UPDATE of outcome columns only, via model_copy + save().

Two distinct write paths, because Postgres ON CONFLICT takes one target:

- `save()` conflicts on `attempt_id` — the Worker completing an attempt
  it already holds.
- `insert_if_absent()` conflicts on (delivery_id, attempt_number) — the
  Dispatcher/Retry Manager claiming an attempt slot. Without this, those
  callers had to read every attempt for the delivery just to find out
  whether one row existed.
"""
from typing import Optional

from sqlalchemy import Row, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncEngine

from relay.infrastructure.persistence.engine import get_engine
from relay.infrastructure.persistence.models import (
    deliveries_table,
    delivery_attempts_table,
)
from relay.kernel.entities.delivery import Delivery, DeliveryAttempt


def _to_attempt(row: Row) -> DeliveryAttempt:
    return DeliveryAttempt(
        attempt_id=row.attempt_id,
        delivery_id=row.delivery_id,
        attempt_number=row.attempt_number,
        started_at=row.started_at,
        completed_at=row.completed_at,
        http_status=row.http_status,
        success=row.success,
        error_message=row.error_message,
    )


class DeliveryAttemptRepository:
    def __init__(self, engine: Optional[AsyncEngine] = None) -> None:
        self._engine = engine or get_engine()

    async def save(self, attempt: DeliveryAttempt) -> Optional[DeliveryAttempt]:
        """
        Persist a DeliveryAttempt. Insert if new. If the same `attempt_id`
        already exists (Dispatcher persist-first row) and `completed_at`
        is still null, fill in outcome columns from the Worker's
        model_copy. Identity fields and a completed row are never rewritten.

        Returns the stored row, or None when the row was already completed
        and the update was therefore skipped.
        """
        stmt = pg_insert(delivery_attempts_table).values(
            attempt_id=attempt.attempt_id,
            delivery_id=attempt.delivery_id,
            attempt_number=attempt.attempt_number,
            started_at=attempt.started_at,
            completed_at=attempt.completed_at,
            http_status=attempt.http_status,
            success=attempt.success,
            error_message=attempt.error_message,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["attempt_id"],
            set_={
                "completed_at": stmt.excluded.completed_at,
                "http_status": stmt.excluded.http_status,
                "success": stmt.excluded.success,
                "error_message": stmt.excluded.error_message,
            },
            where=delivery_attempts_table.c.completed_at.is_(None),
        ).returning(delivery_attempts_table)
        async with self._engine.begin() as conn:
            result = await conn.execute(stmt)
            row = result.fetchone()

        return _to_attempt(row) if row is not None else None

    async def insert_if_absent(
        self, attempt: DeliveryAttempt
    ) -> Optional[DeliveryAttempt]:
        """
        Claim the (delivery_id, attempt_number) slot.

        Returns the inserted row, or None if that attempt number already
        exists — in which case the caller must decide whether to reuse it
        (still in flight) or stand down (already completed).
        """
        stmt = (
            pg_insert(delivery_attempts_table)
            .values(
                attempt_id=attempt.attempt_id,
                delivery_id=attempt.delivery_id,
                attempt_number=attempt.attempt_number,
                started_at=attempt.started_at,
                completed_at=attempt.completed_at,
                http_status=attempt.http_status,
                success=attempt.success,
                error_message=attempt.error_message,
            )
            .on_conflict_do_nothing(
                index_elements=["delivery_id", "attempt_number"],
            )
            .returning(delivery_attempts_table)
        )
        async with self._engine.begin() as conn:
            result = await conn.execute(stmt)
            row = result.fetchone()

        return _to_attempt(row) if row is not None else None

    async def get_by_id(self, attempt_id: str) -> Optional[DeliveryAttempt]:
        """Load a single DeliveryAttempt by its ID. Returns None if it does not exist."""
        stmt = select(delivery_attempts_table).where(
            delivery_attempts_table.c.attempt_id == attempt_id
        )
        async with self._engine.connect() as conn:
            result = await conn.execute(stmt)
            row = result.fetchone()

        return _to_attempt(row) if row is not None else None

    async def get_by_delivery_and_number(
        self, delivery_id: str, attempt_number: int
    ) -> Optional[DeliveryAttempt]:
        """
        One attempt by its natural key. Served directly by
        uq_delivery_attempts_delivery_id_attempt_number.
        """
        stmt = select(delivery_attempts_table).where(
            delivery_attempts_table.c.delivery_id == delivery_id,
            delivery_attempts_table.c.attempt_number == attempt_number,
        )
        async with self._engine.connect() as conn:
            result = await conn.execute(stmt)
            row = result.fetchone()

        return _to_attempt(row) if row is not None else None

    async def list_by_delivery_id(self, delivery_id: str) -> list[DeliveryAttempt]:
        """All attempts for a Delivery, ordered by attempt_number."""
        stmt = (
            select(delivery_attempts_table)
            .where(delivery_attempts_table.c.delivery_id == delivery_id)
            .order_by(delivery_attempts_table.c.attempt_number)
        )
        async with self._engine.connect() as conn:
            result = await conn.execute(stmt)
            rows = result.fetchall()

        return [_to_attempt(row) for row in rows]

    async def list_recent_by_endpoint(
        self, endpoint_id: str, limit: int = 30
    ) -> list[tuple[Delivery, DeliveryAttempt]]:
        """
        Most recently completed attempts for an endpoint, newest first,
        each with its parent Delivery. Read-only; feeds the dashboard
        timeline. Attempts still in flight (a retry row is inserted at
        schedule time with a future started_at) are excluded.
        """
        stmt = (
            select(
                delivery_attempts_table,
                deliveries_table.c.event_id,
                deliveries_table.c.subscription_id,
                deliveries_table.c.endpoint_id,
                deliveries_table.c.created_at,
                deliveries_table.c.status,
            )
            .join(
                deliveries_table,
                deliveries_table.c.delivery_id == delivery_attempts_table.c.delivery_id,
            )
            .where(
                deliveries_table.c.endpoint_id == endpoint_id,
                delivery_attempts_table.c.completed_at.is_not(None),
            )
            .order_by(delivery_attempts_table.c.completed_at.desc())
            .limit(limit)
        )
        async with self._engine.connect() as conn:
            result = await conn.execute(stmt)
            rows = result.fetchall()

        return [
            (
                Delivery(
                    delivery_id=row.delivery_id,
                    event_id=row.event_id,
                    subscription_id=row.subscription_id,
                    endpoint_id=row.endpoint_id,
                    created_at=row.created_at,
                    status=row.status,
                ),
                _to_attempt(row),
            )
            for row in rows
        ]
