"""Main document processing orchestration service."""

import logging
import uuid
from typing import Dict, List, Optional, Tuple

from fastapi import UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.db_models import (
    Client,
    Document,
    DocumentStatus,
    TaxReturn,
    TaxReturnStatus,
    local_now,
)
from app.schemas.documents import (
    DocumentAnalysis,
    DocumentClassification,
    DocumentSummary,
    FlaggedTransactionItem,
    FlaggedTransactionsSummary,
    MissingDocument,
    TaxReturnCreate,
    TaxReturnReview,
)
from app.services.phase1_document_intake.claude_client import ClaudeClient
from app.services.phase1_document_intake.file_handler import FileHandler
from app.services.phase2_feedback_learning.knowledge_store import knowledge_store

logger = logging.getLogger(__name__)


class DuplicateInfo:
    """Information about a detected duplicate."""

    def __init__(
        self,
        is_duplicate: bool,
        duplicate_type: Optional[str] = None,  # "filename", "content", or "both"
        original_document_id: Optional[uuid.UUID] = None,
        original_filename: Optional[str] = None,
    ):
        self.is_duplicate = is_duplicate
        self.duplicate_type = duplicate_type
        self.original_document_id = original_document_id
        self.original_filename = original_filename


class DocumentProcessor:
    """Orchestrate document processing for tax returns."""

    def __init__(self):
        """Initialize document processor."""
        self.file_handler = FileHandler()
        self.claude_client = ClaudeClient()

    async def process_tax_return(
        self, db: AsyncSession, tax_return_data: TaxReturnCreate, files: List[UploadFile]
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
                status=TaxReturnStatus.PENDING,
            )
            db.add(tax_return)
            await db.commit()
            await db.refresh(tax_return)

            # 3. Save all files first and detect duplicates
            saved_files = []
            # Track files within this upload for internal duplicate detection
            upload_hashes: Dict[str, Document] = {}  # hash -> first document with that hash
            upload_filenames: Dict[str, Document] = (
                {}
            )  # filename -> first document with that filename
            duplicate_documents = []

            for file in files:
                try:
                    stored_filename, file_path, file_size, content_hash = (
                        await self.file_handler.save_upload(file, str(tax_return.id))
                    )

                    # Check for duplicates within this upload
                    dup_info = await self._check_for_duplicates(
                        db, file.filename, content_hash, upload_filenames, upload_hashes
                    )

                    # Create document record
                    document = Document(
                        tax_return_id=tax_return.id,
                        original_filename=file.filename,
                        stored_filename=stored_filename,
                        file_path=file_path,
                        mime_type=file.content_type or "application/octet-stream",
                        file_size=file_size,
                        content_hash=content_hash,
                        is_duplicate=dup_info.is_duplicate,
                        duplicate_of_id=dup_info.original_document_id,
                        status=DocumentStatus.PENDING,
                    )
                    db.add(document)
                    await db.commit()
                    await db.refresh(document)

                    # Track this document for internal duplicate detection
                    if not dup_info.is_duplicate:
                        upload_hashes[content_hash] = document
                        upload_filenames[file.filename] = document
                        saved_files.append((document, file.filename, dup_info))
                    else:
                        # Still track duplicate but flag it
                        duplicate_documents.append((document, file.filename, dup_info))
                        logger.info(
                            f"Duplicate detected: {file.filename} ({dup_info.duplicate_type})"
                        )

                except Exception as e:
                    logger.error(f"Error saving file {file.filename}: {e}")
                    continue

            # Include duplicates in saved_files for processing (they'll be handled differently)
            saved_files.extend(duplicate_documents)

            # 4. Process each document with Claude (sequential to avoid rate limits)
            document_analyses = []
            document_summaries = []

            context = {
                "client_name": tax_return_data.client_name,
                "property_address": tax_return_data.property_address,
                "tax_year": tax_return_data.tax_year,
                "property_type": tax_return_data.property_type.value,
            }

            for document, original_filename, dup_info in saved_files:
                try:
                    if dup_info.is_duplicate:
                        # Skip Claude analysis for duplicates - create summary with duplicate flag
                        analysis, summary = await self._handle_duplicate_document(
                            db, document, original_filename, dup_info
                        )
                    else:
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
                        flags=["processing_error", str(e)],
                    )
                    document_summaries.append(error_summary)

            # 5. Run final review for completeness
            review_context = {
                "client_name": tax_return_data.client_name,
                "property_address": tax_return_data.property_address,
                "tax_year": tax_return_data.tax_year,
                "property_type": tax_return_data.property_type.value,
                "gst_registered": tax_return_data.gst_registered,
                "year_of_ownership": tax_return_data.year_of_ownership,
            }

            review_result = await self.claude_client.review_all_documents(
                document_summaries, review_context
            )

            # 6. Parse review result
            missing_documents = []
            for doc in review_result.get(
                "documents_missing", review_result.get("missing_documents", [])
            ):
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
                "blocked": TaxReturnStatus.BLOCKED,
            }
            status = status_map.get(status_str, TaxReturnStatus.PENDING)

            # Collect all flagged transactions from document analyses
            flagged_transactions_summary = self._collect_flagged_transactions(document_analyses)

            # If there are critical flagged transactions and status is complete, downgrade to incomplete
            if flagged_transactions_summary and flagged_transactions_summary.critical_count > 0:
                if status == TaxReturnStatus.COMPLETE:
                    status = TaxReturnStatus.INCOMPLETE
                    status_str = "incomplete"
                    if (
                        "Review flagged transactions and provide supporting documentation"
                        not in review_result.get("recommendations", [])
                    ):
                        review_result.setdefault("recommendations", []).append(
                            "Review flagged transactions and provide supporting documentation (invoices/receipts)"
                        )

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
                review_summary=review_result.get(
                    "summary", review_result.get("review_summary", "")
                ),
                flagged_transactions_summary=flagged_transactions_summary,
                created_at=local_now(),
            )

            # 8. Update tax return with review result (include flagged transactions)
            tax_return.status = status
            # Add flagged transactions summary to review result for template rendering
            if flagged_transactions_summary:
                review_result["flagged_transactions_summary"] = (
                    flagged_transactions_summary.model_dump()
                )
            tax_return.review_result = review_result
            await db.commit()

            return tax_return_review

        except Exception as e:
            logger.error(f"Error processing tax return: {e}")
            await db.rollback()
            raise

    async def _analyze_document(
        self, db: AsyncSession, document: Document, original_filename: str, context: dict
    ) -> Tuple[DocumentAnalysis, DocumentSummary]:
        """Analyze a single document with Claude."""

        # Process file content
        processed = await self.file_handler.process_file(document.file_path, original_filename)

        # Prepare image data if available
        image_data = None
        if processed.image_paths:
            image_data = await self.claude_client.prepare_image_data(
                processed.image_paths[:5]  # Limit to 5 pages
            )

        # Fetch relevant transaction learnings from Pinecone
        transaction_learnings = []
        try:
            # Search for any transaction-related learnings
            learnings = await knowledge_store.search(
                query="transaction rental property expense legitimate vendor",
                top_k=10,
                min_score=0.3,  # Lower threshold to catch more relevant patterns
            )
            if learnings:
                transaction_learnings = learnings
                logger.info(f"Found {len(learnings)} relevant transaction learnings")
        except Exception as e:
            logger.warning(f"Could not fetch transaction learnings: {e}")

        # Analyze with Claude, passing any learnings context
        classification = await self.claude_client.analyze_document(
            processed.text_content, image_data, context, transaction_learnings=transaction_learnings
        )

        # Process transaction analysis if present (for financial documents)
        transaction_analysis = classification.key_details.get("transaction_analysis")
        if transaction_analysis:
            flagged_count = len(transaction_analysis.get("flagged_transactions", []))
            if flagged_count > 0:
                classification.flags.append(f"flagged_transactions_{flagged_count}")
            if transaction_analysis.get("requires_invoices"):
                classification.flags.append("requires_source_documents")

        # Update document record
        document.document_type = classification.document_type
        document.classification_confidence = classification.confidence
        document.extracted_data = {
            "reasoning": classification.reasoning,
            "flags": classification.flags,
            "key_details": classification.key_details,
        }
        document.status = DocumentStatus.CLASSIFIED
        await db.commit()

        # Create analysis and summary objects
        analysis = DocumentAnalysis(
            document_id=document.id,
            filename=original_filename,
            classification=classification,
            extracted_data=classification.key_details,
            status=DocumentStatus.CLASSIFIED,
        )

        summary = DocumentSummary(
            document_id=document.id,
            filename=original_filename,
            document_type=classification.document_type,
            key_details=classification.key_details,
            flags=classification.flags,
        )

        return analysis, summary

    async def _get_or_create_client(self, db: AsyncSession, client_name: str) -> Client:
        """Get existing client or create new one."""
        result = await db.execute(select(Client).where(Client.name == client_name))
        client = result.scalar_one_or_none()

        if not client:
            client = Client(name=client_name)
            db.add(client)
            await db.commit()
            await db.refresh(client)

        return client

    async def _check_for_duplicates(
        self,
        db: AsyncSession,
        filename: str,
        content_hash: str,
        upload_filenames: Dict[str, Document],
        upload_hashes: Dict[str, Document],
    ) -> DuplicateInfo:
        """
        Check if a file is a duplicate.

        Checks for duplicates both:
        1. Within the current upload (using upload_filenames and upload_hashes dicts)
        2. Against existing documents in the database

        Args:
            db: Database session
            filename: Original filename
            content_hash: SHA-256 hash of file content
            upload_filenames: Dict of filenames already in this upload
            upload_hashes: Dict of content hashes already in this upload

        Returns:
            DuplicateInfo object with duplicate detection results
        """
        # Check for duplicates within this upload first
        filename_match = upload_filenames.get(filename)
        hash_match = upload_hashes.get(content_hash)

        if filename_match and hash_match:
            # Both filename and content match (within upload)
            return DuplicateInfo(
                is_duplicate=True,
                duplicate_type="both",
                original_document_id=hash_match.id,
                original_filename=hash_match.original_filename,
            )
        elif hash_match:
            # Same content, different filename (within upload)
            return DuplicateInfo(
                is_duplicate=True,
                duplicate_type="content",
                original_document_id=hash_match.id,
                original_filename=hash_match.original_filename,
            )
        elif filename_match:
            # Same filename, different content (within upload)
            return DuplicateInfo(
                is_duplicate=True,
                duplicate_type="filename",
                original_document_id=filename_match.id,
                original_filename=filename_match.original_filename,
            )

        # Only check for duplicates within the current upload
        # Duplicates across different uploads are allowed (same file can be uploaded multiple times)
        return DuplicateInfo(is_duplicate=False)

    async def _handle_duplicate_document(
        self, db: AsyncSession, document: Document, original_filename: str, dup_info: DuplicateInfo
    ) -> Tuple[DocumentAnalysis, DocumentSummary]:
        """
        Handle a duplicate document without Claude analysis.

        Copies classification from original if available, marks as duplicate.
        """
        # Try to get classification from original document
        original_doc = None
        if dup_info.original_document_id:
            result = await db.execute(
                select(Document).where(Document.id == dup_info.original_document_id)
            )
            original_doc = result.scalar_one_or_none()

        # Set document type from original if available
        doc_type = "duplicate"
        key_details = {}
        if original_doc and original_doc.document_type:
            doc_type = original_doc.document_type
            if original_doc.extracted_data:
                key_details = original_doc.extracted_data.get("key_details", {})

        # Build duplicate flag message
        if dup_info.duplicate_type == "both":
            dup_flag = f"duplicate_file_exact_match_of_{dup_info.original_filename}"
        elif dup_info.duplicate_type == "content":
            dup_flag = f"duplicate_content_same_as_{dup_info.original_filename}"
        else:
            dup_flag = f"duplicate_filename_same_as_{dup_info.original_filename}"

        # Update document record
        document.document_type = doc_type
        document.classification_confidence = 1.0 if original_doc else 0.0
        document.extracted_data = {
            "reasoning": f"Duplicate detected ({dup_info.duplicate_type}). Original: {dup_info.original_filename}",
            "flags": [dup_flag],
            "key_details": key_details,
            "duplicate_info": {
                "type": dup_info.duplicate_type,
                "original_filename": dup_info.original_filename,
                "original_document_id": (
                    str(dup_info.original_document_id) if dup_info.original_document_id else None
                ),
            },
        }
        document.status = DocumentStatus.CLASSIFIED
        await db.commit()

        # Create classification object
        classification = DocumentClassification(
            document_type=doc_type,
            confidence=1.0 if original_doc else 0.0,
            reasoning=f"Duplicate detected ({dup_info.duplicate_type}). Original: {dup_info.original_filename}",
            flags=[dup_flag],
            key_details=key_details,
        )

        # Create analysis and summary objects
        analysis = DocumentAnalysis(
            document_id=document.id,
            filename=original_filename,
            classification=classification,
            extracted_data=key_details,
            status=DocumentStatus.CLASSIFIED,
        )

        summary = DocumentSummary(
            document_id=document.id,
            filename=original_filename,
            document_type=doc_type,
            key_details=key_details,
            flags=[dup_flag],
        )

        return analysis, summary

    def _collect_flagged_transactions(
        self, document_analyses: List[DocumentAnalysis]
    ) -> Optional[FlaggedTransactionsSummary]:
        """
        Collect all flagged transactions from document analyses.

        Args:
            document_analyses: List of analyzed documents

        Returns:
            FlaggedTransactionsSummary or None if no flagged transactions
        """
        all_flagged = []
        total_flagged = 0
        critical_count = 0

        for analysis in document_analyses:
            # Check if this document has transaction analysis
            key_details = analysis.classification.key_details
            transaction_analysis = key_details.get("transaction_analysis")

            if not transaction_analysis:
                continue

            flagged_transactions = transaction_analysis.get("flagged_transactions", [])

            for txn in flagged_transactions:
                total_flagged += 1
                severity = txn.get("severity", "info")

                if severity == "critical":
                    critical_count += 1

                # Create summary item
                all_flagged.append(
                    FlaggedTransactionItem(
                        document=analysis.filename,
                        transaction=txn.get("description", "Unknown"),
                        amount=abs(float(txn.get("amount", 0))),
                        reason=", ".join(txn.get("flag_reasons", ["unknown"])),
                        action_required=txn.get("recommended_action", "Review transaction"),
                    )
                )

        if total_flagged == 0:
            return None

        # Generate recommendation based on counts
        if critical_count > 0:
            recommendation = f"Found {total_flagged} transaction(s) requiring review, including {critical_count} critical item(s). Please provide invoices or receipts to support these expenses as rental property deductions."
        else:
            recommendation = f"Found {total_flagged} transaction(s) that may benefit from supporting documentation. Consider providing invoices or receipts where available."

        return FlaggedTransactionsSummary(
            total_flagged=total_flagged,
            critical_count=critical_count,
            transactions_requiring_invoices=all_flagged,
            recommendation=recommendation,
        )

    async def add_documents_to_return(
        self,
        db: AsyncSession,
        tax_return_id: uuid.UUID,
        tax_return_data: TaxReturnCreate,
        files: List[UploadFile],
    ) -> TaxReturnReview:
        """
        Add additional documents to an existing tax return and re-run analysis.

        Args:
            db: Database session
            tax_return_id: ID of existing tax return
            tax_return_data: Tax return details
            files: List of new files to add

        Returns:
            Updated TaxReturnReview with all documents
        """
        # 1. Get existing tax return
        result = await db.execute(
            select(TaxReturn)
            .options(selectinload(TaxReturn.documents))
            .where(TaxReturn.id == tax_return_id)
        )
        tax_return = result.scalar_one_or_none()

        if not tax_return:
            raise ValueError(f"Tax return {tax_return_id} not found")

        # 2. Save new files - always analyze (no duplicate skipping for add documents)
        # User explicitly chose to add documents, so re-analyze even if similar to existing
        saved_files = []

        for file in files:
            try:
                stored_filename, file_path, file_size, content_hash = (
                    await self.file_handler.save_upload(file, str(tax_return_id))
                )

                document = Document(
                    tax_return_id=tax_return_id,
                    original_filename=file.filename,
                    stored_filename=stored_filename,
                    file_path=file_path,
                    mime_type=file.content_type or "application/octet-stream",
                    file_size=file_size,
                    content_hash=content_hash,
                    is_duplicate=False,  # Always analyze added documents
                    duplicate_of_id=None,
                    status=DocumentStatus.PENDING,
                )
                db.add(document)
                await db.commit()
                await db.refresh(document)

                # Always add to saved_files for analysis
                saved_files.append((document, file.filename, DuplicateInfo(is_duplicate=False)))
                logger.info(f"Added document for analysis: {file.filename}")

            except Exception as e:
                logger.error(f"Error saving file {file.filename}: {e}")
                continue

        # 3. Process new documents
        document_analyses = []
        document_summaries = []

        context = {
            "client_name": tax_return_data.client_name,
            "property_address": tax_return_data.property_address,
            "tax_year": tax_return_data.tax_year,
            "property_type": tax_return_data.property_type.value,
        }

        for document, original_filename, _ in saved_files:
            try:
                # Always analyze - no duplicate skipping for added documents
                analysis, summary = await self._analyze_document(
                    db, document, original_filename, context
                )
                document_analyses.append(analysis)
                document_summaries.append(summary)
            except Exception as e:
                logger.error(f"Error analyzing document {original_filename}: {e}")
                error_summary = DocumentSummary(
                    document_id=document.id,
                    filename=original_filename,
                    document_type="error",
                    key_details={"error": str(e)},
                    flags=["processing_error"],
                )
                document_summaries.append(error_summary)

        # 4. Get all document summaries (existing + new)
        all_summaries = []
        for doc in tax_return.documents:
            if doc.extracted_data:
                all_summaries.append(
                    DocumentSummary(
                        document_id=doc.id,
                        filename=doc.original_filename,
                        document_type=doc.document_type or "unknown",
                        key_details=doc.extracted_data.get("key_details", {}),
                        flags=doc.extracted_data.get("flags", []),
                    )
                )
        all_summaries.extend(document_summaries)

        # 5. Re-run completeness review with all documents
        review_context = {
            "client_name": tax_return_data.client_name,
            "property_address": tax_return_data.property_address,
            "tax_year": tax_return_data.tax_year,
            "property_type": tax_return_data.property_type.value,
            "gst_registered": tax_return_data.gst_registered,
            "year_of_ownership": tax_return_data.year_of_ownership,
        }

        review_result = await self.claude_client.review_all_documents(all_summaries, review_context)

        # 6. Parse review result
        missing_documents = []
        for doc in review_result.get(
            "documents_missing", review_result.get("missing_documents", [])
        ):
            if isinstance(doc, dict):
                missing_documents.append(MissingDocument(**doc))

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
            "blocked": TaxReturnStatus.BLOCKED,
        }
        status = status_map.get(status_str, TaxReturnStatus.PENDING)

        # Collect flagged transactions
        flagged_transactions_summary = self._collect_flagged_transactions(document_analyses)

        if flagged_transactions_summary and flagged_transactions_summary.critical_count > 0:
            if status == TaxReturnStatus.COMPLETE:
                status = TaxReturnStatus.INCOMPLETE
                status_str = "incomplete"

        # 7. Create review object
        tax_return_review = TaxReturnReview(
            tax_return_id=tax_return_id,
            status=status_str,
            documents_processed=len(files),
            documents_analyzed=document_analyses,
            missing_documents=missing_documents,
            blocking_issues=blocking_issues,
            recommendations=review_result.get("recommendations", []),
            completeness_score=review_result.get("completeness_score", 0.0),
            review_summary=review_result.get("summary", review_result.get("review_summary", "")),
            flagged_transactions_summary=flagged_transactions_summary,
        )

        # 8. Update tax return with new review
        tax_return.status = status
        tax_return.review_result = {
            "status": status_str,
            "missing_documents": [m.dict() for m in missing_documents],
            "blocking_issues": blocking_issues,
            "recommendations": review_result.get("recommendations", []),
            "completeness_score": review_result.get("completeness_score", 0.0),
            "summary": review_result.get("summary", review_result.get("review_summary", "")),
            "flagged_transactions_summary": (
                flagged_transactions_summary.dict() if flagged_transactions_summary else None
            ),
        }
        await db.commit()

        return tax_return_review
