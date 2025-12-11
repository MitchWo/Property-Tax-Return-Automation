"""Database models package."""
from app.models.db_models import (
    # Phase 1: Document Intake
    Client,
    Document,
    TaxReturn,
    PropertyType,
    TaxReturnStatus,
    DocumentStatus,
    # Phase 3: Transaction Processing
    TaxRule,
    PLRowMapping,
    Transaction,
    TransactionSummary,
    TransactionPattern,
    CategoryFeedback,
    TransactionType,
)

__all__ = [
    # Phase 1: Document Intake
    "Client",
    "Document",
    "TaxReturn",
    "PropertyType",
    "TaxReturnStatus",
    "DocumentStatus",
    # Phase 3: Transaction Processing
    "TaxRule",
    "PLRowMapping",
    "Transaction",
    "TransactionSummary",
    "TransactionPattern",
    "CategoryFeedback",
    "TransactionType",
]