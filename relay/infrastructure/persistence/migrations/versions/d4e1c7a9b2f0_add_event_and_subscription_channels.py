"""add event and subscription channels

Revision ID: d4e1c7a9b2f0
Revises: b7c41de9a052
Create Date: 2026-10-14 23:30:00.000000

Per-tenant routing (ADR 0025). An Event may carry channels and a
Subscription may be limited to channels; a subscription with no channels
keeps receiving every event of its types. Existing rows get the empty
array, so routing for them is unchanged.

The events append-only trigger blocks UPDATE and DELETE only; adding a
column with a default does neither to existing rows' meaning.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'd4e1c7a9b2f0'
down_revision: Union[str, Sequence[str], None] = 'b7c41de9a052'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    for table in ("events", "subscriptions"):
        op.add_column(
            table,
            sa.Column(
                "channels",
                postgresql.ARRAY(sa.String()),
                nullable=False,
                server_default=sa.text("'{}'::varchar[]"),
            ),
        )
    # Fan-out filters subscriptions with `channels && :event_channels`.
    op.create_index(
        "ix_subscriptions_channels",
        "subscriptions",
        ["channels"],
        postgresql_using="gin",
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_subscriptions_channels", table_name="subscriptions")
    for table in ("subscriptions", "events"):
        op.drop_column(table, "channels")
