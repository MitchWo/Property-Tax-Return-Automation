"""add_phase3_transaction_processing_tables

Revision ID: aec6e527475d
Revises: 
Create Date: 2025-12-11 15:50:20.903759

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'aec6e527475d'
down_revision: Union[str, None] = '000000000000'  # Depends on initial_schema
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Tables are now created in initial_schema migration (000000000000)
    # This migration only adds the trigram index for fuzzy search
    op.execute('''
        CREATE INDEX IF NOT EXISTS ix_patterns_trgm
        ON transaction_patterns
        USING gin (description_normalized gin_trgm_ops)
    ''')


def downgrade() -> None:
    # Only drop the trigram index (tables are handled by initial_schema)
    op.execute('DROP INDEX IF EXISTS ix_patterns_trgm')