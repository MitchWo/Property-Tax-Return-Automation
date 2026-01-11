"""add_categorization_trace

Revision ID: 1174c4935449
Revises: 1e6e16260d1e
Create Date: 2025-12-13 20:56:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '1174c4935449'
down_revision: Union[str, None] = '1e6e16260d1e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Column now included in initial_schema migration - no-op
    pass


def downgrade() -> None:
    # No-op - handled by initial_schema
    pass