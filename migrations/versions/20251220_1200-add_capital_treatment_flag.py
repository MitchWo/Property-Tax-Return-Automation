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
    """Add capital_treatment to flagcategory enum."""
    from sqlalchemy import text
    conn = op.get_bind()

    # Add new value to the flagcategory enum
    # PostgreSQL allows adding values to enums with ALTER TYPE
    conn.execute(text(
        "ALTER TYPE flagcategory ADD VALUE IF NOT EXISTS 'capital_treatment'"
    ))


def downgrade() -> None:
    """Remove capital_treatment from flagcategory enum.

    Note: PostgreSQL doesn't support removing enum values easily.
    This would require recreating the type, which is complex.
    """
    pass
