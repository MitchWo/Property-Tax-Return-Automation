"""add_workings_flags_requests_inventory_tables

Revision ID: a86c33afbc9f
Revises: 852a58db8c70
Create Date: 2025-12-17 13:38:38.988499

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB, ENUM


# revision identifiers, used by Alembic.
revision: str = 'a86c33afbc9f'
down_revision: Union[str, None] = '852a58db8c70'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create enums only if they don't exist
    from sqlalchemy import text
    conn = op.get_bind()

    # Check and create each enum type
    enums_to_create = [
        ('workingsstatus', ['draft', 'in_review', 'approved', 'submitted']),
        ('flagseverity', ['high', 'medium', 'low']),
        ('flagcategory', ['missing_document', 'mismatch', 'review_required', 'anomaly', 'invoice_required']),
        ('flagstatus', ['open', 'resolved', 'ignored']),
        ('requeststatus', ['pending', 'sent', 'received', 'cancelled']),
        ('questionstatus', ['pending', 'sent', 'answered']),
    ]

    for enum_name, enum_values in enums_to_create:
        result = conn.execute(text(
            "SELECT EXISTS (SELECT 1 FROM pg_type WHERE typname = :name)"
        ), {"name": enum_name})
        exists = result.scalar()
        if not exists:
            values_str = ", ".join([f"'{v}'" for v in enum_values])
            conn.execute(text(f"CREATE TYPE {enum_name} AS ENUM ({values_str})"))

    # Use ENUM types that already exist (create_type=False prevents creation)
    workings_status = ENUM('draft', 'in_review', 'approved', 'submitted', name='workingsstatus', create_type=False)
    flag_severity = ENUM('high', 'medium', 'low', name='flagseverity', create_type=False)
    flag_category = ENUM('missing_document', 'mismatch', 'review_required', 'anomaly', 'invoice_required', name='flagcategory', create_type=False)
    flag_status = ENUM('open', 'resolved', 'ignored', name='flagstatus', create_type=False)
    request_status = ENUM('pending', 'sent', 'received', 'cancelled', name='requeststatus', create_type=False)
    question_status = ENUM('pending', 'sent', 'answered', name='questionstatus', create_type=False)

    # Create tax_return_workings table
    op.create_table(
        'tax_return_workings',
        sa.Column('id', UUID(as_uuid=True), primary_key=True),
        sa.Column('tax_return_id', UUID(as_uuid=True), sa.ForeignKey('tax_returns.id'), nullable=False),
        sa.Column('version', sa.Integer(), default=1, nullable=False),
        sa.Column('total_income', sa.Numeric(12, 2), nullable=True),
        sa.Column('total_expenses', sa.Numeric(12, 2), nullable=True),
        sa.Column('total_deductions', sa.Numeric(12, 2), nullable=True),
        sa.Column('net_rental_income', sa.Numeric(12, 2), nullable=True),
        sa.Column('interest_gross', sa.Numeric(12, 2), nullable=True),
        sa.Column('interest_deductible_percentage', sa.Float(), nullable=True),
        sa.Column('interest_deductible_amount', sa.Numeric(12, 2), nullable=True),
        sa.Column('income_workings', JSONB, nullable=True),
        sa.Column('expense_workings', JSONB, nullable=True),
        sa.Column('document_inventory', JSONB, nullable=True),
        sa.Column('processing_notes', JSONB, nullable=True),
        sa.Column('audit_trail', JSONB, nullable=True),
        sa.Column('ai_model_used', sa.String(100), nullable=True),
        sa.Column('ai_prompt_version', sa.String(50), nullable=True),
        sa.Column('processing_time_seconds', sa.Float(), nullable=True),
        sa.Column('status', workings_status, server_default='draft', nullable=False),
        sa.Column('reviewed_by', sa.String(255), nullable=True),
        sa.Column('reviewed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('approved_by', sa.String(255), nullable=True),
        sa.Column('approved_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_workings_tax_return', 'tax_return_workings', ['tax_return_id'])
    op.create_index('ix_workings_status', 'tax_return_workings', ['status'])
    op.create_index('ix_workings_created_at', 'tax_return_workings', [sa.text('created_at DESC')])

    # Create workings_flags table
    op.create_table(
        'workings_flags',
        sa.Column('id', UUID(as_uuid=True), primary_key=True),
        sa.Column('workings_id', UUID(as_uuid=True), sa.ForeignKey('tax_return_workings.id'), nullable=False),
        sa.Column('severity', flag_severity, nullable=False),
        sa.Column('category', flag_category, nullable=False),
        sa.Column('message', sa.Text(), nullable=False),
        sa.Column('action_required', sa.Text(), nullable=True),
        sa.Column('related_transaction_ids', JSONB, nullable=True),
        sa.Column('related_document_id', UUID(as_uuid=True), sa.ForeignKey('documents.id'), nullable=True),
        sa.Column('related_amount', sa.Numeric(12, 2), nullable=True),
        sa.Column('status', flag_status, server_default='open', nullable=False),
        sa.Column('resolved_by', sa.String(255), nullable=True),
        sa.Column('resolved_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('resolution_notes', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_flags_workings', 'workings_flags', ['workings_id'])
    op.create_index('ix_flags_status', 'workings_flags', ['status'])
    op.create_index('ix_flags_severity', 'workings_flags', ['severity'])
    op.create_index('ix_flags_created_at', 'workings_flags', [sa.text('created_at DESC')])

    # Create document_requests table
    op.create_table(
        'document_requests',
        sa.Column('id', UUID(as_uuid=True), primary_key=True),
        sa.Column('workings_id', UUID(as_uuid=True), sa.ForeignKey('tax_return_workings.id'), nullable=True),
        sa.Column('tax_return_id', UUID(as_uuid=True), sa.ForeignKey('tax_returns.id'), nullable=False),
        sa.Column('document_type', sa.String(50), nullable=False),
        sa.Column('reason', sa.Text(), nullable=False),
        sa.Column('priority', sa.String(20), server_default='required', nullable=False),
        sa.Column('details', sa.Text(), nullable=True),
        sa.Column('status', request_status, server_default='pending', nullable=False),
        sa.Column('sent_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('sent_method', sa.String(50), nullable=True),
        sa.Column('received_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('received_document_id', UUID(as_uuid=True), sa.ForeignKey('documents.id'), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_doc_requests_tax_return', 'document_requests', ['tax_return_id'])
    op.create_index('ix_doc_requests_status', 'document_requests', ['status'])
    op.create_index('ix_doc_requests_created_at', 'document_requests', [sa.text('created_at DESC')])

    # Create client_questions table
    op.create_table(
        'client_questions',
        sa.Column('id', UUID(as_uuid=True), primary_key=True),
        sa.Column('workings_id', UUID(as_uuid=True), sa.ForeignKey('tax_return_workings.id'), nullable=True),
        sa.Column('tax_return_id', UUID(as_uuid=True), sa.ForeignKey('tax_returns.id'), nullable=False),
        sa.Column('question', sa.Text(), nullable=False),
        sa.Column('context', sa.Text(), nullable=True),
        sa.Column('options', JSONB, nullable=True),
        sa.Column('related_transaction_id', UUID(as_uuid=True), sa.ForeignKey('transactions.id'), nullable=True),
        sa.Column('related_amount', sa.Numeric(12, 2), nullable=True),
        sa.Column('status', question_status, server_default='pending', nullable=False),
        sa.Column('sent_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('answered_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('answer', sa.Text(), nullable=True),
        sa.Column('answer_option_index', sa.Integer(), nullable=True),
        sa.Column('affects_category', sa.String(50), nullable=True),
        sa.Column('affects_deductibility', sa.Boolean(), server_default='false'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_questions_tax_return', 'client_questions', ['tax_return_id'])
    op.create_index('ix_questions_status', 'client_questions', ['status'])
    op.create_index('ix_questions_created_at', 'client_questions', [sa.text('created_at DESC')])

    # Create document_inventory table
    op.create_table(
        'document_inventory',
        sa.Column('id', UUID(as_uuid=True), primary_key=True),
        sa.Column('tax_return_id', UUID(as_uuid=True), sa.ForeignKey('tax_returns.id'), nullable=False),
        sa.Column('inventory_data', JSONB, nullable=False),
        sa.Column('provided_count', sa.Integer(), server_default='0'),
        sa.Column('missing_count', sa.Integer(), server_default='0'),
        sa.Column('excluded_count', sa.Integer(), server_default='0'),
        sa.Column('blocking_issues_count', sa.Integer(), server_default='0'),
        sa.Column('has_pm_statement', sa.Boolean(), server_default='false'),
        sa.Column('has_bank_statement', sa.Boolean(), server_default='false'),
        sa.Column('has_loan_statement', sa.Boolean(), server_default='false'),
        sa.Column('has_rates_invoice', sa.Boolean(), server_default='false'),
        sa.Column('has_insurance_policy', sa.Boolean(), server_default='false'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_inventory_tax_return', 'document_inventory', ['tax_return_id'])
    op.create_unique_constraint('uq_inventory_tax_return', 'document_inventory', ['tax_return_id'])


def downgrade() -> None:
    # Drop tables
    op.drop_table('document_inventory')
    op.drop_table('client_questions')
    op.drop_table('document_requests')
    op.drop_table('workings_flags')
    op.drop_table('tax_return_workings')

    # Drop enums
    from sqlalchemy import text
    conn = op.get_bind()
    for enum_name in ['questionstatus', 'requeststatus', 'flagstatus', 'flagcategory', 'flagseverity', 'workingsstatus']:
        conn.execute(text(f"DROP TYPE IF EXISTS {enum_name}"))
