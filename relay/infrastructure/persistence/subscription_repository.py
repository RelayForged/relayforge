"""
Repository for the `Subscription` entity.

Same upsert rationale as EndpointRepository -- Subscription has a lifecycle
(CREATED -> ACTIVE -> PAUSED -> DISABLED -> ARCHIVED per
docs/architecture.md), so save() must support persisting a status
transition made via `subscription.model_copy(update={"status": ...})`.
"""
from typing import Optional

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncEngine

from relay.infrastructure.persistence.engine import get_engine
from relay.infrastructure.persistence.models import subscriptions_table
from relay.kernel.entities.subscription import Subscription


class SubscriptionRepository:
    def __init__(self, engine: Optional[AsyncEngine] = None) -> None:
        self._engine = engine or get_engine()

    async def save(self, subscription: Subscription) -> None:
        """
        Upsert a Subscription by subscription_id. `endpoint_id` and
        `created_at` are immutable once set; `event_types` and `status`
        are the fields expected to change via model_copy + re-save.
        """
        stmt = pg_insert(subscriptions_table).values(
            subscription_id=subscription.subscription_id,
            endpoint_id=subscription.endpoint_id,
            event_types=subscription.event_types,
            created_at=subscription.created_at,
            status=subscription.status,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["subscription_id"],
            set_={
                "event_types": stmt.excluded.event_types,
                "status": stmt.excluded.status,
            },
        )
        async with self._engine.begin() as conn:
            await conn.execute(stmt)

    async def get_by_id(self, subscription_id: str) -> Optional[Subscription]:
        """Load a single Subscription by its ID. Returns None if it does not exist."""
        stmt = select(subscriptions_table).where(
            subscriptions_table.c.subscription_id == subscription_id
        )
        async with self._engine.connect() as conn:
            result = await conn.execute(stmt)
            row = result.fetchone()

        if row is None:
            return None

        return Subscription(
            subscription_id=row.subscription_id,
            endpoint_id=row.endpoint_id,
            event_types=list(row.event_types),
            created_at=row.created_at,
            status=row.status,
        )

    async def list_active_by_event_type(self, event_type: str) -> list[Subscription]:
        """
        ACTIVE subscriptions whose `event_types` array contains `event_type`.
        Used by the Dispatcher to fan out one Event to N deliveries.
        """
        stmt = (
            select(subscriptions_table)
            .where(subscriptions_table.c.status == "ACTIVE")
            .where(subscriptions_table.c.event_types.contains([event_type]))
            .order_by(subscriptions_table.c.created_at)
        )
        async with self._engine.connect() as conn:
            result = await conn.execute(stmt)
            rows = result.fetchall()

        return [
            Subscription(
                subscription_id=row.subscription_id,
                endpoint_id=row.endpoint_id,
                event_types=list(row.event_types),
                created_at=row.created_at,
                status=row.status,
            )
            for row in rows
        ]
