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

from app.database import get_db
from app.models.db_models import Client, Document, TaxReturn
from app.schemas.documents import (
    DocumentResponse,
    PropertyType,
    TaxReturnCreate,
    TaxReturnResponse,
    TaxReturnReview,
)
from app.services.document_processor import DocumentProcessor

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
    client_name: str = Form(...),
    property_address: str = Form(...),
    tax_year: str = Form(...),
    property_type: PropertyType = Form(...),
    gst_registered: Optional[str] = Form(None),
    year_of_ownership: int = Form(...),
    files: List[UploadFile] = File(...),
    db: AsyncSession = Depends(get_db)
):
    """Create a new tax return with document analysis."""
    try:
        if not files:
            raise HTTPException(status_code=400, detail="No files uploaded")

        # Convert checkbox value to bool (checkbox sends "on" when checked, nothing when unchecked)
        is_gst_registered = gst_registered in ("on", "true", "True", "1")

        # Create tax return data
        tax_return_data = TaxReturnCreate(
            client_name=client_name,
            property_address=property_address,
            tax_year=tax_year,
            property_type=property_type,
            gst_registered=is_gst_registered,
            year_of_ownership=year_of_ownership
        )

        # Process tax return
        review = await document_processor.process_tax_return(
            db, tax_return_data, files
        )

        return review

    except Exception as e:
        logger.error(f"Error creating tax return: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@api_router.get("/returns/{tax_return_id}", response_model=TaxReturnResponse)
async def get_tax_return(
    tax_return_id: UUID,
    db: AsyncSession = Depends(get_db)
):
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
        updated_at=tax_return.updated_at
    )


@api_router.get("/returns", response_model=List[TaxReturnResponse])
async def list_tax_returns(
    skip: int = 0,
    limit: int = 100,
    db: AsyncSession = Depends(get_db)
):
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
            updated_at=tr.updated_at
        )
        for tr in tax_returns
    ]


@api_router.get("/returns/{tax_return_id}/documents", response_model=List[DocumentResponse])
async def get_tax_return_documents(
    tax_return_id: UUID,
    db: AsyncSession = Depends(get_db)
):
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
            created_at=doc.created_at
        )
        for doc in documents
    ]


# Web Routes
@web_router.get("/", response_class=HTMLResponse)
async def home(request: Request):
    """Home page with upload form."""
    return templates.TemplateResponse("upload.html", {"request": request})


@web_router.post("/upload")
async def upload_documents(
    request: Request,
    client_name: str = Form(...),
    property_address: str = Form(...),
    tax_year: str = Form(...),
    property_type: PropertyType = Form(...),
    gst_registered: Optional[str] = Form(None),
    year_of_ownership: int = Form(...),
    files: List[UploadFile] = File(...),
    db: AsyncSession = Depends(get_db)
):
    """Handle form submission and redirect to results."""
    try:
        # Convert checkbox value to bool
        is_gst_registered = gst_registered in ("on", "true", "True", "1")

        # Create tax return data
        tax_return_data = TaxReturnCreate(
            client_name=client_name,
            property_address=property_address,
            tax_year=tax_year,
            property_type=property_type,
            gst_registered=is_gst_registered,
            year_of_ownership=year_of_ownership
        )

        # Process tax return
        review = await document_processor.process_tax_return(
            db, tax_return_data, files
        )

        # Redirect to results page
        return RedirectResponse(
            url=f"/result/{review.tax_return_id}",
            status_code=303
        )

    except Exception as e:
        logger.error(f"Error processing upload: {e}")
        return templates.TemplateResponse(
            "upload.html",
            {
                "request": request,
                "error": str(e)
            }
        )


@web_router.get("/result/{tax_return_id}", response_class=HTMLResponse)
async def show_result(
    request: Request,
    tax_return_id: UUID,
    db: AsyncSession = Depends(get_db)
):
    """Show tax return processing results."""
    # Get tax return with all related data
    result = await db.execute(
        select(TaxReturn)
        .options(
            selectinload(TaxReturn.client),
            selectinload(TaxReturn.documents)
        )
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
            "review": review_data
        }
    )