"""add_workings_flags_requests_inventory_tables

Revision ID: a86c33afbc9f
Revises: 852a58db8c70
Create Date: 2025-12-17 13:38:38.988499

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a86c33afbc9f'
down_revision: Union[str, None] = '852a58db8c70'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Tables now created in initial_schema migration - no-op
    pass


def downgrade() -> None:
    # No-op - handled by initial_schema
    pass
