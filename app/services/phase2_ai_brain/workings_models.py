"""Pydantic models for AI Brain workings output."""

import datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Dict, List, Optional
from uuid import UUID

from pydantic import BaseModel, Field


class VerificationStatus(str, Enum):
    """Verification status for line items."""
    VERIFIED = "verified"
    NEEDS_REVIEW = "needs_review"
    UNVERIFIED = "unverified"
    MISSING_INVOICE = "missing_invoice"
    ESTIMATED = "estimated"


class FlagSeverity(str, Enum):
    """Severity of flags."""
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"

    @classmethod
    def from_string(cls, value: str) -> "FlagSeverity":
        """Convert string to FlagSeverity, handling variations."""
        if not value:
            return cls.MEDIUM
        value_lower = value.lower().strip()
        mapping = {
            "critical": cls.CRITICAL,
            "high": cls.HIGH,
            "medium": cls.MEDIUM,
            "low": cls.LOW,
            "info": cls.INFO,
            "warning": cls.MEDIUM,
            "error": cls.HIGH,
        }
        return mapping.get(value_lower, cls.MEDIUM)


class FlagCategory(str, Enum):
    """Category of flags."""
    MISSING_DOCUMENT = "missing_document"
    MISMATCH = "mismatch"
    REVIEW_REQUIRED = "review_required"
    ANOMALY = "anomaly"
    INVOICE_REQUIRED = "invoice_required"
    VERIFICATION = "verification"
    DATA_QUALITY = "data_quality"
    COMPLIANCE = "compliance"
    CAPITAL_TREATMENT = "capital_treatment"  # For items that should be capital, not deductible


class SourceCode(str, Enum):
    """Source reference codes for audit trail."""
    BS = "BS"      # Bank Statement
    SS = "SS"      # Settlement Statement
    PM = "PM"      # Property Manager
    LS = "LS"      # Loan Statement
    INV = "INV"    # Invoice
    DEP = "DEP"    # Depreciation Schedule
    CP = "CP"      # Client Provided
    AF = "AF"      # Accounting Fees (standard)
    AI = "AI"      # Additional Information
    CALC = "CALC"  # Calculated


class SourceReference(BaseModel):
    """Reference to a source document/transaction."""

    source_code: str  # BS, SS, PM, etc.
    document_name: str  # e.g., "Kiwibank Statement Mar 2024"
    document_id: Optional[UUID] = None
    line_reference: Optional[str] = None  # e.g., "Row 45", "Page 2"
    amount: Optional[Decimal] = None
    date: Optional[datetime.date] = None
    description: Optional[str] = None


class CalculationLogic(BaseModel):
    """Detailed calculation logic for audit trail."""

    # Primary source
    primary_source_code: str  # BS, SS, PM, etc.
    primary_source_name: str  # Human-readable source name

    # Calculation method
    calculation_method: str  # e.g., "Sum of 12 monthly rent deposits"
    formula: Optional[str] = None  # e.g., "=SUM(Bank!D10:D21)"

    # All source references used
    source_references: List[SourceReference] = Field(default_factory=list)

    # Cross-validation notes
    cross_validated_with: List[str] = Field(default_factory=list)  # e.g., ["PM Statement totals"]
    validation_status: str = "not_validated"  # matched, variance, not_validated
    variance_amount: Optional[Decimal] = None
    variance_notes: Optional[str] = None

    # Adjustments applied
    adjustments: List[Dict[str, Any]] = Field(default_factory=list)
    # e.g., [{"description": "Less: Bond payment", "amount": -1000}]

    # Final calculation breakdown
    calculation_steps: List[str] = Field(default_factory=list)
    # e.g., ["Gross rent from PM: $55,000", "Less water charges: -$500", "Net: $54,500"]


class WorkingsTransaction(BaseModel):
    """Individual transaction in workings."""

    transaction_id: Optional[UUID] = None
    date: Optional[datetime.date] = None
    description: str
    amount: Decimal
    other_party: Optional[str] = None
    source_document: str  # e.g., "Bank Statement", "PM Statement"
    source_code: str = "BS"  # Source reference code
    source_document_id: Optional[UUID] = None
    line_reference: Optional[str] = None  # Row/page reference in source
    verification_status: VerificationStatus = VerificationStatus.UNVERIFIED
    notes: Optional[str] = None
    matched_invoice_id: Optional[UUID] = None


class LineItem(BaseModel):
    """A line item in workings (income or expense category)."""

    category_code: str
    display_name: str
    pl_row: Optional[int] = None  # P&L row number (e.g., 6 for rental income)
    gross_amount: Decimal
    deductible_percentage: float = 100.0
    deductible_amount: Decimal
    source: str  # e.g., "PM Statement (Quinovic)", "Bank Statement + Rates Invoice"
    source_code: str = "BS"  # Primary source code (BS, SS, PM, etc.)
    verification_status: VerificationStatus = VerificationStatus.UNVERIFIED
    transactions: List[WorkingsTransaction] = Field(default_factory=list)
    notes: Optional[str] = None
    monthly_breakdown: Optional[Dict[str, float]] = None  # For interest

    # Detailed calculation logic for audit trail
    calculation_logic: Optional[CalculationLogic] = None


class RepairItem(BaseModel):
    """Individual repair transaction with invoice tracking."""

    transaction_id: Optional[UUID] = None
    date: Optional[datetime.date] = None
    description: str
    amount: Decimal
    payee: Optional[str] = None
    invoice_status: str  # "verified", "missing_required", "missing_optional", "not_required"
    invoice_document_id: Optional[UUID] = None
    notes: Optional[str] = None


class RepairsLineItem(LineItem):
    """Repairs & Maintenance with invoice tracking."""

    repair_items: List[RepairItem] = Field(default_factory=list)
    items_requiring_invoice: int = 0  # Count of items > $800 without invoice


class IncomeWorkings(BaseModel):
    """Income section of workings."""

    rental_income: Optional[LineItem] = None
    water_rates_recovered: Optional[LineItem] = None
    bank_contribution: Optional[LineItem] = None
    insurance_payout: Optional[LineItem] = None
    other_income: Optional[LineItem] = None

    # Excluded income (tracked but not included in totals)
    bond_received: Optional[LineItem] = None

    # Totals
    total_income: Decimal = Decimal("0")

    def calculate_total(self) -> Decimal:
        """Calculate total income from all categories."""
        total = Decimal("0")
        for field_name in ["rental_income", "water_rates_recovered", "bank_contribution",
                          "insurance_payout", "other_income"]:
            item = getattr(self, field_name, None)
            if item:
                total += item.gross_amount
        self.total_income = total
        return total


class ExpenseWorkings(BaseModel):
    """Expense section of workings."""

    # Interest (special handling for deductibility)
    interest: Optional[LineItem] = None

    # Standard expenses
    rates: Optional[LineItem] = None
    water_rates: Optional[LineItem] = None
    body_corporate: Optional[LineItem] = None
    resident_society: Optional[LineItem] = None  # Separate from BC (Row 36)
    insurance: Optional[LineItem] = None
    agent_fees: Optional[LineItem] = None
    repairs_maintenance: Optional[RepairsLineItem] = None
    legal_fees: Optional[LineItem] = None
    bank_fees: Optional[LineItem] = None
    advertising: Optional[LineItem] = None
    depreciation: Optional[LineItem] = None  # Row 17
    accounting_fees: Optional[LineItem] = None  # Row 16 - Standard $862.50
    due_diligence: Optional[LineItem] = None  # Row 18 - LIM, meth test, etc.
    other_expenses: Optional[LineItem] = None

    # Excluded expenses (tracked but not deductible)
    principal_repayment: Optional[LineItem] = None
    capital_expenses: Optional[LineItem] = None

    # Totals
    total_expenses_gross: Decimal = Decimal("0")
    total_deductions: Decimal = Decimal("0")

    def calculate_totals(self) -> tuple:
        """Calculate total expenses and deductions."""
        gross = Decimal("0")
        deductible = Decimal("0")

        deductible_fields = [
            "interest", "rates", "water_rates", "body_corporate", "resident_society",
            "insurance", "agent_fees", "repairs_maintenance", "legal_fees", "bank_fees",
            "advertising", "depreciation", "accounting_fees", "due_diligence", "other_expenses"
        ]

        for field_name in deductible_fields:
            item = getattr(self, field_name, None)
            if item:
                gross += abs(item.gross_amount)
                deductible += abs(item.deductible_amount)

        self.total_expenses_gross = gross
        self.total_deductions = deductible
        return gross, deductible


class WorkingsSummary(BaseModel):
    """Summary totals for the tax return."""

    # Income
    total_income: Decimal = Decimal("0")

    # Expenses
    total_expenses: Decimal = Decimal("0")
    total_deductions: Decimal = Decimal("0")

    # Interest detail
    interest_gross: Decimal = Decimal("0")
    interest_deductible_percentage: float = 100.0
    interest_deductible_amount: Decimal = Decimal("0")

    # Net result
    net_rental_income: Decimal = Decimal("0")

    # Counts
    transactions_processed: int = 0
    transactions_needing_review: int = 0
    flags_count: int = 0


class WorkingsFlag(BaseModel):
    """A flag raised during workings generation."""

    severity: FlagSeverity
    category: FlagCategory
    message: str
    action_required: str
    related_transaction_ids: List[UUID] = Field(default_factory=list)
    related_document_id: Optional[UUID] = None
    related_amount: Optional[Decimal] = None


class DocumentRequestData(BaseModel):
    """A document request to send to client."""

    document_type: str
    reason: str
    priority: str = "required"  # required, recommended
    details: Optional[str] = None


class ClientQuestionData(BaseModel):
    """A question to ask the client."""

    question: str
    context: Optional[str] = None
    options: List[str] = Field(default_factory=list)
    related_transaction_id: Optional[UUID] = None
    related_amount: Optional[Decimal] = None
    affects_category: Optional[str] = None
    affects_deductibility: bool = False


class DocumentStatusData(BaseModel):
    """Status of a document type."""

    status: str  # "received", "partial", "missing", "not_applicable"
    period_covered: Optional[str] = None
    notes: Optional[str] = None


class DocumentsStatus(BaseModel):
    """Status of all document types."""

    pm_statement: DocumentStatusData = Field(default_factory=lambda: DocumentStatusData(status="missing"))
    bank_statement: DocumentStatusData = Field(default_factory=lambda: DocumentStatusData(status="missing"))
    loan_statement: DocumentStatusData = Field(default_factory=lambda: DocumentStatusData(status="missing"))
    rates_invoice: DocumentStatusData = Field(default_factory=lambda: DocumentStatusData(status="missing"))
    insurance_policy: DocumentStatusData = Field(default_factory=lambda: DocumentStatusData(status="missing"))
    settlement_statement: DocumentStatusData = Field(default_factory=lambda: DocumentStatusData(status="not_applicable"))
    other_documents: List[DocumentStatusData] = Field(default_factory=list)


class TaxReturnWorkingsData(BaseModel):
    """Complete workings output from AI Brain."""

    # Tax return info
    tax_return_id: UUID
    property_address: str
    tax_year: str
    property_type: str

    # Summary
    summary: WorkingsSummary

    # Detailed workings
    income: IncomeWorkings
    expenses: ExpenseWorkings

    # Flags and requests
    flags: List[WorkingsFlag] = Field(default_factory=list)
    document_requests: List[DocumentRequestData] = Field(default_factory=list)
    client_questions: List[ClientQuestionData] = Field(default_factory=list)

    # Document status
    documents_status: DocumentsStatus = Field(default_factory=DocumentsStatus)

    # Processing notes
    processing_notes: List[str] = Field(default_factory=list)

    # Audit trail
    audit_trail: List[Dict[str, Any]] = Field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return self.model_dump(mode="json")

    def calculate_all_totals(self) -> None:
        """Calculate all totals from the workings data."""
        # Calculate income total
        self.income.calculate_total()

        # Calculate expense totals
        self.expenses.calculate_totals()

        # Update summary
        self.summary.total_income = self.income.total_income
        self.summary.total_expenses = self.expenses.total_expenses_gross
        self.summary.total_deductions = self.expenses.total_deductions

        # Interest detail
        if self.expenses.interest:
            self.summary.interest_gross = abs(self.expenses.interest.gross_amount)
            self.summary.interest_deductible_percentage = self.expenses.interest.deductible_percentage
            self.summary.interest_deductible_amount = abs(self.expenses.interest.deductible_amount)

        # Net rental income
        self.summary.net_rental_income = self.summary.total_income - self.summary.total_deductions

        # Counts
        self.summary.flags_count = len(self.flags)
