"""Main document processing orchestration service."""
import asyncio
import logging
import uuid
from datetime import datetime
from typing import List, Tuple

from fastapi import UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.db_models import Client, Document, DocumentStatus, TaxReturn, TaxReturnStatus
from app.schemas.documents import (
    DocumentAnalysis,
    DocumentClassification,
    DocumentSummary,
    MissingDocument,
    TaxReturnCreate,
    TaxReturnReview,
)
from app.services.phase1_document_intake.claude_client import ClaudeClient
from app.services.phase1_document_intake.file_handler import FileHandler

logger = logging.getLogger(__name__)


class DocumentProcessor:
    """Orchestrate document processing for tax returns."""

    def __init__(self):
        """Initialize document processor."""
        self.file_handler = FileHandler()
        self.claude_client = ClaudeClient()

    async def process_tax_return(
        self,
        db: AsyncSession,
        tax_return_data: TaxReturnCreate,
        files: List[UploadFile]
    ) -> TaxReturnReview:
        """
        Process a complete tax return with all documents.
        """
        try:
            # 1. Create or get client
            client = await self._get_or_create_client(db, tax_return_data.client_name)

            # 2. Create tax return record
            tax_return = TaxReturn(
                client_id=client.id,
                property_address=tax_return_data.property_address,
                tax_year=tax_return_data.tax_year,
                property_type=tax_return_data.property_type,
                gst_registered=tax_return_data.gst_registered,
                year_of_ownership=tax_return_data.year_of_ownership,
                status=TaxReturnStatus.PENDING
            )
            db.add(tax_return)
            await db.commit()
            await db.refresh(tax_return)

            # 3. Save all files first (no Claude calls yet)
            saved_files = []
            for file in files:
                try:
                    stored_filename, file_path, file_size = await self.file_handler.save_upload(
                        file, str(tax_return.id)
                    )

                    # Create document record
                    document = Document(
                        tax_return_id=tax_return.id,
                        original_filename=file.filename,
                        stored_filename=stored_filename,
                        file_path=file_path,
                        mime_type=file.content_type or "application/octet-stream",
                        file_size=file_size,
                        status=DocumentStatus.PENDING
                    )
                    db.add(document)
                    await db.commit()
                    await db.refresh(document)

                    saved_files.append((document, file.filename))

                except Exception as e:
                    logger.error(f"Error saving file {file.filename}: {e}")
                    continue

            # 4. Process each document with Claude (sequential to avoid rate limits)
            document_analyses = []
            document_summaries = []

            context = {
                "property_address": tax_return_data.property_address,
                "tax_year": tax_return_data.tax_year,
                "property_type": tax_return_data.property_type.value
            }

            for document, original_filename in saved_files:
                try:
                    analysis, summary = await self._analyze_document(
                        db, document, original_filename, context
                    )
                    document_analyses.append(analysis)
                    document_summaries.append(summary)
                except Exception as e:
                    logger.error(f"Error analyzing document {original_filename}: {e}")
                    # Create error summary
                    error_summary = DocumentSummary(
                        document_id=document.id,
                        filename=original_filename,
                        document_type="error",
                        key_details={},
                        flags=["processing_error", str(e)]
                    )
                    document_summaries.append(error_summary)

            # 5. Run final review for completeness
            review_context = {
                "client_name": tax_return_data.client_name,
                "property_address": tax_return_data.property_address,
                "tax_year": tax_return_data.tax_year,
                "property_type": tax_return_data.property_type.value,
                "gst_registered": tax_return_data.gst_registered,
                "year_of_ownership": tax_return_data.year_of_ownership
            }

            review_result = await self.claude_client.review_all_documents(
                document_summaries, review_context
            )

            # 6. Parse review result
            missing_documents = []
            for doc in review_result.get("documents_missing", review_result.get("missing_documents", [])):
                if isinstance(doc, dict):
                    missing_documents.append(MissingDocument(**doc))

            # Handle blocking_issues as list of strings
            blocking_issues = []
            for issue in review_result.get("blocking_issues", []):
                if isinstance(issue, str):
                    blocking_issues.append(issue)
                elif isinstance(issue, dict):
                    blocking_issues.append(issue.get("issue", str(issue)))
                else:
                    blocking_issues.append(str(issue))

            # Determine status
            status_str = review_result.get("status", "pending")
            status_map = {
                "complete": TaxReturnStatus.COMPLETE,
                "incomplete": TaxReturnStatus.INCOMPLETE,
                "blocked": TaxReturnStatus.BLOCKED
            }
            status = status_map.get(status_str, TaxReturnStatus.PENDING)

            # 7. Create review object
            tax_return_review = TaxReturnReview(
                tax_return_id=tax_return.id,
                status=status_str,
                documents_processed=len(files),
                documents_analyzed=document_analyses,
                missing_documents=missing_documents,
                blocking_issues=blocking_issues,
                recommendations=review_result.get("recommendations", []),
                completeness_score=review_result.get("completeness_score", 0.0),
                review_summary=review_result.get("summary", review_result.get("review_summary", "")),
                created_at=datetime.utcnow()
            )

            # 8. Update tax return with review result
            tax_return.status = status
            tax_return.review_result = review_result
            await db.commit()

            return tax_return_review

        except Exception as e:
            logger.error(f"Error processing tax return: {e}")
            await db.rollback()
            raise

    async def _analyze_document(
        self,
        db: AsyncSession,
        document: Document,
        original_filename: str,
        context: dict
    ) -> Tuple[DocumentAnalysis, DocumentSummary]:
        """Analyze a single document with Claude."""

        # Process file content
        processed = await self.file_handler.process_file(
            document.file_path, original_filename
        )

        # Prepare image data if available
        image_data = None
        if processed.image_paths:
            image_data = await self.claude_client.prepare_image_data(
                processed.image_paths[:5]  # Limit to 5 pages
            )

        # Analyze with Claude
        classification = await self.claude_client.analyze_document(
            processed.text_content,
            image_data,
            context
        )

        # Update document record
        document.document_type = classification.document_type
        document.classification_confidence = classification.confidence
        document.extracted_data = {
            "reasoning": classification.reasoning,
            "flags": classification.flags,
            "key_details": classification.key_details
        }
        document.status = DocumentStatus.CLASSIFIED
        await db.commit()

        # Create analysis and summary objects
        analysis = DocumentAnalysis(
            document_id=document.id,
            filename=original_filename,
            classification=classification,
            extracted_data=classification.key_details,
            status=DocumentStatus.CLASSIFIED
        )

        summary = DocumentSummary(
            document_id=document.id,
            filename=original_filename,
            document_type=classification.document_type,
            key_details=classification.key_details,
            flags=classification.flags
        )

        return analysis, summary

    async def _get_or_create_client(
        self,
        db: AsyncSession,
        client_name: str
    ) -> Client:
        """Get existing client or create new one."""
        result = await db.execute(
            select(Client).where(Client.name == client_name)
        )
        client = result.scalar_one_or_none()

        if not client:
            client = Client(name=client_name)
            db.add(client)
            await db.commit()
            await db.refresh(client)

        return client