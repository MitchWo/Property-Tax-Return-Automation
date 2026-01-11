"""add_category_group_to_pl_row_mappings

Revision ID: 8078bff4ff28
Revises: 3e892393688e
Create Date: 2025-12-14 17:15:16.926991

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '8078bff4ff28'
down_revision: Union[str, None] = '3e892393688e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Column now included in initial_schema migration - no-op
    pass


def downgrade() -> None:
    # No-op - handled by initial_schema
    pass
