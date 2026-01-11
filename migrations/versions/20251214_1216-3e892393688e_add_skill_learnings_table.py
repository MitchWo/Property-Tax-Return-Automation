"""add_skill_learnings_table

Revision ID: 3e892393688e
Revises: d7f814dd9ac8
Create Date: 2025-12-14 12:16:29.024153

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '3e892393688e'
down_revision: Union[str, None] = 'd7f814dd9ac8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Table now created in initial_schema migration - no-op
    pass


def downgrade() -> None:
    # No-op - handled by initial_schema
    pass
