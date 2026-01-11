"""increase_transaction_review_reason_column_size

Revision ID: 1e6e16260d1e
Revises: aec6e527475d
Create Date: 2025-12-11 22:17:04.321556

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '1e6e16260d1e'
down_revision: Union[str, None] = 'aec6e527475d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Column type now Text in initial_schema migration - no-op
    pass


def downgrade() -> None:
    # No-op - handled by initial_schema
    pass
