"""Pydantic schemas for documents and tax returns."""

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.db_models import DocumentStatus, PropertyType, TaxReturnStatus, local_now


class DocumentType(str, Enum):
    """Document type enumeration."""

    BANK_STATEMENT = "bank_statement"
    LOAN_STATEMENT = "loan_statement"
    SETTLEMENT_STATEMENT = "settlement_statement"
    DEPRECIATION_SCHEDULE = "depreciation_schedule"
    BODY_CORPORATE = "body_corporate"
    PROPERTY_MANAGER_STATEMENT = "property_manager_statement"
    LIM_REPORT = "lim_report"
    HEALTHY_HOMES = "healthy_homes"
    METH_TEST = "meth_test"
    SMOKE_ALARM = "smoke_alarm"
    CCC = "ccc"
    LANDLORD_INSURANCE = "landlord_insurance"
    RATES = "rates"
    PERSONAL_EXPENDITURE_CLAIMS = "personal_expenditure_claims"
    OTHER = "other"
    INVALID = "invalid"


class FlaggedTransactionReason(str, Enum):
    """Reason why a transaction was flagged."""

    LARGE_PAYMENT = "large_payment"  # Single payment > $500 NZD
    CASH_TRANSACTION = "cash_transaction"  # Cash withdrawal/deposit
    UNUSUAL_VENDOR = "unusual_vendor"  # Unknown vendor or individual
    UNCLEAR_PURPOSE = "unclear_purpose"  # Could be personal expense


class FlaggedTransaction(BaseModel):
    """A transaction flagged for review."""

    date: str
    description: str
    amount: float
    flag_reasons: List[FlaggedTransactionReason]
    severity: Literal["info", "warning", "critical"]
    recommended_action: str
    vendor_name: Optional[str] = None


class TransactionAnalysis(BaseModel):
    """Analysis of transactions in a financial document."""

    total_transactions: int = 0
    flagged_transactions: List[FlaggedTransaction] = []
    summary: str = ""
    requires_invoices: bool = False


class ProcessedFile(BaseModel):
    """Processed file information."""

    file_path: str
    file_type: Literal["digital_pdf", "scanned_pdf", "image", "spreadsheet"]
    text_content: Optional[str] = None
    image_paths: Optional[List[str]] = None
    page_count: int


class DocumentClassification(BaseModel):
    """Document classification result from Claude."""

    document_type: str
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning: str
    flags: List[str] = []
    key_details: Dict[str, Any] = {}


class DocumentAnalysis(BaseModel):
    """Complete document analysis result."""

    document_id: UUID
    filename: str
    classification: DocumentClassification
    extracted_data: Dict[str, Any] = {}
    status: DocumentStatus


class DocumentSummary(BaseModel):
    """Summary of a document for review."""

    document_id: UUID
    filename: str
    document_type: str
    key_details: Dict[str, Any]
    flags: List[str] = []


class MissingDocument(BaseModel):
    """Information about a missing document."""

    document_type: str
    required: bool = False
    impact: str = ""
    action: str = ""


class BlockingIssue(BaseModel):
    """A blocking issue found during review."""

    severity: Literal["critical", "high", "medium"]
    issue: str
    document_id: Optional[UUID] = None
    document_name: Optional[str] = None


class FlaggedTransactionItem(BaseModel):
    """A flagged transaction item for the summary."""

    document: str
    transaction: str
    amount: float
    reason: str
    action_required: str


class FlaggedTransactionsSummary(BaseModel):
    """Summary of all flagged transactions across documents."""

    total_flagged: int = 0
    critical_count: int = 0
    transactions_requiring_invoices: List[FlaggedTransactionItem] = []
    recommendation: str = ""


class TaxReturnReview(BaseModel):
    """Complete tax return review result."""

    tax_return_id: UUID
    status: TaxReturnStatus
    documents_processed: int
    documents_analyzed: List[DocumentAnalysis]
    missing_documents: List[MissingDocument] = []
    blocking_issues: List[str] = []  # Changed to List[str]
    recommendations: List[str] = []
    completeness_score: float = Field(default=0.0, ge=0.0, le=1.0)
    review_summary: str = ""
    flagged_transactions_summary: Optional[FlaggedTransactionsSummary] = None
    created_at: datetime = Field(default_factory=local_now)


class TaxReturnCreate(BaseModel):
    """Tax return creation request."""

    client_name: str = Field(..., min_length=1, max_length=255)
    property_address: str = Field(..., min_length=1)
    tax_year: str = Field(..., pattern="^FY\\d{2}$")  # e.g., FY25
    property_type: PropertyType
    gst_registered: Optional[bool] = None  # None = user wants AI suggestion
    year_of_ownership: int = Field(..., ge=1, le=100)


class TaxReturnResponse(BaseModel):
    """Tax return response."""

    id: UUID
    client_id: UUID
    client_name: str
    property_address: str
    tax_year: str
    property_type: PropertyType
    gst_registered: Optional[bool] = None
    year_of_ownership: int
    status: TaxReturnStatus
    review_result: Optional[Dict[str, Any]] = None
    created_at: datetime
    updated_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class DocumentResponse(BaseModel):
    """Document response."""

    id: UUID
    tax_return_id: UUID
    original_filename: str
    document_type: Optional[str] = None
    classification_confidence: Optional[float] = None
    extracted_data: Optional[Dict[str, Any]] = None
    status: DocumentStatus
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
