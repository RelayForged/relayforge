"""add delivery delivery_attempt and telemetry tables

Revision ID: c026fc304e9b
Revises: 7bca8225231f
Create Date: 2026-08-31 20:12:11.214961

Delivery is a lifecycle aggregate (status PENDING → SUCCESS | FAILED |
DEAD_LETTER) — no append-only trigger. DeliveryAttempt and Telemetry are
immutable facts (ADR 0007): BEFORE UPDATE OR DELETE triggers raise, same
pattern as events (ADR 0011). See docs/adr/0014-delivery-attempt-telemetry-append-only.md.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


# revision identifiers, used by Alembic.
revision: str = 'c026fc304e9b'
down_revision: Union[str, Sequence[str], None] = '7bca8225231f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "deliveries",
        sa.Column("delivery_id", UUID(as_uuid=False), primary_key=True),
        sa.Column(
            "event_id",
            UUID(as_uuid=False),
            sa.ForeignKey("events.event_id"),
            nullable=False,
        ),
        sa.Column(
            "subscription_id",
            UUID(as_uuid=False),
            sa.ForeignKey("subscriptions.subscription_id"),
            nullable=False,
        ),
        sa.Column(
            "endpoint_id",
            UUID(as_uuid=False),
            sa.ForeignKey("endpoints.endpoint_id"),
            nullable=False,
        ),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="PENDING"),
        sa.UniqueConstraint(
            "event_id",
            "subscription_id",
            name="uq_deliveries_event_id_subscription_id",
        ),
    )
    op.create_index("ix_deliveries_event_id", "deliveries", ["event_id"])
    op.create_index("ix_deliveries_subscription_id", "deliveries", ["subscription_id"])
    op.create_index("ix_deliveries_endpoint_id", "deliveries", ["endpoint_id"])
    op.create_index("ix_deliveries_status", "deliveries", ["status"])

    op.create_table(
        "delivery_attempts",
        sa.Column("attempt_id", UUID(as_uuid=False), primary_key=True),
        sa.Column(
            "delivery_id",
            UUID(as_uuid=False),
            sa.ForeignKey("deliveries.delivery_id"),
            nullable=False,
        ),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("completed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("http_status", sa.Integer(), nullable=True),
        sa.Column("success", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.UniqueConstraint(
            "delivery_id",
            "attempt_number",
            name="uq_delivery_attempts_delivery_id_attempt_number",
        ),
    )
    op.create_index("ix_delivery_attempts_delivery_id", "delivery_attempts", ["delivery_id"])

    op.create_table(
        "telemetry",
        sa.Column(
            "attempt_id",
            UUID(as_uuid=False),
            sa.ForeignKey("delivery_attempts.attempt_id"),
            primary_key=True,
        ),
        sa.Column(
            "endpoint_id",
            UUID(as_uuid=False),
            sa.ForeignKey("endpoints.endpoint_id"),
            nullable=False,
        ),
        sa.Column("succeeded", sa.Boolean(), nullable=False),
        sa.Column("http_status", sa.Integer(), nullable=True),
        sa.Column("latency_ms", sa.Float(), nullable=False),
        sa.Column("timed_out", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("observed_at", sa.TIMESTAMP(timezone=True), nullable=False),
    )
    op.create_index("ix_telemetry_endpoint_id", "telemetry", ["endpoint_id"])

    # Shared append-only enforcement for DeliveryAttempt and Telemetry.
    # Events keep their own prevent_events_mutation() from ad1fc8a0fdb0.
    op.execute(
        """
        CREATE FUNCTION prevent_append_only_mutation() RETURNS TRIGGER AS $$
        BEGIN
            RAISE EXCEPTION '% table is append-only: % is not permitted', TG_TABLE_NAME, TG_OP;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER delivery_attempts_immutable
        BEFORE UPDATE OR DELETE ON delivery_attempts
        FOR EACH ROW EXECUTE FUNCTION prevent_append_only_mutation();
        """
    )
    op.execute(
        """
        CREATE TRIGGER telemetry_immutable
        BEFORE UPDATE OR DELETE ON telemetry
        FOR EACH ROW EXECUTE FUNCTION prevent_append_only_mutation();
        """
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DROP TRIGGER IF EXISTS telemetry_immutable ON telemetry;")
    op.execute("DROP TRIGGER IF EXISTS delivery_attempts_immutable ON delivery_attempts;")
    op.execute("DROP FUNCTION IF EXISTS prevent_append_only_mutation();")

    op.drop_index("ix_telemetry_endpoint_id", table_name="telemetry")
    op.drop_table("telemetry")
    op.drop_index("ix_delivery_attempts_delivery_id", table_name="delivery_attempts")
    op.drop_table("delivery_attempts")
    op.drop_index("ix_deliveries_status", table_name="deliveries")
    op.drop_index("ix_deliveries_endpoint_id", table_name="deliveries")
    op.drop_index("ix_deliveries_subscription_id", table_name="deliveries")
    op.drop_index("ix_deliveries_event_id", table_name="deliveries")
    op.drop_table("deliveries")
