"""add not_sure to propertytype enum

Revision ID: 38b939fd17f9
Revises: 
Create Date: 2025-12-12 10:24:36.218894

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '38b939fd17f9'
down_revision: Union[str, None] = 'aec6e527475d'  # Depends on phase3 tables
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 'not_sure' is now included in initial_schema migration
    # This is a no-op for fresh installs
    pass


def downgrade() -> None:
    # PostgreSQL doesn't support removing enum values directly
    # Would need to recreate the enum type, which is complex
    # For now, we leave the value in place (it's harmless)
    pass