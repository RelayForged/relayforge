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
from typing import Optional

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncEngine

from relay.infrastructure.persistence.engine import get_engine
from relay.infrastructure.persistence.models import deliveries_table
from relay.kernel.entities.delivery import Delivery


class DeliveryRepository:
    def __init__(self, engine: Optional[AsyncEngine] = None) -> None:
        self._engine = engine or get_engine()

    async def save(self, delivery: Delivery) -> None:
        """
        Persist a Delivery. Idempotent on (event_id, subscription_id):
        a second insert for the same pair with a different delivery_id is
        a no-op. If the conflicting row has the same delivery_id, `status`
        is updated so Retry Manager can mark SUCCESS / FAILED / DEAD_LETTER
        via model_copy + re-save. created_at and identity FKs are never
        overwritten.
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
        )
        async with self._engine.begin() as conn:
            await conn.execute(stmt)

    async def get_by_id(self, delivery_id: str) -> Optional[Delivery]:
        """Load a single Delivery by its ID. Returns None if it does not exist."""
        stmt = select(deliveries_table).where(
            deliveries_table.c.delivery_id == delivery_id
        )
        async with self._engine.connect() as conn:
            result = await conn.execute(stmt)
            row = result.fetchone()

        if row is None:
            return None

        return Delivery(
            delivery_id=row.delivery_id,
            event_id=row.event_id,
            subscription_id=row.subscription_id,
            endpoint_id=row.endpoint_id,
            created_at=row.created_at,
            status=row.status,
        )

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

        if row is None:
            return None

        return Delivery(
            delivery_id=row.delivery_id,
            event_id=row.event_id,
            subscription_id=row.subscription_id,
            endpoint_id=row.endpoint_id,
            created_at=row.created_at,
            status=row.status,
        )
