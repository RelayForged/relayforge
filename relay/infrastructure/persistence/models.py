"""
SQLAlchemy Core table definitions for the persistence layer.

These are schema-only definitions used by Alembic and the repositories to
read/write rows. They are intentionally NOT the domain model -- the domain
model is the frozen Pydantic `Event` in relay/kernel/entities/event.py
(ADR 0010). Repositories map between the two; nothing outside
`infrastructure/persistence/` should import from this module.
"""
from sqlalchemy import (
    ARRAY,
    Boolean,
    Column,
    Float,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
)
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

# Delivery is a lifecycle aggregate (PENDING → SUCCESS | FAILED | DEAD_LETTER).
# Status is mutable via model_copy + save(); no append-only trigger.
# Uniqueness is (event_id, subscription_id): one Delivery per fan-out target.
# DeliveryAttempt and Telemetry are immutable facts (ADR 0007, ADR 0014) —
# append-only triggers live in the Alembic revision, not on this Table.

deliveries_table = Table(
    "deliveries",
    metadata,
    Column("delivery_id", UUID(as_uuid=False), primary_key=True),
    Column(
        "event_id",
        UUID(as_uuid=False),
        ForeignKey("events.event_id"),
        nullable=False,
    ),
    Column(
        "subscription_id",
        UUID(as_uuid=False),
        ForeignKey("subscriptions.subscription_id"),
        nullable=False,
    ),
    Column(
        "endpoint_id",
        UUID(as_uuid=False),
        ForeignKey("endpoints.endpoint_id"),
        nullable=False,
    ),
    Column("created_at", TIMESTAMP(timezone=True), nullable=False),
    Column("status", String, nullable=False, server_default="PENDING"),
    UniqueConstraint(
        "event_id",
        "subscription_id",
        name="uq_deliveries_event_id_subscription_id",
    ),
)

delivery_attempts_table = Table(
    "delivery_attempts",
    metadata,
    Column("attempt_id", UUID(as_uuid=False), primary_key=True),
    Column(
        "delivery_id",
        UUID(as_uuid=False),
        ForeignKey("deliveries.delivery_id"),
        nullable=False,
    ),
    Column("attempt_number", Integer, nullable=False),
    Column("started_at", TIMESTAMP(timezone=True), nullable=False),
    Column("completed_at", TIMESTAMP(timezone=True), nullable=True),
    Column("http_status", Integer, nullable=True),
    Column("success", Boolean, nullable=False, server_default="false"),
    Column("error_message", Text, nullable=True),
    UniqueConstraint(
        "delivery_id",
        "attempt_number",
        name="uq_delivery_attempts_delivery_id_attempt_number",
    ),
)

# One Telemetry row per DeliveryAttempt. PK is attempt_id (the kernel VO has
# no separate telemetry_id — do not invent a fourth shape).
telemetry_table = Table(
    "telemetry",
    metadata,
    Column(
        "attempt_id",
        UUID(as_uuid=False),
        ForeignKey("delivery_attempts.attempt_id"),
        primary_key=True,
    ),
    Column(
        "endpoint_id",
        UUID(as_uuid=False),
        ForeignKey("endpoints.endpoint_id"),
        nullable=False,
    ),
    Column("succeeded", Boolean, nullable=False),
    Column("http_status", Integer, nullable=True),
    Column("latency_ms", Float, nullable=False),
    Column("timed_out", Boolean, nullable=False, server_default="false"),
    Column("observed_at", TIMESTAMP(timezone=True), nullable=False),
)
