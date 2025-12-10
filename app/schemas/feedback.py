"""Feedback schemas for the learning system."""
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class FeedbackCreate(BaseModel):
    """Schema for submitting feedback."""
    content: str = Field(..., min_length=10, description="The correction or guidance")
    scenario: str = Field(..., min_length=3, description="Brief scenario name")
    category: str = Field(
        default="general_guidance",
        description="Category: document_classification, document_validation, expense_classification, blocking_rules, general_guidance"
    )
    tax_return_id: Optional[str] = Field(None, description="Related tax return ID if applicable")
    document_id: Optional[str] = Field(None, description="Related document ID if applicable")


class FeedbackResponse(BaseModel):
    """Response after storing feedback."""
    id: str
    content: str
    scenario: str
    category: str
    stored_at: datetime
    message: str = "Feedback stored successfully"


class LearningItem(BaseModel):
    """A single learning from the knowledge base."""
    id: str
    content: str
    scenario: str
    category: str
    score: float = 0.0
    created_at: Optional[str] = None


class LearningsListResponse(BaseModel):
    """Response for listing learnings."""
    total: int
    learnings: list[LearningItem]