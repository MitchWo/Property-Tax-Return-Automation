"""add_capital_treatment_to_flagcategory

Revision ID: add_capital_treatment
Revises: add_extraction_metadata
Create Date: 2025-12-20 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'add_capital_treatment'
down_revision: Union[str, None] = 'add_extraction_metadata'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Enum value now included in initial_schema migration - no-op
    pass


def downgrade() -> None:
    # No-op
    pass
