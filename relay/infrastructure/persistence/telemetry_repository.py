"""
Repository for the `Telemetry` value object.

Translates between the frozen Pydantic `Telemetry`
(relay/kernel/value_objects/telemetry.py) and rows in the `telemetry`
table. This is the ERI observation shape (TelemetryProtocol); it is not
a DeliveryAttempt.

Append-only: INSERT + SELECT only. PK is `attempt_id` (one observation
per attempt — no invented telemetry_id).
"""
from typing import Optional

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncEngine

from relay.infrastructure.persistence.engine import get_engine
from relay.infrastructure.persistence.models import telemetry_table
from relay.kernel.value_objects.telemetry import Telemetry


class TelemetryRepository:
    def __init__(self, engine: Optional[AsyncEngine] = None) -> None:
        self._engine = engine or get_engine()

    async def save(self, telemetry: Telemetry) -> None:
        """
        Persist a Telemetry observation. Idempotent on `attempt_id`: a
        duplicate save is a no-op and never mutates the existing row
        (ADR 0014).
        """
        stmt = (
            pg_insert(telemetry_table)
            .values(
                attempt_id=telemetry.attempt_id,
                endpoint_id=telemetry.endpoint_id,
                succeeded=telemetry.succeeded,
                http_status=telemetry.http_status,
                latency_ms=telemetry.latency_ms,
                timed_out=telemetry.timed_out,
                observed_at=telemetry.observed_at,
            )
            .on_conflict_do_nothing(index_elements=["attempt_id"])
        )
        async with self._engine.begin() as conn:
            await conn.execute(stmt)

    async def get_by_id(self, attempt_id: str) -> Optional[Telemetry]:
        """Load Telemetry by the DeliveryAttempt it belongs to. Returns None if missing."""
        stmt = select(telemetry_table).where(
            telemetry_table.c.attempt_id == attempt_id
        )
        async with self._engine.connect() as conn:
            result = await conn.execute(stmt)
            row = result.fetchone()

        if row is None:
            return None

        return Telemetry(
            attempt_id=row.attempt_id,
            endpoint_id=row.endpoint_id,
            succeeded=row.succeeded,
            http_status=row.http_status,
            latency_ms=row.latency_ms,
            timed_out=row.timed_out,
            observed_at=row.observed_at,
        )

    async def list_by_endpoint_id(self, endpoint_id: str) -> list[Telemetry]:
        """All observations for an endpoint, ordered by observed_at (ERI window)."""
        stmt = (
            select(telemetry_table)
            .where(telemetry_table.c.endpoint_id == endpoint_id)
            .order_by(telemetry_table.c.observed_at)
        )
        async with self._engine.connect() as conn:
            result = await conn.execute(stmt)
            rows = result.fetchall()

        return [
            Telemetry(
                attempt_id=row.attempt_id,
                endpoint_id=row.endpoint_id,
                succeeded=row.succeeded,
                http_status=row.http_status,
                latency_ms=row.latency_ms,
                timed_out=row.timed_out,
                observed_at=row.observed_at,
            )
            for row in rows
        ]
