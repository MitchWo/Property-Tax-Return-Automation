"""Document Inventory service for tracking provided, missing, and excluded documents."""

import logging
from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from typing import Any, Dict, List, Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


class DocumentStatus(str, Enum):
    """Status of a document in the inventory."""

    PROVIDED = "provided"          # Document received and extracted
    MISSING = "missing"            # Required but not provided
    EXCLUDED = "excluded"          # Provided but not relevant
    PARTIAL = "partial"            # Partially provided (e.g., missing months)
    WRONG_TYPE = "wrong_type"      # Wrong document type (e.g., home insurance vs landlord)
    DUPLICATE = "duplicate"        # Duplicate of another document


class MissingSeverity(str, Enum):
    """Severity level for missing documents."""

    REQUIRED = "required"          # Must have to complete return
    RECOMMENDED = "recommended"    # Should have but can proceed without
    OPTIONAL = "optional"          # Nice to have


class ExclusionReason(str, Enum):
    """Reasons for excluding a document."""

    NOT_RELEVANT = "not_relevant"           # Not related to rental property
    WRONG_PROPERTY = "wrong_property"       # Different property address
    WRONG_DOCUMENT_TYPE = "wrong_type"      # e.g., home insurance instead of landlord
    PERSONAL_EXPENSE = "personal"           # Personal, not rental related
    DUPLICATE = "duplicate"                 # Already have this document
    OUTSIDE_PERIOD = "outside_period"       # Outside tax year
    UNREADABLE = "unreadable"              # Cannot extract content


@dataclass
class ProvidedDocument:
    """A document that has been provided and processed."""

    document_id: UUID
    document_type: str
    filename: str
    status: DocumentStatus = DocumentStatus.PROVIDED
    period_start: Optional[date] = None
    period_end: Optional[date] = None
    extraction_confidence: float = 0.0
    key_details: Dict[str, Any] = field(default_factory=dict)
    notes: Optional[str] = None


@dataclass
class MissingDocument:
    """A document that is expected but not provided."""

    document_type: str
    reason: str
    severity: MissingSeverity
    detected_from: str  # What triggered this detection (e.g., "bank_statement", "phase2_analysis")
    details: Optional[str] = None


@dataclass
class ExcludedDocument:
    """A document that was provided but excluded from processing."""

    document_id: Optional[UUID]
    filename: str
    original_type: Optional[str]  # What it was classified as
    exclusion_reason: ExclusionReason
    explanation: str
    can_reinclude: bool = False  # Can user override and include it?


@dataclass
class BlockingIssue:
    """A blocking issue that prevents return completion."""

    issue_type: str
    severity: str  # "high", "medium", "low"
    message: str
    resolution: str
    related_document_id: Optional[UUID] = None


@dataclass
class DocumentInventory:
    """Complete inventory of documents for a tax return."""

    tax_return_id: UUID
    property_address: str

    # Document lists
    provided: List[ProvidedDocument] = field(default_factory=list)
    missing: List[MissingDocument] = field(default_factory=list)
    excluded: List[ExcludedDocument] = field(default_factory=list)

    # Issues
    blocking_issues: List[BlockingIssue] = field(default_factory=list)

    # Summary flags
    has_pm_statement: bool = False
    has_bank_statement: bool = False
    has_loan_statement: bool = False
    has_rates_invoice: bool = False
    has_insurance_policy: bool = False

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "tax_return_id": str(self.tax_return_id),
            "property_address": self.property_address,
            "provided": [
                {
                    "document_id": str(d.document_id),
                    "document_type": d.document_type,
                    "filename": d.filename,
                    "status": d.status.value,
                    "period_start": d.period_start.isoformat() if d.period_start else None,
                    "period_end": d.period_end.isoformat() if d.period_end else None,
                    "extraction_confidence": d.extraction_confidence,
                    "notes": d.notes
                }
                for d in self.provided
            ],
            "missing": [
                {
                    "document_type": d.document_type,
                    "reason": d.reason,
                    "severity": d.severity.value,
                    "detected_from": d.detected_from,
                    "details": d.details
                }
                for d in self.missing
            ],
            "excluded": [
                {
                    "document_id": str(d.document_id) if d.document_id else None,
                    "filename": d.filename,
                    "original_type": d.original_type,
                    "exclusion_reason": d.exclusion_reason.value,
                    "explanation": d.explanation
                }
                for d in self.excluded
            ],
            "blocking_issues": [
                {
                    "issue_type": b.issue_type,
                    "severity": b.severity,
                    "message": b.message,
                    "resolution": b.resolution,
                    "related_document_id": str(b.related_document_id) if b.related_document_id else None
                }
                for b in self.blocking_issues
            ],
            "summary": {
                "provided_count": len(self.provided),
                "missing_count": len(self.missing),
                "excluded_count": len(self.excluded),
                "blocking_issues_count": len(self.blocking_issues),
                "has_pm_statement": self.has_pm_statement,
                "has_bank_statement": self.has_bank_statement,
                "has_loan_statement": self.has_loan_statement,
                "has_rates_invoice": self.has_rates_invoice,
                "has_insurance_policy": self.has_insurance_policy
            }
        }


class DocumentInventoryService:
    """
    Service for building and managing document inventory.

    Tracks what documents have been provided, what's missing,
    and what's been excluded from processing.
    """

    # Required document types for a complete return
    CORE_DOCUMENT_TYPES = [
        "bank_statement",
        "loan_statement",  # If property has mortgage
    ]

    # Expected supporting documents
    SUPPORTING_DOCUMENT_TYPES = [
        "rates",
        "insurance",  # Specifically landlord insurance
        "property_manager_statement",
    ]

    # Document types that indicate property manager managed
    PM_MANAGED_INDICATORS = [
        "property_manager_statement",
    ]

    def __init__(self):
        """Initialize the inventory service."""
        pass

    async def build_inventory(
        self,
        tax_return_id: UUID,
        property_address: str,
        documents: List[Dict[str, Any]],
        db: AsyncSession
    ) -> DocumentInventory:
        """
        Build a complete document inventory from processed documents.

        Args:
            tax_return_id: Tax return ID
            property_address: Property address for relevance checking
            documents: List of processed document data
            db: Database session

        Returns:
            Complete DocumentInventory
        """
        from sqlalchemy import func
        from app.models.db_models import Transaction

        inventory = DocumentInventory(
            tax_return_id=tax_return_id,
            property_address=property_address
        )

        # Process each document
        for doc in documents:
            await self._process_document(doc, inventory, property_address)

        # Update summary flags
        self._update_summary_flags(inventory)

        # Also check database for transactions - if we have extracted transactions,
        # then we have bank transaction data regardless of document classification
        if not inventory.has_bank_statement:
            result = await db.execute(
                select(func.count(Transaction.id)).where(
                    Transaction.tax_return_id == tax_return_id
                )
            )
            transaction_count = result.scalar() or 0
            if transaction_count > 0:
                logger.info(
                    f"Found {transaction_count} transactions in DB for {tax_return_id}, "
                    "marking bank transaction data as available"
                )
                inventory.has_bank_statement = True

        # Check database for interest-categorized transactions
        # If we have transactions categorized as interest, we have loan/interest data
        if not inventory.has_loan_statement:
            result = await db.execute(
                select(func.count(Transaction.id)).where(
                    Transaction.tax_return_id == tax_return_id,
                    Transaction.category_code.in_(["interest", "mortgage_interest", "loan_interest"])
                )
            )
            interest_count = result.scalar() or 0
            if interest_count > 0:
                logger.info(
                    f"Found {interest_count} interest transactions in DB for {tax_return_id}, "
                    "marking loan/interest data as available"
                )
                inventory.has_loan_statement = True

        # Detect missing documents based on what was provided
        self._detect_missing_documents(inventory)

        # Check for blocking issues
        self._check_blocking_issues(inventory)

        logger.info(
            f"Built inventory for {tax_return_id}: "
            f"{len(inventory.provided)} provided, "
            f"{len(inventory.missing)} missing, "
            f"{len(inventory.excluded)} excluded, "
            f"{len(inventory.blocking_issues)} blocking issues"
        )

        return inventory

    async def _process_document(
        self,
        doc: Any,
        inventory: DocumentInventory,
        property_address: str
    ) -> None:
        """
        Process a single document and add to appropriate inventory list.

        Args:
            doc: Document data (SQLAlchemy model or dict)
            inventory: Inventory to update
            property_address: Expected property address
        """
        # Support both dict and SQLAlchemy model objects
        if isinstance(doc, dict):
            doc_type = doc.get("document_type", "unknown")
            doc_id = doc.get("id")
            filename = doc.get("filename", "unknown")
            extracted_data = doc.get("extracted_data", {})
            confidence = doc.get("confidence", 0.0)
        else:
            # SQLAlchemy model object
            doc_type = getattr(doc, "document_type", "unknown") or "unknown"
            doc_id = getattr(doc, "id", None)
            filename = getattr(doc, "filename", "unknown") or "unknown"
            extracted_data = getattr(doc, "extracted_data", {}) or {}
            confidence = getattr(doc, "confidence", 0.0) or 0.0

        # Check if document is relevant
        relevance = self._check_relevance(doc, property_address)

        if not relevance["is_relevant"]:
            # Add to excluded list
            inventory.excluded.append(ExcludedDocument(
                document_id=doc_id,
                filename=filename,
                original_type=doc_type,
                exclusion_reason=relevance["reason"],
                explanation=relevance["explanation"]
            ))
            return

        # Check for wrong document type (e.g., home insurance vs landlord)
        type_check = self._check_document_type(doc)
        if type_check["is_wrong_type"]:
            inventory.excluded.append(ExcludedDocument(
                document_id=doc_id,
                filename=filename,
                original_type=doc_type,
                exclusion_reason=ExclusionReason.WRONG_DOCUMENT_TYPE,
                explanation=type_check["explanation"]
            ))
            # Also add as blocking issue if critical
            if type_check.get("is_blocking"):
                inventory.blocking_issues.append(BlockingIssue(
                    issue_type="wrong_document_type",
                    severity="high",
                    message=type_check["explanation"],
                    resolution=type_check["resolution"],
                    related_document_id=doc_id
                ))
            return

        # Document is relevant - add to provided list
        key_details = extracted_data.get("key_details", {}) if isinstance(extracted_data, dict) else {}

        # Extract period if available
        period_start = None
        period_end = None
        if "statement_period" in key_details:
            period = key_details["statement_period"]
            if isinstance(period, dict):
                period_start = self._parse_date(period.get("start_date"))
                period_end = self._parse_date(period.get("end_date"))

        inventory.provided.append(ProvidedDocument(
            document_id=doc_id,
            document_type=doc_type,
            filename=filename,
            status=DocumentStatus.PROVIDED,
            period_start=period_start,
            period_end=period_end,
            extraction_confidence=confidence,
            key_details=key_details,
            notes=None
        ))

        # Check for bank transaction data while we have access to full extracted_data
        # This sets the flag early, allowing CSVs and other docs with transactions to qualify
        if self._document_has_bank_transactions(doc_type, extracted_data, key_details):
            inventory.has_bank_statement = True

        # Check for loan/interest data - interest breakdown can come from any document
        if self._document_has_loan_interest_data(doc_type, extracted_data, key_details):
            inventory.has_loan_statement = True

    def _document_has_bank_transactions(
        self,
        doc_type: str,
        extracted_data: Dict[str, Any],
        key_details: Dict[str, Any]
    ) -> bool:
        """
        Check if a document contains bank transaction data.

        This is called during document processing while we have access to
        the full extracted_data, not just key_details.
        """
        # Formal bank statement
        if doc_type == "bank_statement":
            return True

        # Check for transactions array at top level of extracted_data
        if isinstance(extracted_data, dict):
            transactions = extracted_data.get("transactions", [])
            if transactions and len(transactions) > 0:
                return True

            # Also check for line_items (common in CSV extractions)
            line_items = extracted_data.get("line_items", [])
            if line_items and len(line_items) > 0:
                return True

        # Check key_details for bank-related indicators
        if key_details:
            # If document has transactions in key_details
            if key_details.get("transactions") and len(key_details.get("transactions", [])) > 0:
                return True

            # Check for bank-related indicators
            bank_indicators = [
                "account_number", "account_name", "bank_name",
                "opening_balance", "closing_balance",
                "total_credits", "total_debits"
            ]
            if any(key_details.get(indicator) for indicator in bank_indicators):
                return True

        return False

    def _document_has_loan_interest_data(
        self,
        doc_type: str,
        extracted_data: Dict[str, Any],
        key_details: Dict[str, Any]
    ) -> bool:
        """
        Check if a document contains loan/interest data.

        The purpose of a loan statement is to identify interest vs principal.
        If this breakdown is visible in any document (bank statement, CSV,
        settlement statement, etc.), a formal loan statement isn't required.
        """
        # Formal loan statement
        if doc_type == "loan_statement":
            return True

        # Check key_details for loan/interest indicators
        if key_details:
            # Direct interest data
            loan_indicators = [
                "interest_amount", "interest_total", "total_interest",
                "interest_paid", "interest_charged",
                "principal_amount", "principal_paid", "principal_repaid",
                "loan_balance", "opening_balance", "closing_balance",
                "loan_account", "mortgage_interest"
            ]
            if any(key_details.get(indicator) for indicator in loan_indicators):
                return True

            # Check for interest breakdown in nested structures
            if key_details.get("interest") and isinstance(key_details.get("interest"), dict):
                return True

            # Check for loan payment breakdown
            if key_details.get("loan_payments") or key_details.get("mortgage_payments"):
                return True

        # Check extracted_data for interest-related transactions
        if isinstance(extracted_data, dict):
            transactions = extracted_data.get("transactions", [])
            if transactions:
                for txn in transactions:
                    if isinstance(txn, dict):
                        desc = str(txn.get("description", "")).lower()
                        category = str(txn.get("category", "")).lower()
                        # Look for interest-related transactions
                        if any(term in desc for term in ["interest", "mortgage int", "loan int"]):
                            return True
                        if category in ["interest", "mortgage_interest", "loan_interest"]:
                            return True

            # Check line_items for interest breakdown
            line_items = extracted_data.get("line_items", [])
            if line_items:
                for item in line_items:
                    if isinstance(item, dict):
                        desc = str(item.get("description", "")).lower()
                        if "interest" in desc and "principal" not in desc:
                            return True

        return False

    def _check_relevance(
        self,
        doc: Any,
        expected_address: str
    ) -> Dict[str, Any]:
        """
        Check if document is relevant to the rental property.

        Args:
            doc: Document data (SQLAlchemy model or dict)
            expected_address: Expected property address

        Returns:
            Dict with is_relevant, reason, explanation
        """
        # Support both dict and SQLAlchemy model objects
        if isinstance(doc, dict):
            doc_type = doc.get("document_type", "")
            extracted_data = doc.get("extracted_data", {})
        else:
            doc_type = getattr(doc, "document_type", "") or ""
            extracted_data = getattr(doc, "extracted_data", {}) or {}

        key_details = extracted_data.get("key_details", {}) if isinstance(extracted_data, dict) else {}

        # Document types that are always not relevant
        irrelevant_types = ["invalid", "unknown", "personal"]
        if doc_type in irrelevant_types:
            return {
                "is_relevant": False,
                "reason": ExclusionReason.NOT_RELEVANT,
                "explanation": f"Document type '{doc_type}' is not relevant to rental property"
            }

        # Check property address if available
        doc_address = key_details.get("property_address", "")
        if doc_address and expected_address:
            if not self._addresses_match(doc_address, expected_address):
                return {
                    "is_relevant": False,
                    "reason": ExclusionReason.WRONG_PROPERTY,
                    "explanation": f"Document is for different property: {doc_address}"
                }

        return {"is_relevant": True, "reason": None, "explanation": None}

    def _check_document_type(self, doc: Any) -> Dict[str, Any]:
        """
        Check if document is the correct type (e.g., landlord vs home insurance).

        Args:
            doc: Document data (SQLAlchemy model or dict)

        Returns:
            Dict with is_wrong_type, explanation, resolution, is_blocking
        """
        # Support both dict and SQLAlchemy model objects
        if isinstance(doc, dict):
            doc_type = doc.get("document_type", "")
            extracted_data = doc.get("extracted_data", {})
        else:
            doc_type = getattr(doc, "document_type", "") or ""
            extracted_data = getattr(doc, "extracted_data", {}) or {}

        key_details = extracted_data.get("key_details", {}) if isinstance(extracted_data, dict) else {}

        # Check insurance type
        if doc_type == "insurance" or doc_type == "landlord_insurance":
            policy_type = key_details.get("policy_type", "").lower()

            # Wrong insurance types
            wrong_types = ["home and contents", "home & contents", "contents", "personal"]
            if any(wrong in policy_type for wrong in wrong_types):
                return {
                    "is_wrong_type": True,
                    "explanation": f"Insurance policy is '{policy_type}' - need landlord insurance policy",
                    "resolution": "Please provide landlord insurance policy (not home & contents)",
                    "is_blocking": True
                }

        return {"is_wrong_type": False}

    def _addresses_match(self, addr1: str, addr2: str) -> bool:
        """
        Check if two addresses match (fuzzy matching).

        Args:
            addr1: First address
            addr2: Second address

        Returns:
            True if addresses likely match
        """
        if not addr1 or not addr2:
            return True  # Can't verify, assume match

        # Normalize addresses
        def normalize(addr: str) -> str:
            addr = addr.lower()
            # Remove common words
            for word in ["street", "st", "road", "rd", "avenue", "ave", "drive", "dr", "new zealand", "nz"]:
                addr = addr.replace(word, "")
            # Remove punctuation and extra spaces
            addr = "".join(c for c in addr if c.isalnum() or c.isspace())
            return " ".join(addr.split())

        norm1 = normalize(addr1)
        norm2 = normalize(addr2)

        # Check if one contains the other (partial match)
        if norm1 in norm2 or norm2 in norm1:
            return True

        # Check word overlap
        words1 = set(norm1.split())
        words2 = set(norm2.split())
        overlap = len(words1 & words2)
        total = len(words1 | words2)

        return overlap / max(total, 1) > 0.5  # More than 50% overlap

    def _update_summary_flags(self, inventory: DocumentInventory) -> None:
        """Update summary flags based on provided documents."""
        for doc in inventory.provided:
            if doc.document_type == "property_manager_statement":
                inventory.has_pm_statement = True
            elif doc.document_type == "loan_statement":
                inventory.has_loan_statement = True
            elif doc.document_type in ["rates", "rates_invoice"]:
                inventory.has_rates_invoice = True
            elif doc.document_type in ["insurance", "landlord_insurance"]:
                inventory.has_insurance_policy = True


    def _detect_missing_documents(self, inventory: DocumentInventory) -> None:
        """
        Detect missing documents based on what was provided.

        Uses logic like: if bank statement shows loan payments but no loan statement...
        """
        # Check for core missing documents - bank transaction data (statement or CSV export)
        if not inventory.has_bank_statement:
            inventory.missing.append(MissingDocument(
                document_type="bank_transaction_data",
                reason="No bank transaction data provided",
                severity=MissingSeverity.REQUIRED,
                detected_from="phase1_inventory",
                details="Bank transaction data (statement or CSV export) is required to identify rental income and expenses"
            ))

        # Note: Loan statement detection is more intelligent - done in Phase 2
        # when we can see if bank shows loan payments

        # Check for supporting documents
        if not inventory.has_rates_invoice:
            inventory.missing.append(MissingDocument(
                document_type="rates",
                reason="No rates invoice/notice provided",
                severity=MissingSeverity.RECOMMENDED,
                detected_from="phase1_inventory",
                details="Rates invoices help verify council rates payments"
            ))

        if not inventory.has_insurance_policy:
            inventory.missing.append(MissingDocument(
                document_type="insurance",
                reason="No landlord insurance policy provided",
                severity=MissingSeverity.RECOMMENDED,
                detected_from="phase1_inventory",
                details="Insurance policy needed to verify landlord insurance (not home & contents)"
            ))

    def _check_blocking_issues(self, inventory: DocumentInventory) -> None:
        """Check for blocking issues that prevent return completion."""
        # No bank transaction data is blocking
        if not inventory.has_bank_statement:
            # Check if already added
            if not any(b.issue_type == "missing_bank_transaction_data" for b in inventory.blocking_issues):
                inventory.blocking_issues.append(BlockingIssue(
                    issue_type="missing_bank_transaction_data",
                    severity="high",
                    message="No bank transaction data provided - cannot identify income and expenses",
                    resolution="Please provide bank statements or CSV transaction export for the full tax year"
                ))

    def _parse_date(self, date_str: Optional[str]) -> Optional[date]:
        """Parse date string to date object."""
        if not date_str:
            return None

        from datetime import datetime

        formats = [
            "%Y-%m-%d",
            "%d/%m/%Y",
            "%d-%m-%Y",
            "%d %B %Y",
            "%d %b %Y",
        ]

        for fmt in formats:
            try:
                return datetime.strptime(date_str, fmt).date()
            except ValueError:
                continue

        return None

    def add_missing_from_analysis(
        self,
        inventory: DocumentInventory,
        document_type: str,
        reason: str,
        severity: MissingSeverity,
        detected_from: str,
        details: Optional[str] = None
    ) -> None:
        """
        Add a missing document detected during Phase 2 analysis.

        Args:
            inventory: Inventory to update
            document_type: Type of missing document
            reason: Why it's needed
            severity: How critical it is
            detected_from: What analysis detected this
            details: Additional details
        """
        # Check if already in missing list
        if any(m.document_type == document_type for m in inventory.missing):
            return

        inventory.missing.append(MissingDocument(
            document_type=document_type,
            reason=reason,
            severity=severity,
            detected_from=detected_from,
            details=details
        ))


# Singleton instance
_inventory_service: Optional[DocumentInventoryService] = None


def get_inventory_service() -> DocumentInventoryService:
    """Get or create singleton inventory service."""
    global _inventory_service

    if _inventory_service is None:
        _inventory_service = DocumentInventoryService()

    return _inventory_service
