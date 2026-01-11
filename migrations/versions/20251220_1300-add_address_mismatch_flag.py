"""add_address_mismatch_to_flagcategory

Revision ID: add_address_mismatch
Revises: add_capital_treatment
Create Date: 2025-12-20 13:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'add_address_mismatch'
down_revision: Union[str, None] = 'add_capital_treatment'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Enum value now included in initial_schema migration - no-op
    pass


def downgrade() -> None:
    # No-op
    pass
