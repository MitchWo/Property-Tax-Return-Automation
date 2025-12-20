"""add_skill_learnings_table

Revision ID: 3e892393688e
Revises: d7f814dd9ac8
Create Date: 2025-12-14 12:16:29.024153

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
import uuid


# revision identifiers, used by Alembic.
revision: str = '3e892393688e'
down_revision: Union[str, None] = 'd7f814dd9ac8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create the skill_learnings table
    op.create_table('skill_learnings',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, nullable=False),
        sa.Column('skill_name', sa.String(length=50), nullable=False),
        sa.Column('learning_type', sa.String(length=20), nullable=False),
        sa.Column('title', sa.String(length=200), nullable=False),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('keywords', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('applies_to', sa.String(length=20), server_default='transaction', nullable=False),
        sa.Column('client_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('category_code', sa.String(length=50), nullable=True),
        sa.Column('confidence', sa.Float(), server_default='0.8', nullable=False),
        sa.Column('times_applied', sa.Integer(), server_default='0', nullable=False),
        sa.Column('times_confirmed', sa.Integer(), server_default='0', nullable=False),
        sa.Column('created_by', sa.String(length=100), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), onupdate=sa.func.now(), nullable=False),
        sa.Column('embedding_id', sa.String(length=100), nullable=True),
        sa.Column('is_active', sa.Boolean(), server_default='true', nullable=False),
        sa.PrimaryKeyConstraint('id')
    )

    # Add foreign key constraint to clients table
    op.create_foreign_key(
        'fk_skill_learnings_client_id',
        'skill_learnings', 'clients',
        ['client_id'], ['id'],
        ondelete='CASCADE'
    )

    # Add foreign key constraint to pl_row_mappings table for category_code
    op.create_foreign_key(
        'fk_skill_learnings_category_code',
        'skill_learnings', 'pl_row_mappings',
        ['category_code'], ['category_code'],
        ondelete='SET NULL'
    )

    # Create indexes for better query performance
    op.create_index('ix_skill_learnings_skill_name', 'skill_learnings', ['skill_name'])
    op.create_index('ix_skill_learnings_learning_type', 'skill_learnings', ['learning_type'])
    op.create_index('ix_skill_learnings_client_id', 'skill_learnings', ['client_id'])
    op.create_index('ix_skill_learnings_category_code', 'skill_learnings', ['category_code'])

    # Create GIN index for JSONB search on keywords
    op.execute('CREATE INDEX ix_skill_learnings_keywords ON skill_learnings USING GIN(keywords)')

    # Add check constraint for learning_type
    op.create_check_constraint(
        'ck_skill_learnings_learning_type',
        'skill_learnings',
        sa.or_(
            sa.column('learning_type') == 'teaching',
            sa.column('learning_type') == 'correction',
            sa.column('learning_type') == 'pattern',
            sa.column('learning_type') == 'edge_case'
        )
    )

    # Add check constraint for applies_to
    op.create_check_constraint(
        'ck_skill_learnings_applies_to',
        'skill_learnings',
        sa.or_(
            sa.column('applies_to') == 'transaction',
            sa.column('applies_to') == 'document',
            sa.column('applies_to') == 'both'
        )
    )


def downgrade() -> None:
    # Drop indexes
    op.drop_index('ix_skill_learnings_keywords', table_name='skill_learnings')
    op.drop_index('ix_skill_learnings_category_code', table_name='skill_learnings')
    op.drop_index('ix_skill_learnings_client_id', table_name='skill_learnings')
    op.drop_index('ix_skill_learnings_learning_type', table_name='skill_learnings')
    op.drop_index('ix_skill_learnings_skill_name', table_name='skill_learnings')

    # Drop foreign key constraints
    op.drop_constraint('fk_skill_learnings_category_code', 'skill_learnings', type_='foreignkey')
    op.drop_constraint('fk_skill_learnings_client_id', 'skill_learnings', type_='foreignkey')

    # Drop check constraints
    op.drop_constraint('ck_skill_learnings_applies_to', 'skill_learnings', type_='check')
    op.drop_constraint('ck_skill_learnings_learning_type', 'skill_learnings', type_='check')

    # Drop the table
    op.drop_table('skill_learnings')