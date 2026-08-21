"""
Repository for the `Endpoint` entity.

Unlike `EventRepository` (insert-only, append-only per ADR 0011), Endpoint
is a configuration entity expected to change over time (e.g. is_active
toggled off/on by future EndpointPaused/EndpointResumed handling). `Event`
is frozen, so the sanctioned way to "update" one (ADR 0010) is
`endpoint.model_copy(update={...})` followed by `save()` again -- so
`save()` here is an upsert, not an insert-only operation. See
docs/small-decisions-personA/endpoint-subscription-repository.md.
"""
from typing import Optional

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncEngine

from relay.infrastructure.persistence.engine import get_engine
from relay.infrastructure.persistence.models import endpoints_table
from relay.kernel.entities.endpoint import Endpoint


class EndpointRepository:
    def __init__(self, engine: Optional[AsyncEngine] = None) -> None:
        self._engine = engine or get_engine()

    async def save(self, endpoint: Endpoint) -> None:
        """
        Upsert an Endpoint by endpoint_id. Insert if new; if it already
        exists, overwrite mutable fields with the given (model_copy'd)
        instance. `endpoint_id` and `created_at` are never overwritten by
        the update path -- creation identity is immutable even though
        configuration is not.
        """
        stmt = pg_insert(endpoints_table).values(
            endpoint_id=endpoint.endpoint_id,
            target_url=endpoint.target_url,
            owner_id=endpoint.owner_id,
            created_at=endpoint.created_at,
            is_active=endpoint.is_active,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["endpoint_id"],
            set_={
                "target_url": stmt.excluded.target_url,
                "owner_id": stmt.excluded.owner_id,
                "is_active": stmt.excluded.is_active,
            },
        )
        async with self._engine.begin() as conn:
            await conn.execute(stmt)

    async def get_by_id(self, endpoint_id: str) -> Optional[Endpoint]:
        """Load a single Endpoint by its ID. Returns None if it does not exist."""
        stmt = select(endpoints_table).where(endpoints_table.c.endpoint_id == endpoint_id)
        async with self._engine.connect() as conn:
            result = await conn.execute(stmt)
            row = result.fetchone()

        if row is None:
            return None

        return Endpoint(
            endpoint_id=row.endpoint_id,
            target_url=row.target_url,
            owner_id=row.owner_id,
            created_at=row.created_at,
            is_active=row.is_active,
        )
