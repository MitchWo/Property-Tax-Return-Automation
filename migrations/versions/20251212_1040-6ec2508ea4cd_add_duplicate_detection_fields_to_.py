"""add duplicate detection fields to documents

Revision ID: 6ec2508ea4cd
Revises: 38b939fd17f9
Create Date: 2025-12-12 10:40:49.418346

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '6ec2508ea4cd'
down_revision: Union[str, None] = '38b939fd17f9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # These columns are now included in initial_schema migration
    # This is a no-op for fresh installs
    pass


def downgrade() -> None:
    # No-op - handled by initial_schema
    pass