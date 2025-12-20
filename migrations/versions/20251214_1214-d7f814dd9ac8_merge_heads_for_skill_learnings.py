"""merge_heads_for_skill_learnings

Revision ID: d7f814dd9ac8
Revises: 1174c4935449, eb3efe559739
Create Date: 2025-12-14 12:14:57.736542

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd7f814dd9ac8'
down_revision: Union[str, None] = ('1174c4935449', 'eb3efe559739')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass