"""add endpoint and subscription tables

Revision ID: 7bca8225231f
Revises: ad1fc8a0fdb0
Create Date: 2026-08-21 15:34:37.179879

Endpoint and Subscription are configuration entities, not immutable
business facts like Event -- no append-only trigger here (contrast with
ADR 0011). `endpoints` is created before `subscriptions` because
`subscriptions.endpoint_id` has a FK to it.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


# revision identifiers, used by Alembic.
revision: str = '7bca8225231f'
down_revision: Union[str, Sequence[str], None] = 'ad1fc8a0fdb0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "endpoints",
        sa.Column("endpoint_id", UUID(as_uuid=False), primary_key=True),
        sa.Column("target_url", sa.String(), nullable=False),
        sa.Column("owner_id", sa.String(), nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
    )
    op.create_index("ix_endpoints_owner_id", "endpoints", ["owner_id"])

    op.create_table(
        "subscriptions",
        sa.Column("subscription_id", UUID(as_uuid=False), primary_key=True),
        sa.Column(
            "endpoint_id",
            UUID(as_uuid=False),
            sa.ForeignKey("endpoints.endpoint_id"),
            nullable=False,
        ),
        sa.Column("event_types", sa.ARRAY(sa.String()), nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="ACTIVE"),
    )
    op.create_index("ix_subscriptions_endpoint_id", "subscriptions", ["endpoint_id"])
    op.create_index("ix_subscriptions_status", "subscriptions", ["status"])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_subscriptions_status", table_name="subscriptions")
    op.drop_index("ix_subscriptions_endpoint_id", table_name="subscriptions")
    op.drop_table("subscriptions")
    op.drop_index("ix_endpoints_owner_id", table_name="endpoints")
    op.drop_table("endpoints")
