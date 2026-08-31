"""
Repository for the `DeliveryAttempt` entity.

Append-only: only `save()` (INSERT) and reads exist. There is no update
method, mirroring the DB-level trigger from ADR 0014 — a DeliveryAttempt
row is never mutated. Each retry is a new row with a new attempt_id and
incrementing attempt_number.
"""
from typing import Optional

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncEngine

from relay.infrastructure.persistence.engine import get_engine
from relay.infrastructure.persistence.models import delivery_attempts_table
from relay.kernel.entities.delivery import DeliveryAttempt


class DeliveryAttemptRepository:
    def __init__(self, engine: Optional[AsyncEngine] = None) -> None:
        self._engine = engine or get_engine()

    async def save(self, attempt: DeliveryAttempt) -> None:
        """
        Persist a DeliveryAttempt. Idempotent on `attempt_id`: a repeat
        save (e.g. worker retry after a crash post-insert) is a no-op
        rather than raising a unique-violation or mutating the existing
        row (ADR 0002, ADR 0014).
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
            .on_conflict_do_nothing(index_elements=["attempt_id"])
        )
        async with self._engine.begin() as conn:
            await conn.execute(stmt)

    async def get_by_id(self, attempt_id: str) -> Optional[DeliveryAttempt]:
        """Load a single DeliveryAttempt by its ID. Returns None if it does not exist."""
        stmt = select(delivery_attempts_table).where(
            delivery_attempts_table.c.attempt_id == attempt_id
        )
        async with self._engine.connect() as conn:
            result = await conn.execute(stmt)
            row = result.fetchone()

        if row is None:
            return None

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

        return [
            DeliveryAttempt(
                attempt_id=row.attempt_id,
                delivery_id=row.delivery_id,
                attempt_number=row.attempt_number,
                started_at=row.started_at,
                completed_at=row.completed_at,
                http_status=row.http_status,
                success=row.success,
                error_message=row.error_message,
            )
            for row in rows
        ]
