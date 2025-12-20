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
        description="Category: document_classification, document_validation, expense_classification, blocking_rules, general_guidance",
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


class TransactionFeedbackCreate(BaseModel):
    """Schema for submitting transaction feedback."""

    transaction_description: str = Field(
        ..., min_length=1, description="Description of the transaction"
    )
    amount: float = Field(..., description="Transaction amount")
    document_name: str = Field(..., description="Name of the document containing the transaction")
    resolution: str = Field(
        ..., description="Resolution type: legitimate, personal, requires_invoice"
    )
    vendor_name: Optional[str] = Field(None, description="Identified vendor name")
    expense_category: Optional[str] = Field(
        None, description="Expense category for the transaction"
    )
    notes: Optional[str] = Field(None, description="Additional notes")
    tax_return_id: Optional[str] = Field(None, description="Related tax return ID")


class TransactionFeedbackResponse(BaseModel):
    """Response after storing transaction feedback."""

    id: str
    transaction_description: str
    resolution: str
    stored_at: datetime
    message: str = "Transaction feedback stored successfully"
    status_updated: bool = False
    new_status: Optional[str] = None


class BlockingIssueResolution(BaseModel):
    """Schema for resolving a blocking issue."""

    tax_return_id: str = Field(..., description="Tax return ID")
    issue_text: str = Field(..., description="The blocking issue text to resolve")
    resolution_type: str = Field(
        ..., description="Resolution type: 'ok' (mark as resolved) or 'feedback' (provide learning)"
    )
    feedback_content: Optional[str] = Field(
        None, description="Feedback content explaining why this shouldn't be blocking (required if resolution_type is 'feedback')"
    )


class BlockingIssueResolutionResponse(BaseModel):
    """Response after resolving a blocking issue."""

    issue_text: str
    resolution_type: str
    resolved: bool
    learning_id: Optional[str] = None
    status_updated: bool = False
    new_status: Optional[str] = None
    message: str
