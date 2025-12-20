"""API routes for AI Brain workings."""

import logging
from datetime import datetime
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.db_models import (
    TaxReturn,
    TaxReturnWorkings,
    WorkingsFlag,
    DocumentRequest,
    ClientQuestion,
    DocumentInventoryRecord,
    WorkingsStatus,
    FlagStatus,
    RequestStatus,
    QuestionStatus,
)
from app.services.phase2_ai_brain import get_ai_brain

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/workings", tags=["workings"])


# ================== Request/Response Models ==================

class ProcessWorkingsRequest(BaseModel):
    """Request to process/generate workings."""
    force_reprocess: bool = False
    process_transactions: bool = True  # Process/categorize transactions before generating workings


class WorkingsSummaryResponse(BaseModel):
    """Summary response for workings."""
    id: UUID
    tax_return_id: UUID
    version: int
    status: str
    total_income: float
    total_expenses: float
    total_deductions: float
    net_rental_income: float
    interest_gross: Optional[float]
    interest_deductible_percentage: Optional[float]
    interest_deductible_amount: Optional[float]
    flags_count: int
    document_requests_count: int
    client_questions_count: int
    created_at: datetime
    updated_at: datetime


class FlagResponse(BaseModel):
    """Response for a single flag."""
    id: UUID
    severity: str
    category: str
    message: str
    action_required: Optional[str]
    status: str
    resolved_by: Optional[str]
    resolved_at: Optional[datetime]
    resolution_notes: Optional[str]


class ResolveFlagRequest(BaseModel):
    """Request to resolve a flag."""
    resolution_notes: Optional[str] = None
    status: str = "resolved"  # resolved, ignored


class DocumentRequestResponse(BaseModel):
    """Response for a document request."""
    id: UUID
    document_type: str
    reason: str
    priority: str
    status: str
    sent_at: Optional[datetime]
    received_at: Optional[datetime]


class ClientQuestionResponse(BaseModel):
    """Response for a client question."""
    id: UUID
    question: str
    context: Optional[str]
    options: Optional[List[str]]
    related_amount: Optional[float]
    status: str
    answer: Optional[str]


class AnswerQuestionRequest(BaseModel):
    """Request to answer a client question."""
    answer: str
    answer_option_index: Optional[int] = None


class DocumentInventoryResponse(BaseModel):
    """Response for document inventory."""
    tax_return_id: UUID
    provided_count: int
    missing_count: int
    excluded_count: int
    blocking_issues_count: int
    has_pm_statement: bool
    has_bank_statement: bool
    has_loan_statement: bool
    has_rates_invoice: bool
    has_insurance_policy: bool
    inventory_data: dict


# ================== Workings Endpoints ==================

@router.post("/{tax_return_id}/process", response_model=WorkingsSummaryResponse)
async def process_workings(
    tax_return_id: UUID,
    request: ProcessWorkingsRequest = ProcessWorkingsRequest(),
    db: AsyncSession = Depends(get_db)
):
    """
    Process a tax return and generate workings using AI Brain.

    This triggers the accountant workflow:
    1. Process/categorize transactions (if process_transactions=True)
    2. Review PM Statements
    3. Review Bank Statements
    4. Review Loan Statements
    5. Review Invoices
    6. Generate workings with flags and requests
    """
    # Verify tax return exists
    result = await db.execute(
        select(TaxReturn).where(TaxReturn.id == tax_return_id)
    )
    tax_return = result.scalar_one_or_none()
    if not tax_return:
        raise HTTPException(status_code=404, detail="Tax return not found")

    # Optionally process transactions first
    if request.process_transactions:
        from app.services.transaction_processor import TransactionProcessor
        processor = TransactionProcessor()
        logger.info(f"Processing transactions before workings generation for tax return {tax_return_id}")
        try:
            await processor.process_tax_return_transactions(
                db=db,
                tax_return_id=tax_return_id,
                use_claude=True
            )
        except Exception as e:
            logger.warning(f"Transaction processing warning (continuing with workings): {e}")

    # Process with AI Brain
    ai_brain = get_ai_brain()
    try:
        await ai_brain.process_tax_return(
            tax_return_id=tax_return_id,
            db=db,
            force_reprocess=request.force_reprocess
        )
    except Exception as e:
        logger.error(f"AI Brain processing failed: {e}")
        raise HTTPException(status_code=500, detail=f"Processing failed: {str(e)}")

    # Get the saved workings record (latest version)
    result = await db.execute(
        select(TaxReturnWorkings).where(
            TaxReturnWorkings.tax_return_id == tax_return_id
        ).order_by(TaxReturnWorkings.version.desc()).limit(1)
    )
    workings = result.scalar_one_or_none()

    # Count related items
    flags_count = await _count_flags(workings.id, db)
    requests_count = await _count_document_requests(tax_return_id, db)
    questions_count = await _count_client_questions(tax_return_id, db)

    return WorkingsSummaryResponse(
        id=workings.id,
        tax_return_id=workings.tax_return_id,
        version=workings.version,
        status=workings.status.value,
        total_income=float(workings.total_income or 0),
        total_expenses=float(workings.total_expenses or 0),
        total_deductions=float(workings.total_deductions or 0),
        net_rental_income=float(workings.net_rental_income or 0),
        interest_gross=float(workings.interest_gross) if workings.interest_gross else None,
        interest_deductible_percentage=workings.interest_deductible_percentage,
        interest_deductible_amount=float(workings.interest_deductible_amount) if workings.interest_deductible_amount else None,
        flags_count=flags_count,
        document_requests_count=requests_count,
        client_questions_count=questions_count,
        created_at=workings.created_at,
        updated_at=workings.updated_at
    )


@router.get("/{tax_return_id}", response_model=dict)
async def get_workings(
    tax_return_id: UUID,
    version: Optional[int] = None,
    db: AsyncSession = Depends(get_db)
):
    """
    Get workings for a tax return.

    Returns the full workings data including income, expenses, flags, etc.
    """
    query = select(TaxReturnWorkings).where(
        TaxReturnWorkings.tax_return_id == tax_return_id
    )

    if version:
        query = query.where(TaxReturnWorkings.version == version)
    else:
        query = query.order_by(TaxReturnWorkings.version.desc()).limit(1)

    result = await db.execute(query)
    workings = result.scalar_one_or_none()

    if not workings:
        raise HTTPException(status_code=404, detail="Workings not found")

    # Get related items
    flags = await _get_flags(workings.id, db)
    requests = await _get_document_requests(tax_return_id, db)
    questions = await _get_client_questions(tax_return_id, db)

    return {
        "id": str(workings.id),
        "tax_return_id": str(workings.tax_return_id),
        "version": workings.version,
        "status": workings.status.value,
        "summary": {
            "total_income": float(workings.total_income or 0),
            "total_expenses": float(workings.total_expenses or 0),
            "total_deductions": float(workings.total_deductions or 0),
            "net_rental_income": float(workings.net_rental_income or 0),
            "interest_gross": float(workings.interest_gross) if workings.interest_gross else None,
            "interest_deductible_percentage": workings.interest_deductible_percentage,
            "interest_deductible_amount": float(workings.interest_deductible_amount) if workings.interest_deductible_amount else None
        },
        "income_workings": workings.income_workings,
        "expense_workings": workings.expense_workings,
        "document_inventory": workings.document_inventory,
        "processing_notes": workings.processing_notes,
        "flags": [
            {
                "id": str(f.id),
                "severity": f.severity.value,
                "category": f.category.value,
                "message": f.message,
                "action_required": f.action_required,
                "status": f.status.value
            }
            for f in flags
        ],
        "document_requests": [
            {
                "id": str(r.id),
                "document_type": r.document_type,
                "reason": r.reason,
                "priority": r.priority,
                "status": r.status.value
            }
            for r in requests
        ],
        "client_questions": [
            {
                "id": str(q.id),
                "question": q.question,
                "context": q.context,
                "options": q.options,
                "status": q.status.value,
                "answer": q.answer
            }
            for q in questions
        ],
        "ai_model_used": workings.ai_model_used,
        "processing_time_seconds": workings.processing_time_seconds,
        "created_at": workings.created_at.isoformat(),
        "updated_at": workings.updated_at.isoformat()
    }


@router.put("/{tax_return_id}/status")
async def update_workings_status(
    tax_return_id: UUID,
    status: str = Query(..., description="New status: draft, in_review, approved, submitted"),
    db: AsyncSession = Depends(get_db)
):
    """Update workings status."""
    result = await db.execute(
        select(TaxReturnWorkings).where(
            TaxReturnWorkings.tax_return_id == tax_return_id
        ).order_by(TaxReturnWorkings.version.desc()).limit(1)
    )
    workings = result.scalar_one_or_none()

    if not workings:
        raise HTTPException(status_code=404, detail="Workings not found")

    try:
        workings.status = WorkingsStatus(status)
        if status == "approved":
            workings.approved_at = datetime.now()
        await db.commit()
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid status: {status}")

    return {"status": "updated", "new_status": status}


# ================== Flags Endpoints ==================

@router.get("/{tax_return_id}/flags", response_model=List[FlagResponse])
async def get_flags(
    tax_return_id: UUID,
    status: Optional[str] = None,
    db: AsyncSession = Depends(get_db)
):
    """Get all flags for a tax return's workings."""
    # Get workings (latest version)
    result = await db.execute(
        select(TaxReturnWorkings).where(
            TaxReturnWorkings.tax_return_id == tax_return_id
        ).order_by(TaxReturnWorkings.version.desc()).limit(1)
    )
    workings = result.scalar_one_or_none()

    if not workings:
        raise HTTPException(status_code=404, detail="Workings not found")

    flags = await _get_flags(workings.id, db, status)

    return [
        FlagResponse(
            id=f.id,
            severity=f.severity.value,
            category=f.category.value,
            message=f.message,
            action_required=f.action_required,
            status=f.status.value,
            resolved_by=f.resolved_by,
            resolved_at=f.resolved_at,
            resolution_notes=f.resolution_notes
        )
        for f in flags
    ]


@router.put("/flags/{flag_id}/resolve")
async def resolve_flag(
    flag_id: UUID,
    request: ResolveFlagRequest,
    db: AsyncSession = Depends(get_db)
):
    """Resolve a flag."""
    result = await db.execute(
        select(WorkingsFlag).where(WorkingsFlag.id == flag_id)
    )
    flag = result.scalar_one_or_none()

    if not flag:
        raise HTTPException(status_code=404, detail="Flag not found")

    try:
        flag.status = FlagStatus(request.status)
        flag.resolved_at = datetime.now()
        flag.resolution_notes = request.resolution_notes
        await db.commit()
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid status: {request.status}")

    return {"status": "resolved", "flag_id": str(flag_id)}


# ================== Document Requests Endpoints ==================

@router.get("/{tax_return_id}/requests", response_model=List[DocumentRequestResponse])
async def get_document_requests(
    tax_return_id: UUID,
    status: Optional[str] = None,
    db: AsyncSession = Depends(get_db)
):
    """Get all document requests for a tax return."""
    requests = await _get_document_requests(tax_return_id, db, status)

    return [
        DocumentRequestResponse(
            id=r.id,
            document_type=r.document_type,
            reason=r.reason,
            priority=r.priority,
            status=r.status.value,
            sent_at=r.sent_at,
            received_at=r.received_at
        )
        for r in requests
    ]


@router.put("/requests/{request_id}/status")
async def update_request_status(
    request_id: UUID,
    status: str = Query(..., description="New status: pending, sent, received, cancelled"),
    db: AsyncSession = Depends(get_db)
):
    """Update document request status."""
    result = await db.execute(
        select(DocumentRequest).where(DocumentRequest.id == request_id)
    )
    req = result.scalar_one_or_none()

    if not req:
        raise HTTPException(status_code=404, detail="Document request not found")

    try:
        req.status = RequestStatus(status)
        if status == "sent":
            req.sent_at = datetime.now()
        elif status == "received":
            req.received_at = datetime.now()
        await db.commit()
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid status: {status}")

    return {"status": "updated", "request_id": str(request_id)}


# ================== Client Questions Endpoints ==================

@router.get("/{tax_return_id}/questions", response_model=List[ClientQuestionResponse])
async def get_client_questions(
    tax_return_id: UUID,
    status: Optional[str] = None,
    db: AsyncSession = Depends(get_db)
):
    """Get all client questions for a tax return."""
    questions = await _get_client_questions(tax_return_id, db, status)

    return [
        ClientQuestionResponse(
            id=q.id,
            question=q.question,
            context=q.context,
            options=q.options,
            related_amount=float(q.related_amount) if q.related_amount else None,
            status=q.status.value,
            answer=q.answer
        )
        for q in questions
    ]


@router.put("/questions/{question_id}/answer")
async def answer_question(
    question_id: UUID,
    request: AnswerQuestionRequest,
    db: AsyncSession = Depends(get_db)
):
    """Record answer to a client question."""
    result = await db.execute(
        select(ClientQuestion).where(ClientQuestion.id == question_id)
    )
    question = result.scalar_one_or_none()

    if not question:
        raise HTTPException(status_code=404, detail="Question not found")

    question.answer = request.answer
    question.answer_option_index = request.answer_option_index
    question.status = QuestionStatus.ANSWERED
    question.answered_at = datetime.now()
    await db.commit()

    return {"status": "answered", "question_id": str(question_id)}


# ================== Document Inventory Endpoints ==================

@router.get("/{tax_return_id}/inventory", response_model=DocumentInventoryResponse)
async def get_document_inventory(
    tax_return_id: UUID,
    db: AsyncSession = Depends(get_db)
):
    """Get document inventory for a tax return."""
    result = await db.execute(
        select(DocumentInventoryRecord).where(
            DocumentInventoryRecord.tax_return_id == tax_return_id
        )
    )
    inventory = result.scalar_one_or_none()

    if not inventory:
        raise HTTPException(status_code=404, detail="Inventory not found")

    return DocumentInventoryResponse(
        tax_return_id=inventory.tax_return_id,
        provided_count=inventory.provided_count,
        missing_count=inventory.missing_count,
        excluded_count=inventory.excluded_count,
        blocking_issues_count=inventory.blocking_issues_count,
        has_pm_statement=inventory.has_pm_statement,
        has_bank_statement=inventory.has_bank_statement,
        has_loan_statement=inventory.has_loan_statement,
        has_rates_invoice=inventory.has_rates_invoice,
        has_insurance_policy=inventory.has_insurance_policy,
        inventory_data=inventory.inventory_data
    )


# ================== Feedback & Learnings Endpoints ==================

class CalculationConfirmRequest(BaseModel):
    """Request to confirm a calculation is correct."""
    category: str  # 'income' or 'expense'
    item_key: str  # e.g., 'rent', 'interest', 'rates'
    item_name: str  # Display name for context
    confirmed_value: float  # The confirmed value


class CalculationConfirmResponse(BaseModel):
    """Response from confirmation."""
    success: bool
    message: str
    learning_id: Optional[str] = None


class CalculationFeedbackRequest(BaseModel):
    """Request to submit calculation feedback."""
    category: str  # 'income' or 'expense'
    item_key: str  # e.g., 'rent', 'interest', 'rates'
    item_name: str  # Display name for context
    feedback_type: str  # 'correction' or 'teaching'
    content: str  # The feedback content
    expected_value: Optional[float] = None  # For corrections
    recalculate_mode: str = "item"  # 'none', 'item', or 'full'


class CalculationFeedbackResponse(BaseModel):
    """Response from feedback submission."""
    success: bool
    message: str
    learning_id: Optional[str] = None
    recalculated: bool = False


class LearningItem(BaseModel):
    """A single learning item."""
    id: str
    learning_type: str
    title: str
    content: str
    created_at: str
    category_code: Optional[str] = None


class LearningsListResponse(BaseModel):
    """Response with list of learnings."""
    learnings: List[LearningItem]
    total_count: int


@router.post("/{tax_return_id}/confirm", response_model=CalculationConfirmResponse)
async def confirm_calculation(
    tax_return_id: UUID,
    request: CalculationConfirmRequest,
    db: AsyncSession = Depends(get_db)
):
    """Confirm a calculation is correct and save as a positive learning."""
    try:
        # Import skill learning service
        from app.services.phase2_feedback_learning.skill_learning_service import (
            SkillLearningService,
            LearningType,
            AppliesTo
        )

        # Get tax return for context
        result = await db.execute(
            select(TaxReturn).where(TaxReturn.id == tax_return_id)
        )
        tax_return = result.scalar_one_or_none()
        if not tax_return:
            raise HTTPException(status_code=404, detail="Tax return not found")

        # Initialize skill learning service
        skill_service = SkillLearningService(db)

        # Build title and content for the confirmation learning
        title = f"Confirmed: {request.item_name} ({request.category})"
        content = f"""
Calculation confirmed as correct:
- Category: {request.category}
- Item: {request.item_name}
- Confirmed Value: ${request.confirmed_value:,.2f}

This calculation method has been verified by user review.
"""

        # Extract keywords for better retrieval
        keywords = [
            request.item_key,
            request.category,
            request.item_name.lower(),
            'calculation',
            'confirmed',
            'verified'
        ]

        # Create the learning as a pattern (confirmed patterns)
        learning = await skill_service.create_learning(
            skill_name='nz_rental_returns',
            learning_type=LearningType.PATTERN,
            title=title,
            content=content,
            keywords=keywords,
            applies_to=AppliesTo.CALCULATION,
            client_id=tax_return.client_id,
            category_code=request.item_key,
            created_by='user_confirmation'
        )

        learning_id = str(learning.id) if learning else None
        await db.commit()

        return CalculationConfirmResponse(
            success=True,
            message=f"✓ {request.item_name} confirmed as correct",
            learning_id=learning_id
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error confirming calculation: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{tax_return_id}/feedback", response_model=CalculationFeedbackResponse)
async def submit_calculation_feedback(
    tax_return_id: UUID,
    request: CalculationFeedbackRequest,
    db: AsyncSession = Depends(get_db)
):
    """Submit feedback for a calculation and save it as a learning."""
    try:
        # Import skill learning service
        from app.services.phase2_feedback_learning.skill_learning_service import (
            SkillLearningService,
            LearningType,
            AppliesTo
        )

        # Get tax return for context
        result = await db.execute(
            select(TaxReturn).where(TaxReturn.id == tax_return_id)
        )
        tax_return = result.scalar_one_or_none()
        if not tax_return:
            raise HTTPException(status_code=404, detail="Tax return not found")

        # Initialize skill learning service
        skill_service = SkillLearningService(db)

        # Determine learning type
        learning_type = LearningType.CORRECTION if request.feedback_type == 'correction' else LearningType.TEACHING

        # Build title and content
        title = f"{request.feedback_type.title()}: {request.item_name} ({request.category})"
        content = f"""
Feedback for {request.category} calculation: {request.item_name}
Type: {request.feedback_type}

{request.content}
"""
        if request.expected_value is not None:
            content += f"\nExpected Value: ${request.expected_value:,.2f}"

        # Extract keywords for better retrieval
        keywords = [
            request.item_key,
            request.category,
            request.item_name.lower(),
            'calculation',
            'workings'
        ]

        # Create the learning
        learning = await skill_service.create_learning(
            skill_name='nz_rental_returns',
            learning_type=learning_type,
            title=title,
            content=content,
            keywords=keywords,
            applies_to=AppliesTo.CALCULATION,
            client_id=tax_return.client_id,
            category_code=request.item_key,
            created_by='user_feedback'
        )

        learning_id = str(learning.id) if learning else None

        # Handle recalculation based on mode
        recalculated = False
        recalc_message = ""

        if request.recalculate_mode == 'item':
            # Recalculate just this line item
            # If expected_value provided, use it directly; otherwise Claude will use learnings
            try:
                ai_brain = get_ai_brain()
                result = await ai_brain.recalculate_line_item(
                    db=db,
                    tax_return_id=tax_return_id,
                    category=request.category,
                    item_key=request.item_key,
                    expected_value=request.expected_value  # Can be None - Claude will use learnings
                )
                recalculated = result.get('success', False)
                if recalculated:
                    old_val = result.get('old_value', 0)
                    new_val = result.get('new_value', 0)
                    source = "AI" if request.expected_value is None else "user"
                    recalc_message = f" {request.item_name} updated by {source}: ${old_val:.2f} → ${new_val:.2f}"
                    logger.info(f"Updated {request.item_key} ({source}): ${old_val:.2f} -> ${new_val:.2f}")
                else:
                    recalc_message = f" (Item recalculation failed: {result.get('error', 'Unknown error')})"
            except Exception as e:
                logger.error(f"Error recalculating line item: {e}")
                recalc_message = f" (Item recalculation failed: {str(e)})"

        elif request.recalculate_mode == 'full':
            # Full P&L recalculation
            try:
                ai_brain = get_ai_brain()
                await ai_brain.process_tax_return(
                    tax_return_id=tax_return_id,
                    db=db,
                    force_reprocess=True
                )
                recalculated = True
                recalc_message = " Full workings recalculated."
                logger.info(f"Full recalculation for tax return {tax_return_id} after feedback")
            except Exception as e:
                logger.error(f"Error recalculating workings: {e}")
                recalc_message = f" (Full recalculation failed: {str(e)})"

        await db.commit()

        return CalculationFeedbackResponse(
            success=True,
            message="Feedback saved and learned!" + recalc_message,
            learning_id=learning_id,
            recalculated=recalculated
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error submitting feedback: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{tax_return_id}/learnings", response_model=LearningsListResponse)
async def get_workings_learnings(
    tax_return_id: UUID,
    limit: int = Query(50, ge=1, le=100),
    db: AsyncSession = Depends(get_db)
):
    """Get learnings related to workings calculations."""
    try:
        from app.services.phase2_feedback_learning.skill_learning_service import (
            SkillLearningService,
            LearningType
        )

        # Get tax return for client context
        result = await db.execute(
            select(TaxReturn).where(TaxReturn.id == tax_return_id)
        )
        tax_return = result.scalar_one_or_none()
        if not tax_return:
            raise HTTPException(status_code=404, detail="Tax return not found")

        # Initialize skill learning service
        skill_service = SkillLearningService(db)

        # Get relevant learnings
        learnings = await skill_service.get_relevant_learnings(
            query="calculation workings income expense deduction",
            skill_name='nz_rental_returns',
            learning_types=[LearningType.CORRECTION, LearningType.TEACHING],
            client_id=tax_return.client_id,
            limit=limit,
            min_confidence=0.0  # Get all learnings regardless of confidence
        )

        # Format learnings for response
        learning_items = []
        for learning in learnings:
            learning_items.append(LearningItem(
                id=learning.get('id', ''),
                learning_type=learning.get('learning_type', 'unknown'),
                title=learning.get('title', ''),
                content=learning.get('content', ''),
                created_at=learning.get('created_at', ''),
                category_code=learning.get('category_code')
            ))

        return LearningsListResponse(
            learnings=learning_items,
            total_count=len(learning_items)
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting learnings: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ================== Helper Functions ==================

async def _count_flags(workings_id: UUID, db: AsyncSession) -> int:
    """Count flags for workings."""
    result = await db.execute(
        select(WorkingsFlag).where(WorkingsFlag.workings_id == workings_id)
    )
    return len(result.scalars().all())


async def _count_document_requests(tax_return_id: UUID, db: AsyncSession) -> int:
    """Count document requests for tax return."""
    result = await db.execute(
        select(DocumentRequest).where(DocumentRequest.tax_return_id == tax_return_id)
    )
    return len(result.scalars().all())


async def _count_client_questions(tax_return_id: UUID, db: AsyncSession) -> int:
    """Count client questions for tax return."""
    result = await db.execute(
        select(ClientQuestion).where(ClientQuestion.tax_return_id == tax_return_id)
    )
    return len(result.scalars().all())


async def _get_flags(
    workings_id: UUID,
    db: AsyncSession,
    status: Optional[str] = None
) -> List[WorkingsFlag]:
    """Get flags for workings."""
    query = select(WorkingsFlag).where(WorkingsFlag.workings_id == workings_id)
    if status:
        query = query.where(WorkingsFlag.status == FlagStatus(status))
    result = await db.execute(query)
    return result.scalars().all()


async def _get_document_requests(
    tax_return_id: UUID,
    db: AsyncSession,
    status: Optional[str] = None
) -> List[DocumentRequest]:
    """Get document requests for tax return."""
    query = select(DocumentRequest).where(DocumentRequest.tax_return_id == tax_return_id)
    if status:
        query = query.where(DocumentRequest.status == RequestStatus(status))
    result = await db.execute(query)
    return result.scalars().all()


async def _get_client_questions(
    tax_return_id: UUID,
    db: AsyncSession,
    status: Optional[str] = None
) -> List[ClientQuestion]:
    """Get client questions for tax return."""
    query = select(ClientQuestion).where(ClientQuestion.tax_return_id == tax_return_id)
    if status:
        query = query.where(ClientQuestion.status == QuestionStatus(status))
    result = await db.execute(query)
    return result.scalars().all()
