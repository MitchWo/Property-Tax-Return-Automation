"""API routes for skill learning management."""

import logging
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import AppliesTo, LearningType, SkillLearning
from app.services.phase2_feedback_learning.skill_learning_service import (
    SkillLearningService,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/skill-learnings", tags=["skill-learnings"])


# Pydantic models for API
class CreateLearningRequest(BaseModel):
    """Request model for creating a learning."""

    skill_name: str = Field(default="nz_rental_returns")
    learning_type: str = Field(..., description="teaching, correction, pattern, or edge_case")
    title: str = Field(..., max_length=200)
    content: str = Field(...)
    keywords: Optional[List[str]] = Field(default=None)
    applies_to: str = Field(default="transaction", description="transaction, document, or both")
    client_id: Optional[UUID] = None
    category_code: Optional[str] = None
    created_by: Optional[str] = Field(default="api")


class LearningResponse(BaseModel):
    """Response model for a learning."""

    id: UUID
    skill_name: str
    learning_type: str
    title: str
    content: str
    keywords: Optional[List[str]]
    applies_to: str
    client_id: Optional[UUID]
    category_code: Optional[str]
    confidence: float
    times_applied: int
    times_confirmed: int
    is_active: bool
    created_at: str
    created_by: Optional[str]


class SearchLearningsRequest(BaseModel):
    """Request model for searching learnings."""

    query: str = Field(..., description="Search query text")
    skill_name: str = Field(default="nz_rental_returns")
    learning_types: Optional[List[str]] = None
    client_id: Optional[UUID] = None
    limit: int = Field(default=5, le=20)
    min_confidence: float = Field(default=0.5, ge=0.0, le=1.0)


class SuggestionResponse(BaseModel):
    """Response model for categorization suggestions."""

    category_code: str
    confidence: float
    sources: List[str]
    reasoning: List[str]


class CorrectionRequest(BaseModel):
    """Request model for learning from correction."""

    transaction_id: UUID
    original_category: Optional[str]
    corrected_category: str
    corrected_by: Optional[str] = Field(default="user")
    notes: Optional[str] = None


class EdgeCaseRequest(BaseModel):
    """Request model for recording edge case."""

    transaction_id: UUID
    issue_description: str
    resolution: Optional[str] = None
    category_code: Optional[str] = None


@router.post("/", response_model=LearningResponse)
async def create_learning(
    request: CreateLearningRequest,
    db: AsyncSession = Depends(get_db)
) -> LearningResponse:
    """Create a new skill learning entry."""
    try:
        service = SkillLearningService(db)

        # Convert string enums
        learning_type = LearningType(request.learning_type)
        applies_to = AppliesTo(request.applies_to)

        learning = await service.create_learning(
            skill_name=request.skill_name,
            learning_type=learning_type,
            title=request.title,
            content=request.content,
            keywords=request.keywords,
            applies_to=applies_to,
            client_id=request.client_id,
            category_code=request.category_code,
            created_by=request.created_by
        )

        return LearningResponse(
            id=learning.id,
            skill_name=learning.skill_name,
            learning_type=learning.learning_type,
            title=learning.title,
            content=learning.content,
            keywords=learning.keywords if isinstance(learning.keywords, list) else None,
            applies_to=learning.applies_to,
            client_id=learning.client_id,
            category_code=learning.category_code,
            confidence=learning.confidence,
            times_applied=learning.times_applied,
            times_confirmed=learning.times_confirmed,
            is_active=learning.is_active,
            created_at=learning.created_at.isoformat(),
            created_by=learning.created_by
        )

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to create learning: {e}")
        raise HTTPException(status_code=500, detail="Failed to create learning")


@router.post("/search")
async def search_learnings(
    request: SearchLearningsRequest,
    db: AsyncSession = Depends(get_db)
) -> List[dict]:
    """Search for relevant learnings using semantic search."""
    try:
        service = SkillLearningService(db)

        # Convert learning types if provided
        learning_types = None
        if request.learning_types:
            learning_types = [LearningType(lt) for lt in request.learning_types]

        learnings = await service.get_relevant_learnings(
            query=request.query,
            skill_name=request.skill_name,
            learning_types=learning_types,
            client_id=request.client_id,
            limit=request.limit,
            min_confidence=request.min_confidence
        )

        return learnings

    except Exception as e:
        logger.error(f"Failed to search learnings: {e}")
        raise HTTPException(status_code=500, detail="Failed to search learnings")


@router.get("/", response_model=List[LearningResponse])
async def list_learnings(
    skill_name: str = Query(default="nz_rental_returns"),
    learning_type: Optional[str] = None,
    client_id: Optional[UUID] = None,
    category_code: Optional[str] = None,
    min_confidence: float = Query(default=0.0, ge=0.0, le=1.0),
    limit: int = Query(default=50, le=100),
    db: AsyncSession = Depends(get_db)
) -> List[LearningResponse]:
    """List learnings with filters."""
    try:
        service = SkillLearningService(db)

        # Convert learning type if provided
        lt = LearningType(learning_type) if learning_type else None

        learnings = await service.search_learnings(
            skill_name=skill_name,
            learning_type=lt,
            client_id=client_id,
            category_code=category_code,
            min_confidence=min_confidence,
            limit=limit
        )

        return [
            LearningResponse(
                id=learning.id,
                skill_name=learning.skill_name,
                learning_type=learning.learning_type,
                title=learning.title,
                content=learning.content,
                keywords=learning.keywords if isinstance(learning.keywords, list) else None,
                applies_to=learning.applies_to,
                client_id=learning.client_id,
                category_code=learning.category_code,
                confidence=learning.confidence,
                times_applied=learning.times_applied,
                times_confirmed=learning.times_confirmed,
                is_active=learning.is_active,
                created_at=learning.created_at.isoformat(),
                created_by=learning.created_by
            )
            for learning in learnings
        ]

    except Exception as e:
        logger.error(f"Failed to list learnings: {e}")
        raise HTTPException(status_code=500, detail="Failed to list learnings")


@router.post("/corrections", response_model=LearningResponse)
async def learn_from_correction(
    request: CorrectionRequest,
    db: AsyncSession = Depends(get_db)
) -> LearningResponse:
    """Learn from a category correction."""
    try:
        service = SkillLearningService(db)

        learning = await service.learn_from_correction(
            transaction_id=request.transaction_id,
            original_category=request.original_category,
            corrected_category=request.corrected_category,
            corrected_by=request.corrected_by,
            notes=request.notes
        )

        return LearningResponse(
            id=learning.id,
            skill_name=learning.skill_name,
            learning_type=learning.learning_type,
            title=learning.title,
            content=learning.content,
            keywords=learning.keywords if isinstance(learning.keywords, list) else None,
            applies_to=learning.applies_to,
            client_id=learning.client_id,
            category_code=learning.category_code,
            confidence=learning.confidence,
            times_applied=learning.times_applied,
            times_confirmed=learning.times_confirmed,
            is_active=learning.is_active,
            created_at=learning.created_at.isoformat(),
            created_by=learning.created_by
        )

    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to learn from correction: {e}")
        raise HTTPException(status_code=500, detail="Failed to learn from correction")


@router.post("/suggestions")
async def get_categorization_suggestions(
    description: str = Query(...),
    other_party: Optional[str] = None,
    amount: Optional[float] = None,
    client_id: Optional[UUID] = None,
    top_k: int = Query(default=3, le=10),
    db: AsyncSession = Depends(get_db)
) -> List[SuggestionResponse]:
    """Get categorization suggestions based on learnings."""
    try:
        from app.services.phase2_feedback_learning.rag_categorization import RAGCategorizer

        categorizer = RAGCategorizer(db)

        suggestions = await categorizer.get_categorization_suggestions(
            description=description,
            other_party=other_party,
            amount=amount,
            client_id=client_id,
            top_k=top_k
        )

        return [
            SuggestionResponse(
                category_code=s['category_code'],
                confidence=s['confidence'],
                sources=s['sources'],
                reasoning=s['reasoning']
            )
            for s in suggestions
        ]

    except Exception as e:
        logger.error(f"Failed to get suggestions: {e}")
        raise HTTPException(status_code=500, detail="Failed to get suggestions")


@router.post("/edge-cases")
async def record_edge_case(
    request: EdgeCaseRequest,
    db: AsyncSession = Depends(get_db)
) -> dict:
    """Record an edge case for future reference."""
    try:
        from app.models import Transaction
        from app.services.phase2_feedback_learning.rag_categorization import RAGCategorizer

        # Get the transaction
        transaction = await db.get(Transaction, request.transaction_id)
        if not transaction:
            raise HTTPException(status_code=404, detail="Transaction not found")

        categorizer = RAGCategorizer(db)

        await categorizer.record_edge_case(
            transaction=transaction,
            issue_description=request.issue_description,
            resolution=request.resolution,
            category_code=request.category_code
        )

        return {"message": "Edge case recorded successfully"}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to record edge case: {e}")
        raise HTTPException(status_code=500, detail="Failed to record edge case")


@router.put("/{learning_id}/apply")
async def track_learning_application(
    learning_id: UUID,
    was_successful: bool = Query(default=True),
    db: AsyncSession = Depends(get_db)
) -> dict:
    """Track when a learning is applied."""
    try:
        service = SkillLearningService(db)

        await service.apply_learning(
            learning_id=learning_id,
            was_successful=was_successful
        )

        return {"message": "Learning application tracked successfully"}

    except Exception as e:
        logger.error(f"Failed to track application: {e}")
        raise HTTPException(status_code=500, detail="Failed to track application")


@router.put("/{learning_id}/deactivate")
async def deactivate_learning(
    learning_id: UUID,
    db: AsyncSession = Depends(get_db)
) -> dict:
    """Deactivate a learning."""
    try:
        learning = await db.get(SkillLearning, learning_id)
        if not learning:
            raise HTTPException(status_code=404, detail="Learning not found")

        learning.is_active = False
        await db.commit()

        return {"message": "Learning deactivated successfully"}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to deactivate learning: {e}")
        await db.rollback()
        raise HTTPException(status_code=500, detail="Failed to deactivate learning")


@router.post("/initialize-teachings")
async def initialize_teachings(
    db: AsyncSession = Depends(get_db)
) -> dict:
    """Initialize the system with base NZ tax teachings."""
    try:
        from app.services.phase2_feedback_learning.rag_categorization import (
            create_initial_teachings,
        )

        service = SkillLearningService(db)

        await create_initial_teachings(db, service)

        return {"message": "Initial teachings created successfully"}

    except Exception as e:
        logger.error(f"Failed to initialize teachings: {e}")
        raise HTTPException(status_code=500, detail="Failed to initialize teachings")