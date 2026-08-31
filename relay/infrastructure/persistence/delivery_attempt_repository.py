"""
Repository for the `DeliveryAttempt` entity.

Retries are new rows (new attempt_id / attempt_number). DELETE is
forbidden at the DB (ADR 0014). Completing an in-flight attempt is a
sanctioned UPDATE of outcome columns only, via model_copy + save().
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
        Persist a DeliveryAttempt. Insert if new. If the same `attempt_id`
        already exists (Dispatcher persist-first row) and `completed_at`
        is still null, fill in outcome columns from the Worker's
        model_copy. Identity fields and a completed row are never rewritten.
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
