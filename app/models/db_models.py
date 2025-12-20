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
    is_excluded = Column(Boolean, default=False, nullable=False)  # Exclude from calculations
    exclusion_reason = Column(String(255), nullable=True)  # Reason for exclusion
    document_type = Column(String(50), nullable=True)  # Set after classification
    classification_confidence = Column(Float, nullable=True)
    extracted_data = Column(JSONB, nullable=True)
    status = Column(Enum(DocumentStatus), default=DocumentStatus.PENDING, nullable=False)
    created_at = Column(DateTime(timezone=True), default=local_now, nullable=False)

    # Phase 1 extraction metadata (batch processing)
    pages_processed = Column(Integer, nullable=True)
    extraction_batches = Column(Integer, nullable=True)
    verification_status = Column(String(20), nullable=True)  # 'passed', 'warnings', 'failed'
    data_quality_score = Column(Float, nullable=True)
    extraction_warnings = Column(JSONB, nullable=True)
    processing_started_at = Column(DateTime(timezone=True), nullable=True)
    processing_completed_at = Column(DateTime(timezone=True), nullable=True)
    api_calls_used = Column(Integer, nullable=True)

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


# ================== PHASE 2: TRANSACTION PROCESSING & LEARNING MODELS ==================

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
    category_group = Column(String(50), nullable=True)  # 'Income', 'Rates & Levies', etc. for UI grouping
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


class LearningType(str, enum.Enum):
    """Types of learnings that can be stored."""
    TEACHING = "teaching"
    CORRECTION = "correction"
    PATTERN = "pattern"
    EDGE_CASE = "edge_case"


class AppliesTo(str, enum.Enum):
    """What the learning applies to."""
    TRANSACTION = "transaction"
    DOCUMENT = "document"
    BOTH = "both"
    CALCULATION = "calculation"


class SkillLearning(Base):
    """Stores learnings and teachings for skills to improve over time."""
    __tablename__ = "skill_learnings"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    skill_name = Column(String(50), nullable=False)  # e.g., "nz_rental_returns"
    learning_type = Column(String(20), nullable=False)  # Changed from Enum to String
    title = Column(String(200), nullable=False)
    content = Column(Text, nullable=False)
    keywords = Column(JSONB, nullable=True)  # Store as JSONB instead of ARRAY for better compatibility
    applies_to = Column(String(20), default="transaction", nullable=False)  # Changed from Enum to String

    # Relationships
    client_id = Column(UUID(as_uuid=True), ForeignKey("clients.id", ondelete="CASCADE"), nullable=True)
    category_code = Column(String(50), ForeignKey("pl_row_mappings.category_code", ondelete="SET NULL"), nullable=True)

    # Tracking
    confidence = Column(Float, default=0.8, nullable=False)
    times_applied = Column(Integer, default=0, nullable=False)
    times_confirmed = Column(Integer, default=0, nullable=False)

    # Metadata
    created_by = Column(String(100), nullable=True)
    created_at = Column(DateTime(timezone=True), default=local_now, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=local_now, onupdate=local_now, nullable=False)
    embedding_id = Column(String(100), nullable=True)  # Reference to Pinecone/vector DB
    is_active = Column(Boolean, default=True, nullable=False)

    # Relationships
    client = relationship("Client")
    category_mapping = relationship("PLRowMapping")


# Database Indexes for SkillLearning
Index("ix_skill_learnings_skill_name", SkillLearning.skill_name)
Index("ix_skill_learnings_learning_type", SkillLearning.learning_type)
Index("ix_skill_learnings_client_id", SkillLearning.client_id)
Index("ix_skill_learnings_category_code", SkillLearning.category_code)


# ================== PHASE 2 v2.0: AI BRAIN & WORKINGS MODELS ==================

class WorkingsStatus(str, enum.Enum):
    """Status of tax return workings."""
    DRAFT = "draft"              # AI generated, not reviewed
    IN_REVIEW = "in_review"      # User is reviewing
    APPROVED = "approved"        # User approved
    SUBMITTED = "submitted"      # Sent to accountant/IRD


class FlagSeverity(str, enum.Enum):
    """Severity level of workings flags."""
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


class FlagCategory(str, enum.Enum):
    """Category of workings flags."""
    MISSING_DOCUMENT = "missing_document"
    MISMATCH = "mismatch"
    REVIEW_REQUIRED = "review_required"
    ANOMALY = "anomaly"
    INVOICE_REQUIRED = "invoice_required"
    VERIFICATION = "verification"
    DATA_QUALITY = "data_quality"
    COMPLIANCE = "compliance"
    CAPITAL_TREATMENT = "capital_treatment"  # For items that should be capital, not deductible


class FlagStatus(str, enum.Enum):
    """Status of a flag."""
    OPEN = "open"
    RESOLVED = "resolved"
    IGNORED = "ignored"


class RequestStatus(str, enum.Enum):
    """Status of a document request."""
    PENDING = "pending"
    SENT = "sent"
    RECEIVED = "received"
    CANCELLED = "cancelled"


class QuestionStatus(str, enum.Enum):
    """Status of a client question."""
    PENDING = "pending"
    SENT = "sent"
    ANSWERED = "answered"


class TaxReturnWorkings(Base):
    """
    Stores the AI Brain generated workings for a tax return.
    This is the main output of Phase 2 processing.
    """
    __tablename__ = "tax_return_workings"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tax_return_id = Column(UUID(as_uuid=True), ForeignKey("tax_returns.id"), nullable=False)
    version = Column(Integer, default=1, nullable=False)

    # Summary totals
    total_income = Column(Numeric(12, 2), nullable=True)
    total_expenses = Column(Numeric(12, 2), nullable=True)
    total_deductions = Column(Numeric(12, 2), nullable=True)  # After deductibility rules
    net_rental_income = Column(Numeric(12, 2), nullable=True)

    # Interest breakdown
    interest_gross = Column(Numeric(12, 2), nullable=True)
    interest_deductible_percentage = Column(Float, nullable=True)
    interest_deductible_amount = Column(Numeric(12, 2), nullable=True)

    # Full workings data as JSON
    income_workings = Column(JSONB, nullable=True)
    expense_workings = Column(JSONB, nullable=True)
    document_inventory = Column(JSONB, nullable=True)
    processing_notes = Column(JSONB, nullable=True)
    audit_trail = Column(JSONB, nullable=True)

    # AI processing metadata
    ai_model_used = Column(String(100), nullable=True)
    ai_prompt_version = Column(String(50), nullable=True)
    processing_time_seconds = Column(Float, nullable=True)

    # Status and review
    status = Column(Enum(WorkingsStatus), default=WorkingsStatus.DRAFT, nullable=False)
    reviewed_by = Column(String(255), nullable=True)
    reviewed_at = Column(DateTime(timezone=True), nullable=True)
    approved_by = Column(String(255), nullable=True)
    approved_at = Column(DateTime(timezone=True), nullable=True)

    created_at = Column(DateTime(timezone=True), default=local_now, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=local_now, onupdate=local_now, nullable=False)

    # Relationships
    tax_return = relationship("TaxReturn")
    flags = relationship("WorkingsFlag", back_populates="workings", cascade="all, delete-orphan")
    document_requests = relationship("DocumentRequest", back_populates="workings", cascade="all, delete-orphan")
    client_questions = relationship("ClientQuestion", back_populates="workings", cascade="all, delete-orphan")

    __table_args__ = (
        Index('ix_workings_tax_return', 'tax_return_id'),
        Index('ix_workings_status', 'status'),
    )


class WorkingsFlag(Base):
    """
    Flags raised during workings generation that need attention.
    E.g., missing documents, mismatches, items needing review.
    """
    __tablename__ = "workings_flags"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workings_id = Column(UUID(as_uuid=True), ForeignKey("tax_return_workings.id"), nullable=False)

    # Flag details
    severity = Column(Enum(FlagSeverity), nullable=False)
    category = Column(Enum(FlagCategory), nullable=False)
    message = Column(Text, nullable=False)
    action_required = Column(Text, nullable=True)

    # Related items
    related_transaction_ids = Column(JSONB, nullable=True)  # List of transaction IDs
    related_document_id = Column(UUID(as_uuid=True), ForeignKey("documents.id"), nullable=True)
    related_amount = Column(Numeric(12, 2), nullable=True)

    # Resolution
    status = Column(Enum(FlagStatus), default=FlagStatus.OPEN, nullable=False)
    resolved_by = Column(String(255), nullable=True)
    resolved_at = Column(DateTime(timezone=True), nullable=True)
    resolution_notes = Column(Text, nullable=True)

    created_at = Column(DateTime(timezone=True), default=local_now, nullable=False)

    # Relationships
    workings = relationship("TaxReturnWorkings", back_populates="flags")
    related_document = relationship("Document")

    __table_args__ = (
        Index('ix_flags_workings', 'workings_id'),
        Index('ix_flags_status', 'status'),
        Index('ix_flags_severity', 'severity'),
    )


class DocumentRequest(Base):
    """
    Tracks document requests sent to clients.
    Generated from missing document detection.
    """
    __tablename__ = "document_requests"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workings_id = Column(UUID(as_uuid=True), ForeignKey("tax_return_workings.id"), nullable=True)
    tax_return_id = Column(UUID(as_uuid=True), ForeignKey("tax_returns.id"), nullable=False)

    # Request details
    document_type = Column(String(50), nullable=False)
    reason = Column(Text, nullable=False)
    priority = Column(String(20), default="required", nullable=False)  # required, recommended
    details = Column(Text, nullable=True)

    # Status tracking
    status = Column(Enum(RequestStatus), default=RequestStatus.PENDING, nullable=False)
    sent_at = Column(DateTime(timezone=True), nullable=True)
    sent_method = Column(String(50), nullable=True)  # email, sms, portal
    received_at = Column(DateTime(timezone=True), nullable=True)
    received_document_id = Column(UUID(as_uuid=True), ForeignKey("documents.id"), nullable=True)

    created_at = Column(DateTime(timezone=True), default=local_now, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=local_now, onupdate=local_now, nullable=False)

    # Relationships
    workings = relationship("TaxReturnWorkings", back_populates="document_requests")
    tax_return = relationship("TaxReturn")
    received_document = relationship("Document")

    __table_args__ = (
        Index('ix_doc_requests_tax_return', 'tax_return_id'),
        Index('ix_doc_requests_status', 'status'),
    )


class ClientQuestion(Base):
    """
    Questions to ask the client for clarification.
    E.g., "Is this insurance payment landlord or personal?"
    """
    __tablename__ = "client_questions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workings_id = Column(UUID(as_uuid=True), ForeignKey("tax_return_workings.id"), nullable=True)
    tax_return_id = Column(UUID(as_uuid=True), ForeignKey("tax_returns.id"), nullable=False)

    # Question details
    question = Column(Text, nullable=False)
    context = Column(Text, nullable=True)  # Background info for the question
    options = Column(JSONB, nullable=True)  # Multiple choice options if applicable
    related_transaction_id = Column(UUID(as_uuid=True), ForeignKey("transactions.id"), nullable=True)
    related_amount = Column(Numeric(12, 2), nullable=True)

    # Status tracking
    status = Column(Enum(QuestionStatus), default=QuestionStatus.PENDING, nullable=False)
    sent_at = Column(DateTime(timezone=True), nullable=True)
    answered_at = Column(DateTime(timezone=True), nullable=True)
    answer = Column(Text, nullable=True)
    answer_option_index = Column(Integer, nullable=True)  # If multiple choice

    # Impact
    affects_category = Column(String(50), nullable=True)
    affects_deductibility = Column(Boolean, default=False)

    created_at = Column(DateTime(timezone=True), default=local_now, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=local_now, onupdate=local_now, nullable=False)

    # Relationships
    workings = relationship("TaxReturnWorkings", back_populates="client_questions")
    tax_return = relationship("TaxReturn")
    related_transaction = relationship("Transaction")

    __table_args__ = (
        Index('ix_questions_tax_return', 'tax_return_id'),
        Index('ix_questions_status', 'status'),
    )


class DocumentInventoryRecord(Base):
    """
    Stores the document inventory for a tax return.
    Tracks what's provided, missing, and excluded.
    """
    __tablename__ = "document_inventory"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tax_return_id = Column(UUID(as_uuid=True), ForeignKey("tax_returns.id"), nullable=False)

    # Full inventory as JSON
    inventory_data = Column(JSONB, nullable=False)

    # Summary counts
    provided_count = Column(Integer, default=0)
    missing_count = Column(Integer, default=0)
    excluded_count = Column(Integer, default=0)
    blocking_issues_count = Column(Integer, default=0)

    # Key document flags
    has_pm_statement = Column(Boolean, default=False)
    has_bank_statement = Column(Boolean, default=False)
    has_loan_statement = Column(Boolean, default=False)
    has_rates_invoice = Column(Boolean, default=False)
    has_insurance_policy = Column(Boolean, default=False)

    created_at = Column(DateTime(timezone=True), default=local_now, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=local_now, onupdate=local_now, nullable=False)

    # Relationships
    tax_return = relationship("TaxReturn")

    __table_args__ = (
        Index('ix_inventory_tax_return', 'tax_return_id'),
        UniqueConstraint('tax_return_id', name='uq_inventory_tax_return'),
    )


# Database Indexes for new models
Index("ix_workings_created_at", TaxReturnWorkings.created_at.desc())
Index("ix_flags_created_at", WorkingsFlag.created_at.desc())
Index("ix_doc_requests_created_at", DocumentRequest.created_at.desc())
Index("ix_questions_created_at", ClientQuestion.created_at.desc())
