"""
Repository for the `Subscription` entity.

Same upsert rationale as EndpointRepository -- Subscription has a lifecycle
(CREATED -> ACTIVE -> PAUSED -> DISABLED -> ARCHIVED per
docs/architecture.md), so save() must support persisting a status
transition made via `subscription.model_copy(update={"status": ...})`.
"""
from typing import Optional

from sqlalchemy import Row, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncEngine

from relay.infrastructure.persistence.engine import get_engine
from relay.infrastructure.persistence.models import (
    endpoints_table,
    subscriptions_table,
)
from relay.kernel.entities.endpoint import Endpoint
from relay.kernel.entities.subscription import Subscription


def _to_subscription(row: Row) -> Subscription:
    return Subscription(
        subscription_id=row.subscription_id,
        endpoint_id=row.endpoint_id,
        event_types=list(row.event_types),
        created_at=row.created_at,
        status=row.status,
    )


def _to_endpoint(row: Row) -> Endpoint:
    """
    Endpoint columns from the joined projection in
    `list_active_targets_by_event_type`. `endpoints` and `subscriptions`
    both define `endpoint_id` and `created_at`, so the endpoint side is
    selected under explicit `endpoint_*` labels to keep Row access
    unambiguous.
    """
    return Endpoint(
        endpoint_id=row.endpoint_endpoint_id,
        target_url=row.endpoint_target_url,
        owner_id=row.endpoint_owner_id,
        created_at=row.endpoint_created_at,
        is_active=row.endpoint_is_active,
    )


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

        return [_to_subscription(row) for row in rows]

    async def list_active_targets_by_event_type(
        self, event_type: str
    ) -> list[tuple[Subscription, Endpoint]]:
        """
        ACTIVE subscriptions joined to their *active* Endpoint.

        Replaces `list_active_by_event_type` + one EndpointRepository
        lookup per subscription: fanning one Event out to N subscriptions
        used to cost N extra SELECTs, each on its own connection. Filtering
        `is_active` here also stops deliveries to deactivated endpoints,
        which the per-subscription path never checked.
        """
        stmt = (
            select(
                subscriptions_table,
                endpoints_table.c.endpoint_id.label("endpoint_endpoint_id"),
                endpoints_table.c.target_url.label("endpoint_target_url"),
                endpoints_table.c.owner_id.label("endpoint_owner_id"),
                endpoints_table.c.created_at.label("endpoint_created_at"),
                endpoints_table.c.is_active.label("endpoint_is_active"),
            )
            .join(
                endpoints_table,
                subscriptions_table.c.endpoint_id == endpoints_table.c.endpoint_id,
            )
            .where(subscriptions_table.c.status == "ACTIVE")
            .where(endpoints_table.c.is_active.is_(True))
            .where(subscriptions_table.c.event_types.contains([event_type]))
            .order_by(subscriptions_table.c.created_at)
        )
        async with self._engine.connect() as conn:
            result = await conn.execute(stmt)
            rows = result.fetchall()

        return [(_to_subscription(row), _to_endpoint(row)) for row in rows]
