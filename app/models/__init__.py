"""Database models package."""
from app.models.db_models import (
    # Phase 1: Document Intake
    Client,
    Document,
    TaxReturn,
    PropertyType,
    TaxReturnStatus,
    DocumentStatus,
    # Phase 2: Transaction Processing & Learning
    TaxRule,
    PLRowMapping,
    Transaction,
    TransactionSummary,
    TransactionPattern,
    CategoryFeedback,
    TransactionType,
    # Skill Learning System
    SkillLearning,
    LearningType,
    AppliesTo,
)

__all__ = [
    # Phase 1: Document Intake
    "Client",
    "Document",
    "TaxReturn",
    "PropertyType",
    "TaxReturnStatus",
    "DocumentStatus",
    # Phase 2: Transaction Processing & Learning
    "TaxRule",
    "PLRowMapping",
    "Transaction",
    "TransactionSummary",
    "TransactionPattern",
    "CategoryFeedback",
    "TransactionType",
    # Skill Learning System
    "SkillLearning",
    "LearningType",
    "AppliesTo",
]
