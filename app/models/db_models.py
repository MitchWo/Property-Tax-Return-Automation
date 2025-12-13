"""SQLAlchemy database models."""

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import relationship

from app.database import Base


def local_now():
    """Return current time in local timezone."""
    return datetime.now().astimezone()


class PropertyType(str, enum.Enum):
    """Property type enumeration."""

    NEW_BUILD = "new_build"
    EXISTING = "existing"
    NOT_SURE = "not_sure"


class TaxReturnStatus(str, enum.Enum):
    """Tax return status enumeration."""

    PENDING = "pending"
    COMPLETE = "complete"
    INCOMPLETE = "incomplete"
    BLOCKED = "blocked"


class DocumentStatus(str, enum.Enum):
    """Document status enumeration."""

    PENDING = "pending"
    CLASSIFIED = "classified"
    VERIFIED = "verified"
    ERROR = "error"


class Client(Base):
    """Client model."""

    __tablename__ = "clients"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(255), nullable=False)
    created_at = Column(DateTime(timezone=True), default=local_now, nullable=False)

    # Relationships
    tax_returns = relationship("TaxReturn", back_populates="client", cascade="all, delete-orphan")


class TaxReturn(Base):
    """Tax return model."""

    __tablename__ = "tax_returns"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    client_id = Column(UUID(as_uuid=True), ForeignKey("clients.id"), nullable=False)
    property_address = Column(Text, nullable=False)
    tax_year = Column(String(10), nullable=False)  # e.g., "FY25"
    property_type = Column(Enum(PropertyType), nullable=False)
    gst_registered = Column(Boolean, default=None, nullable=True)  # None = user wants AI suggestion
    year_of_ownership = Column(Integer, nullable=False)
    status = Column(Enum(TaxReturnStatus), default=TaxReturnStatus.PENDING, nullable=False)
    review_result = Column(JSONB, nullable=True)  # Stores full analysis
    created_at = Column(DateTime(timezone=True), default=local_now, nullable=False)
    updated_at = Column(
        DateTime(timezone=True), default=local_now, onupdate=local_now, nullable=False
    )

    # Relationships
    client = relationship("Client", back_populates="tax_returns")
    documents = relationship("Document", back_populates="tax_return", cascade="all, delete-orphan")
    transactions = relationship("Transaction", back_populates="tax_return", cascade="all, delete-orphan")


class Document(Base):
    """Document model."""

    __tablename__ = "documents"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tax_return_id = Column(UUID(as_uuid=True), ForeignKey("tax_returns.id"), nullable=False)
    original_filename = Column(String(255), nullable=False)
    stored_filename = Column(String(255), nullable=False)
    file_path = Column(Text, nullable=False)
    mime_type = Column(String(100), nullable=False)
    file_size = Column(Integer, nullable=False)
    content_hash = Column(String(64), nullable=True)  # SHA-256 hash for duplicate detection
    is_duplicate = Column(Boolean, default=False, nullable=False)  # Flag for duplicates
    duplicate_of_id = Column(
        UUID(as_uuid=True), ForeignKey("documents.id"), nullable=True
    )  # Reference to original
    document_type = Column(String(50), nullable=True)  # Set after classification
    classification_confidence = Column(Float, nullable=True)
    extracted_data = Column(JSONB, nullable=True)
    status = Column(Enum(DocumentStatus), default=DocumentStatus.PENDING, nullable=False)
    created_at = Column(DateTime(timezone=True), default=local_now, nullable=False)

    # Relationships
    tax_return = relationship("TaxReturn", back_populates="documents")
    transactions = relationship("Transaction", back_populates="document")


# Database Indexes for Phase 1 models
Index("ix_clients_name", Client.name)
Index("ix_tax_returns_client_id", TaxReturn.client_id)
Index("ix_tax_returns_status", TaxReturn.status)
Index("ix_tax_returns_tax_year", TaxReturn.tax_year)
Index("ix_tax_returns_created_at", TaxReturn.created_at.desc())
Index("ix_documents_tax_return_id", Document.tax_return_id)
Index("ix_documents_document_type", Document.document_type)
Index("ix_documents_status", Document.status)


# ================== PHASE 3: TRANSACTION PROCESSING MODELS ==================

class TransactionType(str, enum.Enum):
    """Transaction type enumeration."""
    INCOME = "income"
    EXPENSE = "expense"
    EXCLUDED = "excluded"
    FLAGGED = "flagged"


class TaxRule(Base):
    """Tax rules that change by year/property type."""
    __tablename__ = "tax_rules"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    rule_type = Column(String(50), nullable=False)  # 'interest_deductibility', 'accounting_fee', 'ird_mileage_rate'
    tax_year = Column(String(10), nullable=False)   # 'FY24', 'FY25', 'FY26'
    property_type = Column(String(20), nullable=False)  # 'new_build', 'existing', 'all'
    value = Column(JSONB, nullable=False)  # {"percentage": 80} or {"amount": 862.50}
    effective_from = Column(Date, nullable=True)
    effective_to = Column(Date, nullable=True)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index('ix_tax_rules_lookup', 'rule_type', 'tax_year', 'property_type'),
    )


class PLRowMapping(Base):
    """Maps transaction categories to P&L Excel rows."""
    __tablename__ = "pl_row_mappings"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    category_code = Column(String(50), unique=True, nullable=False)  # 'rental_income', 'rates', etc.
    pl_row = Column(Integer, nullable=True)  # Excel row number (null = excluded from P&L)
    display_name = Column(String(100), nullable=False)  # 'Rental Income', 'Rates'
    transaction_type = Column(String(20), nullable=False)  # 'income', 'expense', 'excluded'
    is_deductible = Column(Boolean, default=True)
    default_source = Column(String(10), nullable=True)  # 'BS', 'PM', 'INV', 'SS'
    sort_order = Column(Integer, default=0)  # For display ordering

    # Relationships
    transactions = relationship("Transaction", back_populates="category_mapping")


class Transaction(Base):
    """Individual transaction extracted from documents."""
    __tablename__ = "transactions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tax_return_id = Column(UUID(as_uuid=True), ForeignKey("tax_returns.id"), nullable=False)
    document_id = Column(UUID(as_uuid=True), ForeignKey("documents.id"), nullable=True)

    # Transaction details
    transaction_date = Column(Date, nullable=False)
    description = Column(Text, nullable=False)
    other_party = Column(String(255), nullable=True)
    amount = Column(Numeric(12, 2), nullable=False)  # Positive = debit/expense, Negative = credit/income
    balance = Column(Numeric(12, 2), nullable=True)  # Running balance if available

    # Categorization
    category_code = Column(String(50), ForeignKey("pl_row_mappings.category_code"), nullable=True)
    transaction_type = Column(Enum(TransactionType), nullable=True)

    # Deductibility (for expenses)
    is_deductible = Column(Boolean, default=True)
    deductible_percentage = Column(Float, default=100.0)  # 100, 80, 0
    deductible_amount = Column(Numeric(12, 2), nullable=True)  # Calculated: amount * percentage

    # GST handling
    gst_inclusive = Column(Boolean, default=True)
    gst_amount = Column(Numeric(12, 2), nullable=True)

    # Confidence and review
    confidence = Column(Float, default=0.0)
    categorization_source = Column(String(50), nullable=True)  # 'yaml_pattern', 'learned_exact', 'learned_fuzzy', 'claude', 'manual'
    categorization_trace = Column(JSONB, nullable=True)  # Stores diagnostic trace from categorization process
    needs_review = Column(Boolean, default=False)
    review_reason = Column(Text, nullable=True)  # Changed from String(255) to Text to handle longer Claude responses
    manually_reviewed = Column(Boolean, default=False)
    reviewed_by = Column(String(100), nullable=True)
    reviewed_at = Column(DateTime, nullable=True)

    # Original data (for audit)
    raw_data = Column(JSONB, nullable=True)  # Original row from CSV/extraction

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    tax_return = relationship("TaxReturn", back_populates="transactions")
    document = relationship("Document", back_populates="transactions")
    category_mapping = relationship("PLRowMapping", back_populates="transactions")

    __table_args__ = (
        Index('ix_transactions_tax_return', 'tax_return_id'),
        Index('ix_transactions_category', 'category_code'),
        Index('ix_transactions_date', 'transaction_date'),
        Index('ix_transactions_needs_review', 'needs_review'),
    )


class TransactionSummary(Base):
    """Aggregated transaction totals by category for a tax return."""
    __tablename__ = "transaction_summaries"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tax_return_id = Column(UUID(as_uuid=True), ForeignKey("tax_returns.id"), nullable=False)
    category_code = Column(String(50), ForeignKey("pl_row_mappings.category_code"), nullable=False)

    # Totals
    transaction_count = Column(Integer, default=0)
    gross_amount = Column(Numeric(12, 2), default=0)
    deductible_amount = Column(Numeric(12, 2), default=0)
    gst_amount = Column(Numeric(12, 2), nullable=True)

    # For interest specifically
    monthly_breakdown = Column(JSONB, nullable=True)  # {"Apr-24": 1234.56, "May-24": 1234.56, ...}

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    tax_return = relationship("TaxReturn")
    category_mapping = relationship("PLRowMapping")

    __table_args__ = (
        Index('ix_transaction_summaries_return', 'tax_return_id'),
        UniqueConstraint('tax_return_id', 'category_code', name='uq_summary_return_category'),
    )


class TransactionPattern(Base):
    """Learned patterns from user corrections for auto-categorization."""
    __tablename__ = "transaction_patterns"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # Match criteria
    description_normalized = Column(Text, nullable=False)  # Lowercase, trimmed
    other_party_normalized = Column(String(255), nullable=True)

    # Result
    category_code = Column(String(50), ForeignKey("pl_row_mappings.category_code"), nullable=False)

    # Confidence tracking
    confidence = Column(Float, default=0.85)
    times_applied = Column(Integer, default=1)
    times_confirmed = Column(Integer, default=0)
    times_corrected = Column(Integer, default=0)

    # Scope
    is_global = Column(Boolean, default=True)
    client_id = Column(UUID(as_uuid=True), ForeignKey("clients.id"), nullable=True)

    # Metadata
    source = Column(String(50), default='user_correction')  # 'user_correction', 'seed_data', 'bulk_import'
    created_at = Column(DateTime, default=datetime.utcnow)
    last_used_at = Column(DateTime, nullable=True)

    # Relationships
    client = relationship("Client")
    category_mapping = relationship("PLRowMapping")

    __table_args__ = (
        Index('ix_patterns_description', 'description_normalized'),
        Index('ix_patterns_client', 'client_id'),
    )


class CategoryFeedback(Base):
    """Audit trail for category corrections."""
    __tablename__ = "category_feedback"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    transaction_id = Column(UUID(as_uuid=True), ForeignKey("transactions.id"), nullable=False)

    original_category = Column(String(50), nullable=True)
    corrected_category = Column(String(50), nullable=False)

    corrected_by = Column(String(100), nullable=True)
    corrected_at = Column(DateTime, default=datetime.utcnow)
    notes = Column(Text, nullable=True)

    # Whether this created/updated a pattern
    pattern_created = Column(Boolean, default=False)
    pattern_id = Column(UUID(as_uuid=True), ForeignKey("transaction_patterns.id"), nullable=True)

    # Relationships
    transaction = relationship("Transaction")
    pattern = relationship("TransactionPattern")
