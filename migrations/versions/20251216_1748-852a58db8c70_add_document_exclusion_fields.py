"""add document exclusion fields

Revision ID: 852a58db8c70
Revises: 8078bff4ff28
Create Date: 2025-12-16 17:48:01.868403

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '852a58db8c70'
down_revision: Union[str, None] = '8078bff4ff28'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Columns now included in initial_schema migration - no-op
    pass


def downgrade() -> None:
    # No-op - handled by initial_schema
    pass
