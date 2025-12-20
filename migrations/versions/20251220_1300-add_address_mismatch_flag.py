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
    """Add address_mismatch to flagcategory enum."""
    from sqlalchemy import text
    conn = op.get_bind()

    # Add new value to the flagcategory enum
    # PostgreSQL allows adding values to enums with ALTER TYPE
    conn.execute(text(
        "ALTER TYPE flagcategory ADD VALUE IF NOT EXISTS 'address_mismatch'"
    ))


def downgrade() -> None:
    """Remove address_mismatch from flagcategory enum.

    Note: PostgreSQL doesn't support removing enum values easily.
    This would require recreating the type, which is complex.
    """
    pass
