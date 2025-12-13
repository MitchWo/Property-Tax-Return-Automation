"""API and web routes for the property tax agent."""

import logging
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.database import get_db
from app.models.db_models import Document, TaxReturn
from app.schemas.documents import (
    DocumentResponse,
    PropertyType,
    TaxReturnCreate,
    TaxReturnResponse,
    TaxReturnReview,
)
from app.schemas.feedback import (
    FeedbackCreate,
    FeedbackResponse,
    LearningItem,
    LearningsListResponse,
    TransactionFeedbackCreate,
    TransactionFeedbackResponse,
)
from app.services.document_processor import DocumentProcessor
from app.services.knowledge_store import knowledge_store

logger = logging.getLogger(__name__)

# Create routers
api_router = APIRouter(prefix="/api")
web_router = APIRouter()

# Setup templates
templates = Jinja2Templates(directory="app/templates")

# Initialize document processor
document_processor = DocumentProcessor()


# API Routes
@api_router.post("/returns", response_model=TaxReturnReview)
async def create_tax_return(
    first_name: str = Form(...),
    last_name: str = Form(...),
    property_address: str = Form(...),
    tax_year: str = Form(...),
    property_type: PropertyType = Form(...),
    gst_registered: Optional[str] = Form(None),
    year_of_ownership: int = Form(...),
    files: List[UploadFile] = File(...),
    db: AsyncSession = Depends(get_db),
):
    """Create a new tax return with document analysis."""
    try:
        if not files:
            raise HTTPException(status_code=400, detail="No files uploaded")

        # Convert GST value - can be true, false, or not_sure
        if gst_registered == "not_sure":
            is_gst_registered = None  # None indicates user wants AI suggestion
        else:
            is_gst_registered = gst_registered in ("on", "true", "True", "1")

        # Combine first and last name
        client_name = f"{first_name} {last_name}".strip()

        # Create tax return data
        tax_return_data = TaxReturnCreate(
            client_name=client_name,
            property_address=property_address,
            tax_year=tax_year,
            property_type=property_type,
            gst_registered=is_gst_registered,
            year_of_ownership=year_of_ownership,
        )

        # Process tax return
        review = await document_processor.process_tax_return(db, tax_return_data, files)

        return review

    except Exception as e:
        logger.error(f"Error creating tax return: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@api_router.get("/returns/{tax_return_id}", response_model=TaxReturnResponse)
async def get_tax_return(tax_return_id: UUID, db: AsyncSession = Depends(get_db)):
    """Get a tax return by ID."""
    result = await db.execute(
        select(TaxReturn)
        .options(selectinload(TaxReturn.client))
        .where(TaxReturn.id == tax_return_id)
    )
    tax_return = result.scalar_one_or_none()

    if not tax_return:
        raise HTTPException(status_code=404, detail="Tax return not found")

    return TaxReturnResponse(
        id=tax_return.id,
        client_id=tax_return.client_id,
        client_name=tax_return.client.name,
        property_address=tax_return.property_address,
        tax_year=tax_return.tax_year,
        property_type=tax_return.property_type,
        gst_registered=tax_return.gst_registered,
        year_of_ownership=tax_return.year_of_ownership,
        status=tax_return.status,
        review_result=tax_return.review_result,
        created_at=tax_return.created_at,
        updated_at=tax_return.updated_at,
    )


@api_router.get("/returns", response_model=List[TaxReturnResponse])
async def list_tax_returns(skip: int = 0, limit: int = 100, db: AsyncSession = Depends(get_db)):
    """List all tax returns with pagination."""
    result = await db.execute(
        select(TaxReturn)
        .options(selectinload(TaxReturn.client))
        .offset(skip)
        .limit(limit)
        .order_by(TaxReturn.created_at.desc())
    )
    tax_returns = result.scalars().all()

    return [
        TaxReturnResponse(
            id=tr.id,
            client_id=tr.client_id,
            client_name=tr.client.name,
            property_address=tr.property_address,
            tax_year=tr.tax_year,
            property_type=tr.property_type,
            gst_registered=tr.gst_registered,
            year_of_ownership=tr.year_of_ownership,
            status=tr.status,
            review_result=tr.review_result,
            created_at=tr.created_at,
            updated_at=tr.updated_at,
        )
        for tr in tax_returns
    ]


@api_router.get("/returns/{tax_return_id}/documents", response_model=List[DocumentResponse])
async def get_tax_return_documents(tax_return_id: UUID, db: AsyncSession = Depends(get_db)):
    """Get all documents for a tax return."""
    result = await db.execute(
        select(Document)
        .where(Document.tax_return_id == tax_return_id)
        .order_by(Document.created_at)
    )
    documents = result.scalars().all()

    return [
        DocumentResponse(
            id=doc.id,
            tax_return_id=doc.tax_return_id,
            original_filename=doc.original_filename,
            document_type=doc.document_type,
            classification_confidence=doc.classification_confidence,
            extracted_data=doc.extracted_data,
            status=doc.status,
            created_at=doc.created_at,
        )
        for doc in documents
    ]


# Web Routes
@web_router.get("/", response_class=HTMLResponse)
async def home(request: Request):
    """Home page with upload form."""
    return templates.TemplateResponse(
        "upload.html", {"request": request, "google_maps_api_key": settings.GOOGLE_MAPS_API_KEY}
    )


@web_router.post("/upload")
async def upload_documents(
    request: Request,
    first_name: str = Form(...),
    last_name: str = Form(...),
    property_address: str = Form(...),
    tax_year: str = Form(...),
    property_type: PropertyType = Form(...),
    gst_registered: Optional[str] = Form(None),
    year_of_ownership: int = Form(...),
    files: List[UploadFile] = File(...),
    db: AsyncSession = Depends(get_db),
):
    """Handle form submission and redirect to results."""
    try:
        # Convert GST value - can be true, false, or not_sure
        if gst_registered == "not_sure":
            is_gst_registered = None  # None indicates user wants AI suggestion
        else:
            is_gst_registered = gst_registered in ("on", "true", "True", "1")

        # Combine first and last name
        client_name = f"{first_name} {last_name}".strip()

        # Create tax return data
        tax_return_data = TaxReturnCreate(
            client_name=client_name,
            property_address=property_address,
            tax_year=tax_year,
            property_type=property_type,
            gst_registered=is_gst_registered,
            year_of_ownership=year_of_ownership,
        )

        # Process tax return
        review = await document_processor.process_tax_return(db, tax_return_data, files)

        # Redirect to results page
        return RedirectResponse(url=f"/result/{review.tax_return_id}", status_code=303)

    except Exception as e:
        logger.error(f"Error processing upload: {e}")
        return templates.TemplateResponse(
            "upload.html",
            {
                "request": request,
                "error": str(e),
                "google_maps_api_key": settings.GOOGLE_MAPS_API_KEY,
            },
        )


@web_router.get("/result/{tax_return_id}", response_class=HTMLResponse)
async def show_result(request: Request, tax_return_id: UUID, db: AsyncSession = Depends(get_db)):
    """Show tax return processing results."""
    # Get tax return with all related data
    result = await db.execute(
        select(TaxReturn)
        .options(selectinload(TaxReturn.client), selectinload(TaxReturn.documents))
        .where(TaxReturn.id == tax_return_id)
    )
    tax_return = result.scalar_one_or_none()

    if not tax_return:
        raise HTTPException(status_code=404, detail="Tax return not found")

    # Parse review result if available
    review_data = tax_return.review_result or {}

    return templates.TemplateResponse(
        "result.html",
        {
            "request": request,
            "tax_return": tax_return,
            "client": tax_return.client,
            "documents": tax_return.documents,
            "review": review_data,
        },
    )


@web_router.post("/result/{tax_return_id}/add-documents")
async def add_documents(
    request: Request,
    tax_return_id: UUID,
    files: List[UploadFile] = File(...),
    db: AsyncSession = Depends(get_db),
):
    """Add additional documents to an existing tax return and re-analyze."""
    try:
        # Get existing tax return
        result = await db.execute(
            select(TaxReturn)
            .options(selectinload(TaxReturn.client))
            .where(TaxReturn.id == tax_return_id)
        )
        tax_return = result.scalar_one_or_none()

        if not tax_return:
            raise HTTPException(status_code=404, detail="Tax return not found")

        # Create TaxReturnCreate object from existing data
        tax_return_data = TaxReturnCreate(
            client_name=tax_return.client.name,
            property_address=tax_return.property_address,
            tax_year=tax_return.tax_year,
            property_type=tax_return.property_type,
            gst_registered=tax_return.gst_registered,
            year_of_ownership=tax_return.year_of_ownership,
        )

        # Process additional documents
        await document_processor.add_documents_to_return(db, tax_return_id, tax_return_data, files)

        # Redirect back to results page
        return RedirectResponse(url=f"/result/{tax_return_id}", status_code=303)

    except Exception as e:
        logger.error(f"Error adding documents: {e}")
        # Redirect back with error
        return RedirectResponse(url=f"/result/{tax_return_id}?error={str(e)}", status_code=303)


# === Feedback/Learning Routes ===


@api_router.post("/feedback", response_model=FeedbackResponse)
async def submit_feedback(feedback: FeedbackCreate):
    """
    Submit feedback or correction to improve the system.

    Categories:
    - document_classification: How documents should be classified
    - document_validation: Rules for validating document content
    - expense_classification: How expenses should be categorized
    - blocking_rules: When to block processing
    - general_guidance: General best practices
    """
    from app.models.db_models import local_now

    try:
        record_id = await knowledge_store.store(
            content=feedback.content,
            scenario=feedback.scenario,
            category=feedback.category,
            source="user_feedback",
        )

        if not record_id:
            raise HTTPException(
                status_code=503, detail="Knowledge store unavailable - check Pinecone configuration"
            )

        return FeedbackResponse(
            id=record_id,
            content=feedback.content,
            scenario=feedback.scenario,
            category=feedback.category,
            stored_at=local_now(),
            message="Feedback stored successfully. This will improve future document reviews.",
        )

    except Exception as e:
        logger.error(f"Error storing feedback: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@api_router.get("/learnings", response_model=LearningsListResponse)
async def list_learnings(query: Optional[str] = None, limit: int = 20):
    """
    List or search stored learnings.

    If query is provided, returns semantically similar learnings.
    Otherwise returns recent learnings.
    """
    try:
        if query:
            learnings = await knowledge_store.search(query, top_k=limit, min_score=0.0)
        else:
            learnings = await knowledge_store.list_learnings(limit=limit)

        items = [
            LearningItem(
                id=learning["id"],
                content=learning["content"],
                scenario=learning["scenario"],
                category=learning["category"],
                score=learning.get("score", 0.0),
                created_at=learning.get("created_at"),
            )
            for learning in learnings
        ]

        return LearningsListResponse(total=len(items), learnings=items)

    except Exception as e:
        logger.error(f"Error listing learnings: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@api_router.delete("/learnings/{learning_id}")
async def delete_learning(learning_id: str):
    """Delete a learning by ID."""
    try:
        success = await knowledge_store.delete(learning_id)

        if not success:
            raise HTTPException(status_code=404, detail="Learning not found or delete failed")

        return {"message": "Learning deleted successfully", "id": learning_id}

    except Exception as e:
        logger.error(f"Error deleting learning: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@api_router.get("/knowledge/status")
async def knowledge_status():
    """Check knowledge store and embeddings status."""
    from app.services.embeddings import embeddings_service

    return {
        "pinecone": {
            "enabled": knowledge_store.enabled,
            "index_host": knowledge_store.index_host if knowledge_store.enabled else None,
            "namespace": knowledge_store.namespace if knowledge_store.enabled else None,
        },
        "embeddings": {
            "enabled": embeddings_service.enabled,
            "model": embeddings_service.model if embeddings_service.enabled else None,
            "dimensions": embeddings_service.dimensions if embeddings_service.enabled else None,
            "provider": "openai" if embeddings_service.enabled else "random_vectors",
        },
    }


@api_router.post("/transactions/reconcile")
async def reconcile_transaction(request: Request, db: AsyncSession = Depends(get_db)):
    """
    Quick reconcile a transaction without storing AI learning.
    Use this for one-off transactions that don't improve learning.
    """
    from uuid import UUID

    from app.models.db_models import TaxReturnStatus

    try:
        data = await request.json()
        transaction_description = data.get("transaction_description")
        amount = data.get("amount")
        document_name = data.get("document_name")
        tax_return_id = data.get("tax_return_id")

        status_updated = False
        new_status = None

        if tax_return_id:
            try:
                tax_return_uuid = UUID(tax_return_id)
                result = await db.execute(select(TaxReturn).where(TaxReturn.id == tax_return_uuid))
                tax_return = result.scalar_one_or_none()

                if tax_return and tax_return.review_result:
                    review_result = tax_return.review_result

                    # Initialize reconciled_transactions list if not present
                    if "reconciled_transactions" not in review_result:
                        review_result["reconciled_transactions"] = []

                    # Create a unique key for this transaction
                    txn_key = f"{document_name}:{transaction_description}:{amount}"

                    # Add to reconciled list if not already there
                    if txn_key not in review_result["reconciled_transactions"]:
                        review_result["reconciled_transactions"].append(txn_key)

                    # Count total flagged transactions
                    flagged_summary = review_result.get("flagged_transactions_summary", {})
                    total_flagged = flagged_summary.get("total_flagged", 0)
                    reconciled_count = len(review_result["reconciled_transactions"])

                    # Check if all transactions are reconciled and no blocking issues
                    blocking_issues = review_result.get("blocking_issues", [])
                    all_reconciled = reconciled_count >= total_flagged

                    if all_reconciled and not blocking_issues:
                        tax_return.status = TaxReturnStatus.COMPLETE
                        review_result["status"] = "complete"
                        status_updated = True
                        new_status = "complete"
                        logger.info(
                            f"Tax return {tax_return_id} status updated to complete - all transactions reconciled"
                        )

                    # Update the review result
                    tax_return.review_result = review_result
                    await db.commit()

            except Exception as e:
                logger.error(f"Error updating tax return reconciliation: {e}")

        return {
            "success": True,
            "message": "Transaction reconciled (no learning stored)",
            "status_updated": status_updated,
            "new_status": new_status,
        }

    except Exception as e:
        logger.error(f"Error reconciling transaction: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@api_router.post("/transactions/feedback", response_model=TransactionFeedbackResponse)
async def submit_transaction_feedback(
    feedback: TransactionFeedbackCreate, db: AsyncSession = Depends(get_db)
):
    """
    Submit feedback on a flagged transaction.

    Resolution types:
    - legitimate: Transaction is a valid rental property expense (reduces future false positives)
    - personal: Transaction is personal and should be flagged
    - requires_invoice: Transaction needs invoice/receipt documentation

    This feedback is stored in Pinecone to improve future transaction analysis.
    Also tracks reconciliation progress and updates tax return status when all transactions are reconciled.
    """
    from uuid import UUID

    from app.models.db_models import TaxReturnStatus, local_now

    try:
        # Determine if this is a legitimate rental expense
        is_legitimate = feedback.resolution == "legitimate"

        # Build descriptive notes
        notes = feedback.notes or ""
        if feedback.expense_category:
            notes = f"Category: {feedback.expense_category}. {notes}"
        if feedback.resolution == "requires_invoice":
            notes = f"Documentation required. {notes}"
        elif feedback.resolution == "personal":
            notes = f"Personal expense, not rental-related. {notes}"

        # Store the transaction learning
        record_id = await knowledge_store.store_transaction_learning(
            vendor_name=feedback.vendor_name or "Unknown",
            transaction_description=feedback.transaction_description,
            amount=feedback.amount,
            is_legitimate=is_legitimate,
            document_type=feedback.document_name,
            notes=notes,
        )

        if not record_id:
            raise HTTPException(
                status_code=503, detail="Knowledge store unavailable - check Pinecone configuration"
            )

        # Track reconciliation and potentially update tax return status
        status_updated = False
        new_status = None

        if feedback.tax_return_id:
            try:
                tax_return_uuid = UUID(feedback.tax_return_id)
                result = await db.execute(select(TaxReturn).where(TaxReturn.id == tax_return_uuid))
                tax_return = result.scalar_one_or_none()

                if tax_return and tax_return.review_result:
                    review_result = tax_return.review_result

                    # Initialize reconciled_transactions list if not present
                    if "reconciled_transactions" not in review_result:
                        review_result["reconciled_transactions"] = []

                    # Create a unique key for this transaction
                    txn_key = f"{feedback.document_name}:{feedback.transaction_description}:{feedback.amount}"

                    # Add to reconciled list if not already there
                    if txn_key not in review_result["reconciled_transactions"]:
                        review_result["reconciled_transactions"].append(txn_key)

                    # Count total flagged transactions
                    flagged_summary = review_result.get("flagged_transactions_summary", {})
                    total_flagged = flagged_summary.get("total_flagged", 0)
                    reconciled_count = len(review_result["reconciled_transactions"])

                    # Check if all transactions are reconciled and no blocking issues
                    blocking_issues = review_result.get("blocking_issues", [])
                    all_reconciled = reconciled_count >= total_flagged

                    if all_reconciled and not blocking_issues:
                        # Update status to complete
                        tax_return.status = TaxReturnStatus.COMPLETE
                        review_result["status"] = "complete"
                        status_updated = True
                        new_status = "complete"
                        logger.info(
                            f"Tax return {feedback.tax_return_id} status updated to complete - all transactions reconciled"
                        )
                    elif all_reconciled and blocking_issues:
                        # All transactions reconciled but still have blocking issues
                        logger.info(
                            f"Tax return {feedback.tax_return_id}: all transactions reconciled but {len(blocking_issues)} blocking issues remain"
                        )

                    # Update the review result
                    tax_return.review_result = review_result
                    await db.commit()

            except Exception as e:
                logger.error(f"Error updating tax return reconciliation: {e}")
                # Don't fail the whole request, just log the error

        response_message = f"Transaction feedback stored successfully. This will {'reduce false positives' if is_legitimate else 'improve flagging accuracy'} for similar transactions."
        if status_updated:
            response_message += " Tax return status updated to complete."

        return TransactionFeedbackResponse(
            id=record_id,
            transaction_description=feedback.transaction_description,
            resolution=feedback.resolution,
            stored_at=local_now(),
            message=response_message,
            status_updated=status_updated,
            new_status=new_status,
        )

    except Exception as e:
        logger.error(f"Error storing transaction feedback: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# === Custom Categories API ===


@api_router.post("/categories")
async def create_custom_category(
    name: str = Form(...),
    value: str = Form(...),
    group: str = Form(...),
    is_deductible: bool = Form(True),
):
    """
    Store a custom expense category for future use.
    """
    try:
        content = (
            f"Custom category: {name} (value: {value}, group: {group}, deductible: {is_deductible})"
        )

        record_id = await knowledge_store.store(
            content=content,
            scenario="custom_expense_category",
            category="expense_categories",
            source="user_feedback",
        )

        if not record_id:
            raise HTTPException(status_code=503, detail="Knowledge store unavailable")

        return {
            "id": record_id,
            "name": name,
            "value": value,
            "group": group,
            "is_deductible": is_deductible,
            "message": "Category saved successfully",
        }

    except Exception as e:
        logger.error(f"Error storing custom category: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@api_router.get("/categories")
async def list_custom_categories():
    """
    Get all custom expense categories.
    """
    import re

    try:
        # List all learnings and filter for custom categories
        all_learnings = await knowledge_store.list_learnings(limit=100)

        categories = []
        for item in all_learnings:
            if item.get("scenario") == "custom_expense_category":
                content = item.get("content", "")
                # Parse the content to extract category details
                # Format: "Custom category: Name (value: value, group: group, deductible: bool)"
                try:
                    match = re.search(
                        r"Custom category: (.+?) \(value: (.+?), group: (.+?), deductible: (True|False)\)",
                        content,
                    )
                    if match:
                        categories.append(
                            {
                                "id": item.get("id"),
                                "name": match.group(1),
                                "value": match.group(2),
                                "group": match.group(3),
                                "is_deductible": match.group(4) == "True",
                            }
                        )
                except Exception:
                    pass

        return {"categories": categories}

    except Exception as e:
        logger.error(f"Error listing custom categories: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# === Web Routes for Feedback UI ===


@web_router.get("/returns", response_class=HTMLResponse)
async def returns_list_page(request: Request, db: AsyncSession = Depends(get_db)):
    """List all tax returns."""
    result = await db.execute(
        select(TaxReturn)
        .options(selectinload(TaxReturn.client))
        .order_by(TaxReturn.created_at.desc())
        .limit(100)
    )
    returns = result.scalars().all()

    return templates.TemplateResponse("returns.html", {"request": request, "returns": returns})


@web_router.get("/feedback", response_class=HTMLResponse)
async def feedback_page(request: Request):
    """Feedback submission page."""
    return templates.TemplateResponse("feedback.html", {"request": request})


@web_router.post("/feedback")
async def submit_feedback_web(
    request: Request,
    content: str = Form(...),
    scenario: str = Form(...),
    category: str = Form("general_guidance"),
    tax_return_id: Optional[str] = Form(None),
):
    """Handle feedback form submission."""
    try:
        record_id = await knowledge_store.store(
            content=content, scenario=scenario, category=category, source="user_feedback"
        )

        return templates.TemplateResponse(
            "feedback.html",
            {
                "request": request,
                "success": True,
                "message": f"Feedback stored successfully (ID: {record_id})",
            },
        )

    except Exception as e:
        return templates.TemplateResponse("feedback.html", {"request": request, "error": str(e)})


@web_router.get("/learnings", response_class=HTMLResponse)
async def learnings_page(request: Request, query: Optional[str] = None):
    """View stored learnings."""
    try:
        if query:
            learnings = await knowledge_store.search(query, top_k=50, min_score=0.0)
        else:
            learnings = await knowledge_store.list_learnings(limit=50)

        return templates.TemplateResponse(
            "learnings.html", {"request": request, "learnings": learnings, "query": query}
        )

    except Exception as e:
        return templates.TemplateResponse(
            "learnings.html", {"request": request, "error": str(e), "learnings": []}
        )
