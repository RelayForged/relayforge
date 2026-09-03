"""harden delivery indexes and constraints

Revision ID: b7c41de9a052
Revises: 6b999f7451a9
Create Date: 2026-09-03 15:05:00.000000

Four unrelated-looking changes that all came out of the Week 3-4 audit:

1. GIN index on `subscriptions.event_types`. The Dispatcher fan-out
   filters `event_types @> ARRAY[event_type]`, which a btree cannot
   serve, so every ingested event was sequentially scanning the table.

2. Status CHECK constraints. `Delivery.status` is a plain `str` on the
   frozen kernel entity, so a typo used to persist silently and strand
   the row outside every status query.

3. Narrow UPDATE trigger on `delivery_attempts`. Revision 6b999f7451a9
   dropped UPDATE from the trigger so the Worker could fill completion
   columns, which left every column freely rewritable — the immutability
   guarantee in ADR 0014 held only in application code. This re-asserts
   it at the DB while still permitting the sanctioned completion write.

4. Drop two redundant indexes that duplicate the leading column of an
   existing unique constraint, and replace the telemetry index with the
   composite that its ordered window query actually needs.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b7c41de9a052'
down_revision: Union[str, Sequence[str], None] = '6b999f7451a9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

DELIVERY_STATUSES = ("PENDING", "SUCCESS", "FAILED", "DEAD_LETTER")
SUBSCRIPTION_STATUSES = ("CREATED", "ACTIVE", "PAUSED", "DISABLED", "ARCHIVED")


def _in_list(column: str, values: Sequence[str]) -> str:
    rendered = ", ".join(f"'{value}'" for value in values)
    return f"{column} IN ({rendered})"


def upgrade() -> None:
    """Upgrade schema."""
    # --- 1. array containment index for the fan-out query -----------------
    op.create_index(
        "ix_subscriptions_event_types_gin",
        "subscriptions",
        ["event_types"],
        postgresql_using="gin",
    )

    # --- 2. status domains ------------------------------------------------
    op.create_check_constraint(
        "ck_deliveries_status",
        "deliveries",
        _in_list("status", DELIVERY_STATUSES),
    )
    op.create_check_constraint(
        "ck_subscriptions_status",
        "subscriptions",
        _in_list("status", SUBSCRIPTION_STATUSES),
    )

    # --- 3. completion-only UPDATE on delivery_attempts -------------------
    op.execute(
        """
        CREATE FUNCTION prevent_attempt_identity_mutation() RETURNS TRIGGER AS $$
        BEGIN
            IF OLD.completed_at IS NOT NULL THEN
                RAISE EXCEPTION
                    'delivery_attempts row % is already completed and is immutable',
                    OLD.attempt_id;
            END IF;
            IF NEW.attempt_id IS DISTINCT FROM OLD.attempt_id
               OR NEW.delivery_id IS DISTINCT FROM OLD.delivery_id
               OR NEW.attempt_number IS DISTINCT FROM OLD.attempt_number
               OR NEW.started_at IS DISTINCT FROM OLD.started_at THEN
                RAISE EXCEPTION
                    'delivery_attempts identity columns are immutable';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER delivery_attempts_completion_only
        BEFORE UPDATE ON delivery_attempts
        FOR EACH ROW EXECUTE FUNCTION prevent_attempt_identity_mutation();
        """
    )

    # --- 4. index hygiene -------------------------------------------------
    # Prefix of uq_deliveries_event_id_subscription_id.
    op.drop_index("ix_deliveries_event_id", table_name="deliveries")
    # Prefix of uq_delivery_attempts_delivery_id_attempt_number.
    op.drop_index("ix_delivery_attempts_delivery_id", table_name="delivery_attempts")
    # The ERI window reads (endpoint_id, observed_at DESC) with a LIMIT;
    # the endpoint_id-only index cannot serve the ordering.
    op.create_index(
        "ix_telemetry_endpoint_id_observed_at",
        "telemetry",
        ["endpoint_id", sa.text("observed_at DESC")],
    )
    op.drop_index("ix_telemetry_endpoint_id", table_name="telemetry")


def downgrade() -> None:
    """Downgrade schema."""
    op.create_index("ix_telemetry_endpoint_id", "telemetry", ["endpoint_id"])
    op.drop_index("ix_telemetry_endpoint_id_observed_at", table_name="telemetry")
    op.create_index(
        "ix_delivery_attempts_delivery_id", "delivery_attempts", ["delivery_id"]
    )
    op.create_index("ix_deliveries_event_id", "deliveries", ["event_id"])

    op.execute(
        "DROP TRIGGER IF EXISTS delivery_attempts_completion_only "
        "ON delivery_attempts;"
    )
    op.execute("DROP FUNCTION IF EXISTS prevent_attempt_identity_mutation();")

    op.drop_constraint("ck_subscriptions_status", "subscriptions", type_="check")
    op.drop_constraint("ck_deliveries_status", "deliveries", type_="check")

    op.drop_index("ix_subscriptions_event_types_gin", table_name="subscriptions")
