"""API routes for transaction management."""
import logging
from decimal import Decimal
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select, and_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models.db_models import (
    CategoryFeedback,
    Document,
    PLRowMapping,
    TaxReturn,
    Transaction,
    TransactionPattern,
    TransactionSummary,
)
from app.schemas.transactions import (
    CategoryFeedbackCreate,
    CategoryFeedbackResponse,
    PLRowMappingResponse,
    TransactionBulkUpdate,
    TransactionFilter,
    TransactionListResponse,
    TransactionPatternResponse,
    TransactionResponse,
    TransactionSummaryResponse,
    TransactionUpdate,
)
from app.services.transaction_categorizer import get_transaction_categorizer
from app.services.tax_rules_service import get_tax_rules_service
from app.services.transaction_processor import TransactionProcessor
from app.schemas.transactions import ProcessingResult

logger = logging.getLogger(__name__)

# Create router
transaction_router = APIRouter(prefix="/api/transactions", tags=["transactions"])
transaction_web_router = APIRouter(tags=["transactions-web"])

# Templates
templates = Jinja2Templates(directory="app/templates")


# =============================================================================
# API ROUTES
# =============================================================================

@transaction_router.get("/return/{tax_return_id}", response_model=TransactionListResponse)
async def get_transactions_for_return(
    tax_return_id: UUID,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    category_code: Optional[str] = None,
    needs_review: Optional[bool] = None,
    transaction_type: Optional[str] = None,
    min_amount: Optional[float] = None,
    max_amount: Optional[float] = None,
    search: Optional[str] = None,
    db: AsyncSession = Depends(get_db)
):
    """Get transactions for a tax return with filtering and pagination."""
    # Build query
    query = select(Transaction).where(Transaction.tax_return_id == tax_return_id)

    # Apply filters
    if category_code:
        query = query.where(Transaction.category_code == category_code)
    if needs_review is not None:
        query = query.where(Transaction.needs_review == needs_review)
    if transaction_type:
        query = query.where(Transaction.transaction_type == transaction_type)
    if min_amount is not None:
        query = query.where(func.abs(Transaction.amount) >= min_amount)
    if max_amount is not None:
        query = query.where(func.abs(Transaction.amount) <= max_amount)
    if search:
        query = query.where(Transaction.description.ilike(f"%{search}%"))

    # Get total count
    count_query = select(func.count()).select_from(query.subquery())
    total_result = await db.execute(count_query)
    total = total_result.scalar()

    # Apply pagination
    offset = (page - 1) * page_size
    query = query.order_by(Transaction.transaction_date.desc()).offset(offset).limit(page_size)

    result = await db.execute(query)
    transactions = result.scalars().all()

    # Get P&L mappings for display names
    mappings_result = await db.execute(select(PLRowMapping))
    mappings = {m.category_code: m for m in mappings_result.scalars().all()}

    # Build response
    transaction_responses = []
    for txn in transactions:
        mapping = mappings.get(txn.category_code)
        transaction_responses.append(TransactionResponse(
            id=txn.id,
            tax_return_id=txn.tax_return_id,
            document_id=txn.document_id,
            transaction_date=txn.transaction_date,
            description=txn.description,
            other_party=txn.other_party,
            amount=txn.amount,
            balance=txn.balance,
            category_code=txn.category_code,
            category_display_name=mapping.display_name if mapping else None,
            transaction_type=txn.transaction_type,
            pl_row=mapping.pl_row if mapping else None,
            is_deductible=txn.is_deductible,
            deductible_percentage=txn.deductible_percentage,
            deductible_amount=txn.deductible_amount,
            gst_inclusive=txn.gst_inclusive,
            gst_amount=txn.gst_amount,
            confidence=txn.confidence,
            categorization_source=txn.categorization_source,
            needs_review=txn.needs_review,
            review_reason=txn.review_reason,
            manually_reviewed=txn.manually_reviewed,
            reviewed_by=txn.reviewed_by,
            reviewed_at=txn.reviewed_at,
            created_at=txn.created_at,
            updated_at=txn.updated_at
        ))

    # Calculate summary stats
    all_txns_query = select(Transaction).where(Transaction.tax_return_id == tax_return_id)
    all_result = await db.execute(all_txns_query)
    all_txns = all_result.scalars().all()

    total_income = sum(t.amount for t in all_txns if t.amount > 0)
    total_expenses = sum(abs(t.amount) for t in all_txns if t.amount < 0)
    needs_review_count = sum(1 for t in all_txns if t.needs_review)

    return TransactionListResponse(
        transactions=transaction_responses,
        total=total,
        page=page,
        page_size=page_size,
        total_pages=(total + page_size - 1) // page_size,
        total_income=Decimal(str(total_income)),
        total_expenses=Decimal(str(total_expenses)),
        needs_review_count=needs_review_count
    )


@transaction_router.get("/{transaction_id}", response_model=TransactionResponse)
async def get_transaction(
    transaction_id: UUID,
    db: AsyncSession = Depends(get_db)
):
    """Get a single transaction by ID."""
    result = await db.execute(
        select(Transaction).where(Transaction.id == transaction_id)
    )
    txn = result.scalar_one_or_none()

    if not txn:
        raise HTTPException(status_code=404, detail="Transaction not found")

    # Get mapping
    mapping_result = await db.execute(
        select(PLRowMapping).where(PLRowMapping.category_code == txn.category_code)
    )
    mapping = mapping_result.scalar_one_or_none()

    return TransactionResponse(
        id=txn.id,
        tax_return_id=txn.tax_return_id,
        document_id=txn.document_id,
        transaction_date=txn.transaction_date,
        description=txn.description,
        other_party=txn.other_party,
        amount=txn.amount,
        balance=txn.balance,
        category_code=txn.category_code,
        category_display_name=mapping.display_name if mapping else None,
        transaction_type=txn.transaction_type,
        pl_row=mapping.pl_row if mapping else None,
        is_deductible=txn.is_deductible,
        deductible_percentage=txn.deductible_percentage,
        deductible_amount=txn.deductible_amount,
        gst_inclusive=txn.gst_inclusive,
        gst_amount=txn.gst_amount,
        confidence=txn.confidence,
        categorization_source=txn.categorization_source,
        needs_review=txn.needs_review,
        review_reason=txn.review_reason,
        manually_reviewed=txn.manually_reviewed,
        reviewed_by=txn.reviewed_by,
        reviewed_at=txn.reviewed_at,
        created_at=txn.created_at,
        updated_at=txn.updated_at
    )


@transaction_router.put("/{transaction_id}", response_model=TransactionResponse)
async def update_transaction(
    transaction_id: UUID,
    update: TransactionUpdate,
    db: AsyncSession = Depends(get_db)
):
    """Update a transaction (typically for category correction)."""
    result = await db.execute(
        select(Transaction).where(Transaction.id == transaction_id)
    )
    txn = result.scalar_one_or_none()

    if not txn:
        raise HTTPException(status_code=404, detail="Transaction not found")

    # If category is being changed, use the categorizer to learn
    if update.category_code and update.category_code != txn.category_code:
        categorizer = get_transaction_categorizer()
        await categorizer.learn_from_correction(
            db=db,
            transaction_id=transaction_id,
            corrected_category=update.category_code,
            corrected_by=None,  # Would come from auth in production
            notes=update.notes,
            create_pattern=True
        )
    else:
        # Just update the fields
        if update.category_code is not None:
            txn.category_code = update.category_code
        if update.transaction_type is not None:
            txn.transaction_type = update.transaction_type
        if update.is_deductible is not None:
            txn.is_deductible = update.is_deductible
        if update.deductible_percentage is not None:
            txn.deductible_percentage = update.deductible_percentage
        if update.needs_review is not None:
            txn.needs_review = update.needs_review
        if update.review_reason is not None:
            txn.review_reason = update.review_reason

        await db.commit()
        await db.refresh(txn)

    # Get updated transaction
    return await get_transaction(transaction_id, db)


@transaction_router.post("/bulk-update")
async def bulk_update_transactions(
    update: TransactionBulkUpdate,
    db: AsyncSession = Depends(get_db)
):
    """Bulk update multiple transactions to the same category."""
    categorizer = get_transaction_categorizer()

    updated_count = 0
    pattern_id = None

    for txn_id in update.transaction_ids:
        try:
            result = await categorizer.learn_from_correction(
                db=db,
                transaction_id=txn_id,
                corrected_category=update.category_code,
                create_pattern=update.apply_to_similar
            )
            if result and not pattern_id:
                pattern_id = result
            updated_count += 1
        except Exception as e:
            logger.error(f"Failed to update transaction {txn_id}: {e}")

    return {
        "updated_count": updated_count,
        "pattern_id": str(pattern_id) if pattern_id else None,
        "apply_to_similar": update.apply_to_similar
    }


@transaction_router.get("/summaries/{tax_return_id}", response_model=List[TransactionSummaryResponse])
async def get_transaction_summaries(
    tax_return_id: UUID,
    db: AsyncSession = Depends(get_db)
):
    """Get category summaries for a tax return."""
    result = await db.execute(
        select(TransactionSummary)
        .where(TransactionSummary.tax_return_id == tax_return_id)
        .order_by(TransactionSummary.category_code)
    )
    summaries = result.scalars().all()

    # Get P&L mappings
    mappings_result = await db.execute(select(PLRowMapping))
    mappings = {m.category_code: m for m in mappings_result.scalars().all()}

    return [
        TransactionSummaryResponse(
            id=s.id,
            tax_return_id=s.tax_return_id,
            category_code=s.category_code,
            category_display_name=mappings.get(s.category_code, {}).display_name if mappings.get(s.category_code) else None,
            pl_row=mappings.get(s.category_code).pl_row if mappings.get(s.category_code) else None,
            transaction_type=s.transaction_type,
            transaction_count=s.transaction_count,
            gross_amount=s.gross_amount,
            deductible_amount=s.deductible_amount,
            gst_amount=s.gst_amount,
            monthly_breakdown=s.monthly_breakdown,
            created_at=s.created_at,
            updated_at=s.updated_at
        )
        for s in summaries
    ]


@transaction_router.post("/summaries/{tax_return_id}/regenerate")
async def regenerate_summaries(
    tax_return_id: UUID,
    db: AsyncSession = Depends(get_db)
):
    """Regenerate category summaries after corrections."""
    categorizer = get_transaction_categorizer()
    summaries = await categorizer.generate_summaries(db, tax_return_id)

    return {
        "status": "success",
        "summaries_generated": len(summaries)
    }


# =============================================================================
# CATEGORY MANAGEMENT
# =============================================================================

@transaction_router.get("/categories/all", response_model=List[PLRowMappingResponse])
async def get_all_categories(
    db: AsyncSession = Depends(get_db)
):
    """Get all available categories for dropdown."""
    result = await db.execute(
        select(PLRowMapping).order_by(PLRowMapping.sort_order)
    )
    mappings = result.scalars().all()

    return [
        PLRowMappingResponse(
            id=m.id,
            category_code=m.category_code,
            pl_row=m.pl_row,
            display_name=m.display_name,
            transaction_type=m.transaction_type,
            is_deductible=m.is_deductible,
            default_source=m.default_source,
            sort_order=m.sort_order
        )
        for m in mappings
    ]


# =============================================================================
# PATTERNS MANAGEMENT
# =============================================================================

@transaction_router.get("/patterns", response_model=List[TransactionPatternResponse])
async def get_learned_patterns(
    limit: int = Query(100, ge=1, le=500),
    db: AsyncSession = Depends(get_db)
):
    """Get learned transaction patterns."""
    result = await db.execute(
        select(TransactionPattern)
        .order_by(TransactionPattern.times_applied.desc())
        .limit(limit)
    )
    patterns = result.scalars().all()

    return [
        TransactionPatternResponse(
            id=p.id,
            description_normalized=p.description_normalized,
            other_party_normalized=p.other_party_normalized,
            category_code=p.category_code,
            confidence=p.confidence,
            times_applied=p.times_applied,
            times_confirmed=p.times_confirmed,
            times_corrected=p.times_corrected,
            is_global=p.is_global,
            client_id=p.client_id,
            source=p.source,
            created_at=p.created_at,
            last_used_at=p.last_used_at
        )
        for p in patterns
    ]


@transaction_router.delete("/patterns/{pattern_id}")
async def delete_pattern(
    pattern_id: UUID,
    db: AsyncSession = Depends(get_db)
):
    """Delete a learned pattern."""
    result = await db.execute(
        select(TransactionPattern).where(TransactionPattern.id == pattern_id)
    )
    pattern = result.scalar_one_or_none()

    if not pattern:
        raise HTTPException(status_code=404, detail="Pattern not found")

    await db.delete(pattern)
    await db.commit()

    return {"status": "deleted", "pattern_id": str(pattern_id)}


# =============================================================================
# PROCESSING ENDPOINTS
# =============================================================================

@transaction_router.post("/process/{tax_return_id}", response_model=ProcessingResult)
async def process_all_transactions(
    tax_return_id: UUID,
    use_claude: bool = Query(True),
    db: AsyncSession = Depends(get_db)
):
    """Process all bank statement documents for a tax return."""
    processor = TransactionProcessor()

    result = await processor.process_tax_return_transactions(
        db=db,
        tax_return_id=tax_return_id,
        use_claude=use_claude
    )

    return result


@transaction_router.post("/process/document", response_model=ProcessingResult)
async def process_document(
    document_id: UUID = Query(...),
    tax_return_id: UUID = Query(...),
    force_reprocess: bool = Query(False),
    db: AsyncSession = Depends(get_db)
):
    """Process a document to extract and categorize transactions."""
    processor = TransactionProcessor()

    result = await processor.process_document(
        db=db,
        document_id=document_id,
        tax_return_id=tax_return_id,
        force_reprocess=force_reprocess
    )

    return result


@transaction_router.post("/process/reprocess", response_model=ProcessingResult)
async def reprocess_transactions(
    tax_return_id: UUID,
    transaction_ids: Optional[List[UUID]] = None,
    db: AsyncSession = Depends(get_db)
):
    """Reprocess transactions with updated rules and patterns."""
    processor = TransactionProcessor()

    result = await processor.reprocess_transactions(
        db=db,
        tax_return_id=tax_return_id,
        transaction_ids=transaction_ids
    )

    return result


@transaction_router.post("/process/learn")
async def learn_from_feedback(
    transaction_id: UUID,
    correct_category: str,
    confidence_adjustment: float = Query(0.1, ge=0.0, le=1.0),
    db: AsyncSession = Depends(get_db)
):
    """Learn from user correction and update patterns."""
    processor = TransactionProcessor()

    success = await processor.learn_from_feedback(
        db=db,
        transaction_id=transaction_id,
        correct_category=correct_category,
        confidence_adjustment=confidence_adjustment
    )

    return {
        "success": success,
        "message": "Pattern learning successful" if success else "Failed to learn pattern"
    }


# =============================================================================
# WORKBOOK GENERATION
# =============================================================================

@transaction_router.post("/workbook/{tax_return_id}")
async def generate_workbook(
    tax_return_id: UUID,
    db: AsyncSession = Depends(get_db)
):
    """Generate IR3R Excel workbook for a tax return."""
    from app.services.workbook_generator import get_workbook_generator

    generator = get_workbook_generator()

    try:
        filepath = await generator.generate_workbook(db, tax_return_id)

        # Return download info
        filename = filepath.name

        return {
            "status": "success",
            "filename": filename,
            "download_url": f"/api/transactions/workbook/{tax_return_id}/download"
        }

    except Exception as e:
        logger.error(f"Error generating workbook: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@transaction_router.get("/workbook/{tax_return_id}/download")
async def download_workbook(
    tax_return_id: UUID,
    db: AsyncSession = Depends(get_db),
    force_regenerate: bool = False  # Add query param to force regeneration
):
    """Download generated workbook."""
    from app.services.workbook_generator import get_workbook_generator

    generator = get_workbook_generator()

    # Get tax return for filename
    result = await db.execute(
        select(TaxReturn)
        .options(selectinload(TaxReturn.client))
        .where(TaxReturn.id == tax_return_id)
    )
    tax_return = result.scalar_one_or_none()

    if not tax_return:
        raise HTTPException(status_code=404, detail="Tax return not found")

    # Build filename pattern - must match what workbook_generator creates!
    def _sanitize_filename(name: str) -> str:
        return "".join(c if c.isalnum() or c in "_- " else "_" for c in name).replace(" ", "_")

    client_name = _sanitize_filename(tax_return.client.name)
    year = tax_return.tax_year[-2:]  # FY24 -> 24
    filename = f"PTR01_-_Rental_Property_Workbook_-_{client_name}_-_{year}.xlsx"
    filepath = generator.output_dir / filename

    # Log what's happening
    if filepath.exists():
        logger.info(f"Found existing workbook at: {filepath}")
        if force_regenerate:
            logger.info(f"Force regeneration requested, deleting old file")
            filepath.unlink()  # Delete the old file
        else:
            # Check the sheet structure of existing file
            from openpyxl import load_workbook
            try:
                wb = load_workbook(filepath, read_only=True)
                logger.info(f"Existing workbook sheets: {wb.sheetnames}")
                wb.close()
            except Exception as e:
                logger.error(f"Error reading existing workbook: {e}")

    if not filepath.exists() or force_regenerate:
        logger.info(f"Generating new workbook for tax return {tax_return_id}")
        # Generate new workbook
        filepath = await generator.generate_workbook(db, tax_return_id)

        # Verify the new file
        from openpyxl import load_workbook
        try:
            wb = load_workbook(filepath, read_only=True)
            logger.info(f"Generated workbook sheets: {wb.sheetnames}")
            wb.close()
        except Exception as e:
            logger.error(f"Error reading generated workbook: {e}")

    return FileResponse(
        path=filepath,
        filename=filename,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )


# =============================================================================
# FEEDBACK / CORRECTIONS
# =============================================================================

@transaction_router.post("/feedback", response_model=CategoryFeedbackResponse)
async def submit_feedback(
    feedback: CategoryFeedbackCreate,
    db: AsyncSession = Depends(get_db)
):
    """Submit a category correction."""
    categorizer = get_transaction_categorizer()

    pattern_id = await categorizer.learn_from_correction(
        db=db,
        transaction_id=feedback.transaction_id,
        corrected_category=feedback.corrected_category,
        corrected_by=feedback.corrected_by,
        notes=feedback.notes,
        create_pattern=feedback.create_pattern
    )

    # Get the feedback record
    result = await db.execute(
        select(CategoryFeedback)
        .where(CategoryFeedback.transaction_id == feedback.transaction_id)
        .order_by(CategoryFeedback.corrected_at.desc())
    )
    fb = result.scalars().first()

    return CategoryFeedbackResponse(
        id=fb.id,
        transaction_id=fb.transaction_id,
        original_category=fb.original_category,
        corrected_category=fb.corrected_category,
        corrected_by=fb.corrected_by,
        corrected_at=fb.corrected_at,
        notes=fb.notes,
        pattern_created=fb.pattern_created,
        pattern_id=fb.pattern_id
    )


# =============================================================================
# WEB ROUTES
# =============================================================================

@transaction_web_router.get("/transactions/{tax_return_id}", response_class=HTMLResponse)
async def transactions_page(
    request: Request,
    tax_return_id: UUID,
    db: AsyncSession = Depends(get_db)
):
    """Transaction review page."""
    # Get tax return with client
    result = await db.execute(
        select(TaxReturn)
        .options(selectinload(TaxReturn.client))
        .where(TaxReturn.id == tax_return_id)
    )
    tax_return = result.scalar_one_or_none()

    if not tax_return:
        raise HTTPException(status_code=404, detail="Tax return not found")

    # Get transactions
    txn_result = await db.execute(
        select(Transaction)
        .where(Transaction.tax_return_id == tax_return_id)
        .order_by(Transaction.transaction_date.desc())
    )
    transactions = txn_result.scalars().all()

    # Get categories for dropdown
    cat_result = await db.execute(
        select(PLRowMapping).order_by(PLRowMapping.sort_order)
    )
    categories = cat_result.scalars().all()

    # Get summaries
    sum_result = await db.execute(
        select(TransactionSummary)
        .where(TransactionSummary.tax_return_id == tax_return_id)
    )
    summaries = sum_result.scalars().all()

    # Calculate stats
    total_income = sum(t.amount for t in transactions if t.amount > 0)
    total_expenses = sum(abs(t.amount) for t in transactions if t.amount < 0)
    needs_review_count = sum(1 for t in transactions if t.needs_review)

    # Create category lookup
    category_map = {c.category_code: c for c in categories}

    return templates.TemplateResponse(
        "transactions.html",
        {
            "request": request,
            "tax_return": tax_return,
            "client": tax_return.client,
            "transactions": transactions,
            "categories": categories,
            "category_map": category_map,
            "summaries": summaries,
            "total_income": total_income,
            "total_expenses": total_expenses,
            "needs_review_count": needs_review_count,
            "total_count": len(transactions)
        }
    )