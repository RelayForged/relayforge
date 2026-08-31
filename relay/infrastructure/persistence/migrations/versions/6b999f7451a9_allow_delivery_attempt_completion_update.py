"""allow delivery_attempt completion update

Revision ID: 6b999f7451a9
Revises: c026fc304e9b
Create Date: 2026-08-31 20:49:44.028047

Dispatcher persist-first inserts a started DeliveryAttempt; the Worker
fills completion columns after HTTP via model_copy + save(). DELETE stays
forbidden. Telemetry remains UPDATE-or-DELETE blocked.
"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = '6b999f7451a9'
down_revision: Union[str, Sequence[str], None] = 'c026fc304e9b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("DROP TRIGGER IF EXISTS delivery_attempts_immutable ON delivery_attempts;")
    op.execute(
        """
        CREATE TRIGGER delivery_attempts_immutable
        BEFORE DELETE ON delivery_attempts
        FOR EACH ROW EXECUTE FUNCTION prevent_append_only_mutation();
        """
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DROP TRIGGER IF EXISTS delivery_attempts_immutable ON delivery_attempts;")
    op.execute(
        """
        CREATE TRIGGER delivery_attempts_immutable
        BEFORE UPDATE OR DELETE ON delivery_attempts
        FOR EACH ROW EXECUTE FUNCTION prevent_append_only_mutation();
        """
    )
