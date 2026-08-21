"""
SQLAlchemy Core table definitions for the persistence layer.

These are schema-only definitions used by Alembic and the repositories to
read/write rows. They are intentionally NOT the domain model -- the domain
model is the frozen Pydantic `Event` in relay/kernel/entities/event.py
(ADR 0010). Repositories map between the two; nothing outside
`infrastructure/persistence/` should import from this module.
"""
from sqlalchemy import ARRAY, Boolean, Column, ForeignKey, MetaData, String, Table
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

# Endpoint and Subscription are configuration entities, not immutable
# business facts -- unlike `events`, they are expected to change over time
# (is_active toggles, subscription status transitions per
# docs/architecture.md's Subscription Lifecycle). No append-only trigger
# here; see docs/small-decisions-personA/endpoint-subscription-repository.md
# for why save() is an upsert rather than insert-only.

endpoints_table = Table(
    "endpoints",
    metadata,
    Column("endpoint_id", UUID(as_uuid=False), primary_key=True),
    Column("target_url", String, nullable=False),
    Column("owner_id", String, nullable=False),
    Column("created_at", TIMESTAMP(timezone=True), nullable=False),
    Column("is_active", Boolean, nullable=False, server_default="true"),
)

subscriptions_table = Table(
    "subscriptions",
    metadata,
    Column("subscription_id", UUID(as_uuid=False), primary_key=True),
    Column(
        "endpoint_id",
        UUID(as_uuid=False),
        ForeignKey("endpoints.endpoint_id"),
        nullable=False,
    ),
    Column("event_types", ARRAY(String), nullable=False),
    Column("created_at", TIMESTAMP(timezone=True), nullable=False),
    Column("status", String, nullable=False, server_default="ACTIVE"),
)
