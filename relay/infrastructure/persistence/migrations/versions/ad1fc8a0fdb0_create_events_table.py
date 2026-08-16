"""create events table

Revision ID: ad1fc8a0fdb0
Revises:
Create Date: 2026-08-16 21:08:22.623647

Events are immutable business facts (ADR 0002, ADR 0010: Events are never
modified after publication). This migration creates the `events` table and
also enforces append-only semantics at the database level: a trigger raises
an exception on any UPDATE or DELETE against the table. See
docs/adr/0011-events-table-append-only-trigger.md for the rationale.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID


# revision identifiers, used by Alembic.
revision: str = 'ad1fc8a0fdb0'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "events",
        sa.Column("event_id", UUID(as_uuid=False), primary_key=True),
        sa.Column("correlation_id", UUID(as_uuid=False), nullable=False),
        sa.Column("causation_id", UUID(as_uuid=False), nullable=False),
        sa.Column("event_type", sa.String(), nullable=False),
        sa.Column("payload", JSONB(), nullable=False),
        sa.Column("occurred_at", sa.TIMESTAMP(timezone=True), nullable=False),
    )
    op.create_index("ix_events_correlation_id", "events", ["correlation_id"])
    op.create_index("ix_events_event_type", "events", ["event_type"])

    # Append-only enforcement: reject UPDATE and DELETE at the DB level so
    # no code path -- present or future -- can mutate history.
    op.execute(
        """
        CREATE FUNCTION prevent_events_mutation() RETURNS TRIGGER AS $$
        BEGIN
            RAISE EXCEPTION 'events table is append-only: % is not permitted', TG_OP;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER events_immutable
        BEFORE UPDATE OR DELETE ON events
        FOR EACH ROW EXECUTE FUNCTION prevent_events_mutation();
        """
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DROP TRIGGER IF EXISTS events_immutable ON events;")
    op.execute("DROP FUNCTION IF EXISTS prevent_events_mutation();")
    op.drop_index("ix_events_event_type", table_name="events")
    op.drop_index("ix_events_correlation_id", table_name="events")
    op.drop_table("events")
