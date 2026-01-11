"""Pydantic schemas for transaction processing."""
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Dict, List, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict


# Enums
class TransactionTypeEnum(str, Enum):
    """Transaction type enumeration."""
    INCOME = "income"
    EXPENSE = "expense"
    EXCLUDED = "excluded"
    FLAGGED = "flagged"


class CategorizationSource(str, Enum):
    """Source of categorization."""
    YAML_PATTERN = "yaml_pattern"
    LEARNED_EXACT = "learned_exact"
    LEARNED_FUZZY = "learned_fuzzy"
    CLAUDE = "claude"
    MANUAL = "manual"


# P&L Row Mappings
class PLRowMappingBase(BaseModel):
    """Base P&L row mapping schema."""
    category_code: str
    pl_row: Optional[int] = None
    display_name: str
    category_group: Optional[str] = None
    transaction_type: str
    is_deductible: bool = True
    default_source: Optional[str] = None
    sort_order: int = 0


class PLRowMappingResponse(PLRowMappingBase):
    """Schema for P&L row mapping response."""
    id: UUID

    model_config = ConfigDict(from_attributes=True)


# Transactions
class TransactionBase(BaseModel):
    """Base transaction schema."""
    transaction_date: date
    description: str
    other_party: Optional[str] = None
    amount: Decimal
    balance: Optional[Decimal] = None


class TransactionCreate(TransactionBase):
    """Schema for creating a transaction."""
    tax_return_id: UUID
    document_id: Optional[UUID] = None
    category_code: Optional[str] = None
    transaction_type: Optional[TransactionTypeEnum] = None
    is_deductible: bool = True
    deductible_percentage: float = 100.0
    gst_inclusive: bool = True
    gst_amount: Optional[Decimal] = None
    confidence: float = 0.0
    categorization_source: Optional[str] = None
    categorization_trace: Optional[Dict[str, Any]] = None
    needs_review: bool = False
    review_reason: Optional[str] = None
    raw_data: Optional[Dict[str, Any]] = None


class TransactionUpdate(BaseModel):
    """Schema for updating a transaction."""
    category_code: Optional[str] = None
    transaction_type: Optional[TransactionTypeEnum] = None
    is_deductible: Optional[bool] = None
    deductible_percentage: Optional[float] = None
    gst_inclusive: Optional[bool] = None
    gst_amount: Optional[Decimal] = None
    needs_review: Optional[bool] = None
    review_reason: Optional[str] = None
    notes: Optional[str] = None  # For feedback


class TransactionResponse(TransactionBase):
    """Schema for transaction response."""
    id: UUID
    tax_return_id: UUID
    document_id: Optional[UUID] = None

    # Categorization
    category_code: Optional[str] = None
    category_display_name: Optional[str] = None  # From PLRowMapping
    transaction_type: Optional[TransactionTypeEnum] = None
    pl_row: Optional[int] = None  # From PLRowMapping

    # Deductibility
    is_deductible: bool = True
    deductible_percentage: float = 100.0
    deductible_amount: Optional[Decimal] = None

    # GST
    gst_inclusive: bool = True
    gst_amount: Optional[Decimal] = None

    # Review status
    confidence: float = 0.0
    categorization_source: Optional[str] = None
    categorization_trace: Optional[Dict[str, Any]] = None
    needs_review: bool = False
    review_reason: Optional[str] = None
    manually_reviewed: bool = False
    reviewed_by: Optional[str] = None
    reviewed_at: Optional[datetime] = None

    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class MatchingTransactionInfo(BaseModel):
    """Info about a matching transaction for bulk categorization prompt."""
    id: UUID
    transaction_date: date
    description: str
    other_party: Optional[str] = None
    amount: Decimal
    current_category: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class TransactionUpdateResponse(BaseModel):
    """Response for transaction update with matching transactions info."""
    transaction: "TransactionResponse"
    matching_transactions: List[MatchingTransactionInfo] = []
    matching_count: int = 0
    prompt_message: Optional[str] = None


class TransactionBulkUpdate(BaseModel):
    """Schema for bulk updating transactions."""
    transaction_ids: List[UUID]
    category_code: str
    apply_to_similar: bool = False  # Create pattern for future matching


# Transaction Summaries
class TransactionSummaryResponse(BaseModel):
    """Schema for transaction summary response."""
    id: UUID
    tax_return_id: UUID
    category_code: str
    category_display_name: Optional[str] = None
    pl_row: Optional[int] = None
    transaction_type: Optional[str] = None
    category_group: Optional[str] = None
    default_source: Optional[str] = None

    transaction_count: int
    gross_amount: Decimal
    deductible_amount: Decimal
    gst_amount: Optional[Decimal] = None

    monthly_breakdown: Optional[Dict[str, Decimal]] = None

    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_orm_with_mapping(cls, summary):
        """Create response from ORM model with category_mapping relationship."""
        data = {
            'id': summary.id,
            'tax_return_id': summary.tax_return_id,
            'category_code': summary.category_code,
            'transaction_count': summary.transaction_count,
            'gross_amount': summary.gross_amount,
            'deductible_amount': summary.deductible_amount,
            'gst_amount': summary.gst_amount,
            'monthly_breakdown': summary.monthly_breakdown,
            'created_at': summary.created_at,
            'updated_at': summary.updated_at,
        }

        # Add fields from category_mapping if available
        if hasattr(summary, 'category_mapping') and summary.category_mapping:
            data['category_display_name'] = summary.category_mapping.display_name
            data['pl_row'] = summary.category_mapping.pl_row
            data['transaction_type'] = summary.category_mapping.transaction_type

        return cls(**data)

    model_config = ConfigDict(from_attributes=True)


# Transaction Patterns
class TransactionPatternBase(BaseModel):
    """Base transaction pattern schema."""
    description_normalized: str
    other_party_normalized: Optional[str] = None
    category_code: str


class TransactionPatternCreate(TransactionPatternBase):
    """Schema for creating a transaction pattern."""
    is_global: bool = True
    client_id: Optional[UUID] = None
    source: str = "user_correction"


class TransactionPatternResponse(TransactionPatternBase):
    """Schema for transaction pattern response."""
    id: UUID
    confidence: float
    times_applied: int
    times_confirmed: int
    times_corrected: int
    is_global: bool
    client_id: Optional[UUID] = None
    source: str
    created_at: datetime
    last_used_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


# Category Feedback
class CategoryFeedbackCreate(BaseModel):
    """Schema for creating category feedback (correction)."""
    transaction_id: UUID
    corrected_category: str
    corrected_by: Optional[str] = None
    notes: Optional[str] = None
    create_pattern: bool = True  # Whether to create/update a pattern


class CategoryFeedbackResponse(BaseModel):
    """Schema for category feedback response."""
    id: UUID
    transaction_id: UUID
    original_category: Optional[str] = None
    corrected_category: str
    corrected_by: Optional[str] = None
    corrected_at: datetime
    notes: Optional[str] = None
    pattern_created: bool
    pattern_id: Optional[UUID] = None

    model_config = ConfigDict(from_attributes=True)


# Processing Result
class ProcessingResult(BaseModel):
    """Result of transaction processing."""
    success: bool
    message: str
    transactions: List[TransactionResponse] = []
    summary: Optional[TransactionSummaryResponse] = None
    total_transactions: Optional[int] = None
    transactions_categorized: Optional[int] = None
    transactions_needing_review: Optional[int] = None
    documents_processed: Optional[List[str]] = None
    blocking_issues: Optional[List[str]] = None


# Extraction Results
class ExtractedTransaction(BaseModel):
    """Schema for a transaction extracted from a document."""
    transaction_date: date
    description: str
    other_party: Optional[str] = None
    amount: Decimal
    balance: Optional[Decimal] = None

    # Initial categorization from extraction
    suggested_category: Optional[str] = None
    confidence: float = 0.0

    # Flags
    needs_review: bool = False
    review_reason: Optional[str] = None

    # Raw data for audit
    raw_data: Optional[Dict[str, Any]] = None
    row_number: Optional[int] = None  # Row in CSV/document


class DocumentExtractionResult(BaseModel):
    """Schema for extraction result from a single document."""
    document_id: UUID
    document_type: str
    filename: str

    transactions: List[ExtractedTransaction]
    extraction_method: str  # 'csv_parser', 'claude_vision', 'claude_text'

    # Summary
    total_transactions: int
    total_income: Decimal = Decimal("0")
    total_expenses: Decimal = Decimal("0")

    # Errors/warnings
    errors: List[str] = []
    warnings: List[str] = []


class TaxReturnExtractionResult(BaseModel):
    """Schema for extraction result for entire tax return."""
    tax_return_id: UUID

    documents_processed: List[DocumentExtractionResult]

    # Totals
    total_transactions: int
    transactions_categorized: int
    transactions_needing_review: int

    # By category
    category_summaries: List[TransactionSummaryResponse]

    # Status
    ready_for_review: bool
    blocking_issues: List[str] = []


class TransactionListResponse(BaseModel):
    """Schema for paginated transaction list."""
    transactions: List[TransactionResponse]
    total: int
    page: int
    page_size: int
    total_pages: int

    # Summary stats
    total_income: Decimal
    total_expenses: Decimal
    needs_review_count: int


# Interest-specific schemas (for Phase 2 compatibility)
class InterestTransaction(BaseModel):
    """Schema for interest transaction."""
    date: date
    description: str
    amount: Decimal
    loan_account: Optional[str] = None
    month: str  # "Apr-24", "May-24", etc.


class InterestSummary(BaseModel):
    """Schema for interest calculation summary."""
    tax_return_id: UUID

    # By loan account
    loan_accounts: Dict[str, Decimal]  # {"Loan 1": 12345.67, "Loan 2": 5678.90}

    # Monthly breakdown
    monthly_breakdown: Dict[str, Dict[str, Decimal]]  # {"Apr-24": {"Loan 1": 1234, "Loan 2": 567}}

    # Totals
    gross_interest: Decimal
    deductible_percentage: float  # 100 or 80
    deductible_interest: Decimal
    capitalised_interest: Decimal  # gross - deductible

    # Metadata
    interest_frequency: str  # "bi-weekly", "monthly"
    has_offset_account: bool = False
    notes: List[str] = []