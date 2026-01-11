"""make_gst_registered_nullable

Revision ID: eb3efe559739
Revises: 6ec2508ea4cd
Create Date: 2025-12-13 22:17:25.347748

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'eb3efe559739'
down_revision: Union[str, None] = '6ec2508ea4cd'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Schema changes now in initial_schema migration - no-op
    pass


def downgrade() -> None:
    # No-op - handled by initial_schema
    pass
