"""Phase 2 AI Brain - Accountant workflow for tax return processing."""

from app.services.phase2_ai_brain.brain import AIBrain, get_ai_brain
from app.services.phase2_ai_brain.workings_models import (
    TaxReturnWorkingsData,
    WorkingsSummary,
    IncomeWorkings,
    ExpenseWorkings,
    WorkingsFlag as WorkingsFlagData,
    DocumentRequestData,
    ClientQuestionData,
)

__all__ = [
    "AIBrain",
    "get_ai_brain",
    "TaxReturnWorkingsData",
    "WorkingsSummary",
    "IncomeWorkings",
    "ExpenseWorkings",
    "WorkingsFlagData",
    "DocumentRequestData",
    "ClientQuestionData",
]
