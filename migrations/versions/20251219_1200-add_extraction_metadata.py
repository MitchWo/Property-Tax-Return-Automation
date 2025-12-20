"""add_extraction_metadata_to_documents

Revision ID: add_extraction_metadata
Revises: a86c33afbc9f
Create Date: 2025-12-19 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


# revision identifiers, used by Alembic.
revision: str = 'add_extraction_metadata'
down_revision: Union[str, None] = 'a86c33afbc9f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add extraction metadata columns to documents table for Phase 1 batch processing."""

    # Add new columns for extraction metadata
    op.add_column('documents', sa.Column('pages_processed', sa.Integer(), nullable=True))
    op.add_column('documents', sa.Column('extraction_batches', sa.Integer(), nullable=True))
    op.add_column('documents', sa.Column('verification_status', sa.String(20), nullable=True))
    op.add_column('documents', sa.Column('data_quality_score', sa.Float(), nullable=True))
    op.add_column('documents', sa.Column('extraction_warnings', JSONB(), nullable=True))
    op.add_column('documents', sa.Column('processing_started_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('documents', sa.Column('processing_completed_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('documents', sa.Column('api_calls_used', sa.Integer(), nullable=True))

    # Add index for filtering documents by verification status
    op.create_index('ix_documents_verification_status', 'documents', ['verification_status'])


def downgrade() -> None:
    """Remove extraction metadata columns from documents table."""

    # Drop index
    op.drop_index('ix_documents_verification_status', 'documents')

    # Drop columns
    op.drop_column('documents', 'api_calls_used')
    op.drop_column('documents', 'processing_completed_at')
    op.drop_column('documents', 'processing_started_at')
    op.drop_column('documents', 'extraction_warnings')
    op.drop_column('documents', 'data_quality_score')
    op.drop_column('documents', 'verification_status')
    op.drop_column('documents', 'extraction_batches')
    op.drop_column('documents', 'pages_processed')
