"""
SQLAlchemy Core table definitions for the persistence layer.

These are schema-only definitions used by Alembic and the repositories to
read/write rows. They are intentionally NOT the domain model -- the domain
model is the frozen Pydantic `Event` in relay/kernel/entities/event.py
(ADR 0010). Repositories map between the two; nothing outside
`infrastructure/persistence/` should import from this module.
"""
from sqlalchemy import Column, MetaData, String, Table
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP, UUID

metadata = MetaData()

events_table = Table(
    "events",
    metadata,
    Column("event_id", UUID(as_uuid=False), primary_key=True),
    Column("correlation_id", UUID(as_uuid=False), nullable=False),
    Column("causation_id", UUID(as_uuid=False), nullable=False),
    Column("event_type", String, nullable=False),
    Column("payload", JSONB, nullable=False),
    Column("occurred_at", TIMESTAMP(timezone=True), nullable=False),
)
