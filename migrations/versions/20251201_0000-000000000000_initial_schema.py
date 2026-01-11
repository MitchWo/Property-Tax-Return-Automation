"""initial_schema

Revision ID: 000000000000
Revises:
Create Date: 2025-12-01 00:00:00.000000

This migration creates the base schema that was originally created via Base.metadata.create_all()
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '000000000000'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Enable pg_trgm extension for fuzzy text matching
    op.execute('CREATE EXTENSION IF NOT EXISTS pg_trgm')

    # Create enums
    op.execute("CREATE TYPE propertytype AS ENUM ('new_build', 'existing', 'not_sure')")
    op.execute("CREATE TYPE taxreturnstatus AS ENUM ('pending', 'complete', 'incomplete', 'blocked')")
    op.execute("CREATE TYPE documentstatus AS ENUM ('pending', 'classified', 'verified', 'error')")
    op.execute("CREATE TYPE transactiontype AS ENUM ('income', 'expense', 'excluded', 'flagged')")
    op.execute("CREATE TYPE workingsstatus AS ENUM ('draft', 'in_review', 'approved', 'submitted')")
    op.execute("CREATE TYPE flagseverity AS ENUM ('critical', 'high', 'medium', 'low', 'info')")
    op.execute("CREATE TYPE flagcategory AS ENUM ('missing_document', 'mismatch', 'review_required', 'anomaly', 'invoice_required', 'verification', 'data_quality', 'compliance', 'capital_treatment', 'address_mismatch')")
    op.execute("CREATE TYPE flagstatus AS ENUM ('open', 'resolved', 'ignored')")
    op.execute("CREATE TYPE requeststatus AS ENUM ('pending', 'sent', 'received', 'cancelled')")
    op.execute("CREATE TYPE questionstatus AS ENUM ('pending', 'sent', 'answered')")

    # Create clients table
    op.create_table('clients',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_clients_name', 'clients', ['name'])

    # Create tax_returns table
    op.create_table('tax_returns',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('client_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('property_address', sa.Text(), nullable=False),
        sa.Column('tax_year', sa.String(length=10), nullable=False),
        sa.Column('property_type', postgresql.ENUM('new_build', 'existing', 'not_sure', name='propertytype', create_type=False), nullable=False),
        sa.Column('gst_registered', sa.Boolean(), nullable=True),
        sa.Column('year_of_ownership', sa.Integer(), nullable=False),
        sa.Column('status', postgresql.ENUM('pending', 'complete', 'incomplete', 'blocked', name='taxreturnstatus', create_type=False), nullable=False),
        sa.Column('review_result', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['client_id'], ['clients.id']),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_tax_returns_client_id', 'tax_returns', ['client_id'])
    op.create_index('ix_tax_returns_status', 'tax_returns', ['status'])
    op.create_index('ix_tax_returns_tax_year', 'tax_returns', ['tax_year'])
    op.create_index('ix_tax_returns_created_at', 'tax_returns', ['created_at'])

    # Create documents table
    op.create_table('documents',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('tax_return_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('original_filename', sa.String(length=255), nullable=False),
        sa.Column('stored_filename', sa.String(length=255), nullable=False),
        sa.Column('file_path', sa.Text(), nullable=False),
        sa.Column('mime_type', sa.String(length=100), nullable=False),
        sa.Column('file_size', sa.Integer(), nullable=False),
        sa.Column('content_hash', sa.String(length=64), nullable=True),
        sa.Column('is_duplicate', sa.Boolean(), nullable=False, default=False),
        sa.Column('duplicate_of_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('is_excluded', sa.Boolean(), nullable=False, default=False),
        sa.Column('exclusion_reason', sa.String(length=255), nullable=True),
        sa.Column('document_type', sa.String(length=50), nullable=True),
        sa.Column('classification_confidence', sa.Float(), nullable=True),
        sa.Column('extracted_data', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('status', postgresql.ENUM('pending', 'classified', 'verified', 'error', name='documentstatus', create_type=False), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('pages_processed', sa.Integer(), nullable=True),
        sa.Column('extraction_batches', sa.Integer(), nullable=True),
        sa.Column('verification_status', sa.String(length=20), nullable=True),
        sa.Column('data_quality_score', sa.Float(), nullable=True),
        sa.Column('extraction_warnings', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('processing_started_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('processing_completed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('api_calls_used', sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(['tax_return_id'], ['tax_returns.id']),
        sa.ForeignKeyConstraint(['duplicate_of_id'], ['documents.id']),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_documents_tax_return_id', 'documents', ['tax_return_id'])
    op.create_index('ix_documents_document_type', 'documents', ['document_type'])
    op.create_index('ix_documents_status', 'documents', ['status'])

    # Create pl_row_mappings table
    op.create_table('pl_row_mappings',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('category_code', sa.String(length=50), nullable=False),
        sa.Column('pl_row', sa.Integer(), nullable=True),
        sa.Column('display_name', sa.String(length=100), nullable=False),
        sa.Column('category_group', sa.String(length=50), nullable=True),
        sa.Column('transaction_type', sa.String(length=20), nullable=False),
        sa.Column('is_deductible', sa.Boolean(), default=True),
        sa.Column('default_source', sa.String(length=10), nullable=True),
        sa.Column('sort_order', sa.Integer(), default=0),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('category_code')
    )

    # Create tax_rules table
    op.create_table('tax_rules',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('rule_type', sa.String(length=50), nullable=False),
        sa.Column('tax_year', sa.String(length=10), nullable=False),
        sa.Column('property_type', sa.String(length=20), nullable=False),
        sa.Column('value', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('effective_from', sa.Date(), nullable=True),
        sa.Column('effective_to', sa.Date(), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_tax_rules_lookup', 'tax_rules', ['rule_type', 'tax_year', 'property_type'])

    # Create transactions table
    op.create_table('transactions',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('tax_return_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('document_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('transaction_date', sa.Date(), nullable=False),
        sa.Column('description', sa.Text(), nullable=False),
        sa.Column('other_party', sa.String(length=255), nullable=True),
        sa.Column('amount', sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column('balance', sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column('category_code', sa.String(length=50), nullable=True),
        sa.Column('transaction_type', postgresql.ENUM('income', 'expense', 'excluded', 'flagged', name='transactiontype', create_type=False), nullable=True),
        sa.Column('is_deductible', sa.Boolean(), default=True),
        sa.Column('deductible_percentage', sa.Float(), default=100.0),
        sa.Column('deductible_amount', sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column('gst_inclusive', sa.Boolean(), default=True),
        sa.Column('gst_amount', sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column('confidence', sa.Float(), default=0.0),
        sa.Column('categorization_source', sa.String(length=50), nullable=True),
        sa.Column('categorization_trace', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('needs_review', sa.Boolean(), default=False),
        sa.Column('review_reason', sa.Text(), nullable=True),
        sa.Column('manually_reviewed', sa.Boolean(), default=False),
        sa.Column('reviewed_by', sa.String(length=100), nullable=True),
        sa.Column('reviewed_at', sa.DateTime(), nullable=True),
        sa.Column('raw_data', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['tax_return_id'], ['tax_returns.id']),
        sa.ForeignKeyConstraint(['document_id'], ['documents.id']),
        sa.ForeignKeyConstraint(['category_code'], ['pl_row_mappings.category_code']),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_transactions_tax_return', 'transactions', ['tax_return_id'])
    op.create_index('ix_transactions_category', 'transactions', ['category_code'])
    op.create_index('ix_transactions_date', 'transactions', ['transaction_date'])
    op.create_index('ix_transactions_needs_review', 'transactions', ['needs_review'])

    # Create transaction_summaries table
    op.create_table('transaction_summaries',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('tax_return_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('category_code', sa.String(length=50), nullable=False),
        sa.Column('transaction_count', sa.Integer(), default=0),
        sa.Column('gross_amount', sa.Numeric(precision=12, scale=2), default=0),
        sa.Column('deductible_amount', sa.Numeric(precision=12, scale=2), default=0),
        sa.Column('gst_amount', sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column('monthly_breakdown', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['tax_return_id'], ['tax_returns.id']),
        sa.ForeignKeyConstraint(['category_code'], ['pl_row_mappings.category_code']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('tax_return_id', 'category_code', name='uq_summary_return_category')
    )
    op.create_index('ix_transaction_summaries_return', 'transaction_summaries', ['tax_return_id'])

    # Create transaction_patterns table
    op.create_table('transaction_patterns',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('description_normalized', sa.Text(), nullable=False),
        sa.Column('other_party_normalized', sa.String(length=255), nullable=True),
        sa.Column('category_code', sa.String(length=50), nullable=False),
        sa.Column('confidence', sa.Float(), default=0.85),
        sa.Column('times_applied', sa.Integer(), default=1),
        sa.Column('times_confirmed', sa.Integer(), default=0),
        sa.Column('times_corrected', sa.Integer(), default=0),
        sa.Column('is_global', sa.Boolean(), default=True),
        sa.Column('client_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('source', sa.String(length=50), default='user_correction'),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('last_used_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['category_code'], ['pl_row_mappings.category_code']),
        sa.ForeignKeyConstraint(['client_id'], ['clients.id']),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_patterns_description', 'transaction_patterns', ['description_normalized'])
    op.create_index('ix_patterns_client', 'transaction_patterns', ['client_id'])

    # Create category_feedback table
    op.create_table('category_feedback',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('transaction_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('original_category', sa.String(length=50), nullable=True),
        sa.Column('corrected_category', sa.String(length=50), nullable=False),
        sa.Column('corrected_by', sa.String(length=100), nullable=True),
        sa.Column('corrected_at', sa.DateTime(), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('pattern_created', sa.Boolean(), default=False),
        sa.Column('pattern_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.ForeignKeyConstraint(['transaction_id'], ['transactions.id']),
        sa.ForeignKeyConstraint(['pattern_id'], ['transaction_patterns.id']),
        sa.PrimaryKeyConstraint('id')
    )

    # Create skill_learnings table
    op.create_table('skill_learnings',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('skill_name', sa.String(length=50), nullable=False),
        sa.Column('learning_type', sa.String(length=20), nullable=False),
        sa.Column('title', sa.String(length=200), nullable=False),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('keywords', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('applies_to', sa.String(length=20), default='transaction', nullable=False),
        sa.Column('client_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('category_code', sa.String(length=50), nullable=True),
        sa.Column('confidence', sa.Float(), default=0.8, nullable=False),
        sa.Column('times_applied', sa.Integer(), default=0, nullable=False),
        sa.Column('times_confirmed', sa.Integer(), default=0, nullable=False),
        sa.Column('created_by', sa.String(length=100), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('embedding_id', sa.String(length=100), nullable=True),
        sa.Column('is_active', sa.Boolean(), default=True, nullable=False),
        sa.ForeignKeyConstraint(['client_id'], ['clients.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['category_code'], ['pl_row_mappings.category_code'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_skill_learnings_skill_name', 'skill_learnings', ['skill_name'])
    op.create_index('ix_skill_learnings_learning_type', 'skill_learnings', ['learning_type'])
    op.create_index('ix_skill_learnings_client_id', 'skill_learnings', ['client_id'])
    op.create_index('ix_skill_learnings_category_code', 'skill_learnings', ['category_code'])

    # Create tax_return_workings table
    op.create_table('tax_return_workings',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('tax_return_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('version', sa.Integer(), default=1, nullable=False),
        sa.Column('total_income', sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column('total_expenses', sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column('total_deductions', sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column('net_rental_income', sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column('interest_gross', sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column('interest_deductible_percentage', sa.Float(), nullable=True),
        sa.Column('interest_deductible_amount', sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column('income_workings', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('expense_workings', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('document_inventory', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('processing_notes', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('audit_trail', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('ai_model_used', sa.String(length=100), nullable=True),
        sa.Column('ai_prompt_version', sa.String(length=50), nullable=True),
        sa.Column('processing_time_seconds', sa.Float(), nullable=True),
        sa.Column('status', postgresql.ENUM('draft', 'in_review', 'approved', 'submitted', name='workingsstatus', create_type=False), nullable=False),
        sa.Column('reviewed_by', sa.String(length=255), nullable=True),
        sa.Column('reviewed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('approved_by', sa.String(length=255), nullable=True),
        sa.Column('approved_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['tax_return_id'], ['tax_returns.id']),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_workings_tax_return', 'tax_return_workings', ['tax_return_id'])
    op.create_index('ix_workings_status', 'tax_return_workings', ['status'])
    op.create_index('ix_workings_created_at', 'tax_return_workings', ['created_at'])

    # Create workings_flags table
    op.create_table('workings_flags',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('workings_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('severity', postgresql.ENUM('critical', 'high', 'medium', 'low', 'info', name='flagseverity', create_type=False), nullable=False),
        sa.Column('category', postgresql.ENUM('missing_document', 'mismatch', 'review_required', 'anomaly', 'invoice_required', 'verification', 'data_quality', 'compliance', 'capital_treatment', 'address_mismatch', name='flagcategory', create_type=False), nullable=False),
        sa.Column('message', sa.Text(), nullable=False),
        sa.Column('action_required', sa.Text(), nullable=True),
        sa.Column('related_transaction_ids', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('related_document_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('related_amount', sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column('status', postgresql.ENUM('open', 'resolved', 'ignored', name='flagstatus', create_type=False), nullable=False),
        sa.Column('resolved_by', sa.String(length=255), nullable=True),
        sa.Column('resolved_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('resolution_notes', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['workings_id'], ['tax_return_workings.id']),
        sa.ForeignKeyConstraint(['related_document_id'], ['documents.id']),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_flags_workings', 'workings_flags', ['workings_id'])
    op.create_index('ix_flags_status', 'workings_flags', ['status'])
    op.create_index('ix_flags_severity', 'workings_flags', ['severity'])
    op.create_index('ix_flags_created_at', 'workings_flags', ['created_at'])

    # Create document_requests table
    op.create_table('document_requests',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('workings_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('tax_return_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('document_type', sa.String(length=50), nullable=False),
        sa.Column('reason', sa.Text(), nullable=False),
        sa.Column('priority', sa.String(length=20), default='required', nullable=False),
        sa.Column('details', sa.Text(), nullable=True),
        sa.Column('status', postgresql.ENUM('pending', 'sent', 'received', 'cancelled', name='requeststatus', create_type=False), nullable=False),
        sa.Column('sent_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('sent_method', sa.String(length=50), nullable=True),
        sa.Column('received_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('received_document_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['workings_id'], ['tax_return_workings.id']),
        sa.ForeignKeyConstraint(['tax_return_id'], ['tax_returns.id']),
        sa.ForeignKeyConstraint(['received_document_id'], ['documents.id']),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_doc_requests_tax_return', 'document_requests', ['tax_return_id'])
    op.create_index('ix_doc_requests_status', 'document_requests', ['status'])
    op.create_index('ix_doc_requests_created_at', 'document_requests', ['created_at'])

    # Create client_questions table
    op.create_table('client_questions',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('workings_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('tax_return_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('question', sa.Text(), nullable=False),
        sa.Column('context', sa.Text(), nullable=True),
        sa.Column('options', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('related_transaction_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('related_amount', sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column('status', postgresql.ENUM('pending', 'sent', 'answered', name='questionstatus', create_type=False), nullable=False),
        sa.Column('sent_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('answered_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('answer', sa.Text(), nullable=True),
        sa.Column('answer_option_index', sa.Integer(), nullable=True),
        sa.Column('affects_category', sa.String(length=50), nullable=True),
        sa.Column('affects_deductibility', sa.Boolean(), default=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['workings_id'], ['tax_return_workings.id']),
        sa.ForeignKeyConstraint(['tax_return_id'], ['tax_returns.id']),
        sa.ForeignKeyConstraint(['related_transaction_id'], ['transactions.id']),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_questions_tax_return', 'client_questions', ['tax_return_id'])
    op.create_index('ix_questions_status', 'client_questions', ['status'])
    op.create_index('ix_questions_created_at', 'client_questions', ['created_at'])

    # Create document_inventory table
    op.create_table('document_inventory',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('tax_return_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('inventory_data', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('provided_count', sa.Integer(), default=0),
        sa.Column('missing_count', sa.Integer(), default=0),
        sa.Column('excluded_count', sa.Integer(), default=0),
        sa.Column('blocking_issues_count', sa.Integer(), default=0),
        sa.Column('has_pm_statement', sa.Boolean(), default=False),
        sa.Column('has_bank_statement', sa.Boolean(), default=False),
        sa.Column('has_loan_statement', sa.Boolean(), default=False),
        sa.Column('has_rates_invoice', sa.Boolean(), default=False),
        sa.Column('has_insurance_policy', sa.Boolean(), default=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['tax_return_id'], ['tax_returns.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('tax_return_id', name='uq_inventory_tax_return')
    )
    op.create_index('ix_inventory_tax_return', 'document_inventory', ['tax_return_id'])


def downgrade() -> None:
    # Drop tables in reverse order
    op.drop_table('document_inventory')
    op.drop_table('client_questions')
    op.drop_table('document_requests')
    op.drop_table('workings_flags')
    op.drop_table('tax_return_workings')
    op.drop_table('skill_learnings')
    op.drop_table('category_feedback')
    op.drop_table('transaction_patterns')
    op.drop_table('transaction_summaries')
    op.drop_table('transactions')
    op.drop_table('tax_rules')
    op.drop_table('pl_row_mappings')
    op.drop_table('documents')
    op.drop_table('tax_returns')
    op.drop_table('clients')

    # Drop enums
    op.execute("DROP TYPE IF EXISTS questionstatus")
    op.execute("DROP TYPE IF EXISTS requeststatus")
    op.execute("DROP TYPE IF EXISTS flagstatus")
    op.execute("DROP TYPE IF EXISTS flagcategory")
    op.execute("DROP TYPE IF EXISTS flagseverity")
    op.execute("DROP TYPE IF EXISTS workingsstatus")
    op.execute("DROP TYPE IF EXISTS transactiontype")
    op.execute("DROP TYPE IF EXISTS documentstatus")
    op.execute("DROP TYPE IF EXISTS taxreturnstatus")
    op.execute("DROP TYPE IF EXISTS propertytype")
