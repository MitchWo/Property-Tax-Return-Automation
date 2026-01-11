"""add_extraction_metadata_to_documents

Revision ID: add_extraction_metadata
Revises: a86c33afbc9f
Create Date: 2025-12-19 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'add_extraction_metadata'
down_revision: Union[str, None] = 'a86c33afbc9f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Columns now included in initial_schema migration - no-op
    pass


def downgrade() -> None:
    # No-op - handled by initial_schema
    pass
