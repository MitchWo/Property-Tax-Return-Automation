"""Main document processing orchestration service."""

import asyncio
import logging
import uuid
from typing import Dict, List, Optional, Tuple

from fastapi import UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
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
from app.services.phase1_document_intake.schemas import (
    PL_ROW_MAPPING,
    get_pl_row,
    is_deductible,
    get_extraction_tool_for_document_type,
)
from app.services.phase1_document_intake.extraction_validator import (
    ExtractionValidator,
    get_extraction_validator,
)
from app.services.phase2_feedback_learning.knowledge_store import knowledge_store

logger = logging.getLogger(__name__)

# Financial document types that need structured extraction (Tool Use)
# Any document with amounts should use Tool Use for guaranteed schema compliance
FINANCIAL_DOCUMENT_TYPES = {
    # Transaction-heavy documents (may need batch processing)
    "bank_statement",
    "loan_statement",
    "property_manager_statement",
    # Settlement & purchase documents
    "settlement_statement",
    # Expense documents with amounts
    "body_corporate",
    "rates",
    "water_rates",
    "landlord_insurance",
    "maintenance_invoice",
    "depreciation_schedule",
    "resident_society",
}

# Document types that may need batch processing (many transactions/pages)
BATCH_PROCESSING_TYPES = {"bank_statement", "loan_statement", "property_manager_statement"}


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

    # Status mapping from review result string to TaxReturnStatus enum
    STATUS_MAP = {
        "complete": TaxReturnStatus.COMPLETE,
        "incomplete": TaxReturnStatus.INCOMPLETE,
        "blocked": TaxReturnStatus.BLOCKED,
    }

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

                    # Check for duplicates within this upload and against existing documents
                    dup_info = await self._check_for_duplicates(
                        db, file.filename, content_hash, upload_filenames, upload_hashes, tax_return.id
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
            status = self.STATUS_MAP.get(status_str, TaxReturnStatus.PENDING)

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

            # 6.5. Run cross-document validation if enabled
            cross_validation_result = None
            if settings.ENABLE_EXTRACTION_VERIFICATION:
                try:
                    # Build document list for cross-validation
                    docs_for_validation = []
                    for analysis in document_analyses:
                        doc_data = {
                            "id": str(analysis.document_id),
                            "document_type": analysis.classification.document_type if analysis.classification else None,
                            "filename": analysis.filename,
                            "extracted_data": analysis.extracted_data or {},
                        }
                        # Add tool_use_extraction if available
                        if analysis.classification and analysis.classification.key_details:
                            tool_use = analysis.classification.key_details.get("tool_use_extraction")
                            if tool_use:
                                doc_data["extracted_data"] = tool_use
                        docs_for_validation.append(doc_data)

                    validator = get_extraction_validator()
                    cross_validation_result = validator.cross_validate_documents(docs_for_validation)

                    if not cross_validation_result.get("is_valid"):
                        logger.warning(
                            f"Cross-document validation found discrepancies: "
                            f"{len(cross_validation_result.get('discrepancies', []))} issues"
                        )
                        review_result.setdefault("recommendations", []).append(
                            "Cross-document validation found discrepancies - please review interest and income figures"
                        )

                    review_result["cross_validation"] = cross_validation_result

                except Exception as e:
                    logger.error(f"Cross-document validation failed: {e}")
                    review_result["cross_validation_error"] = str(e)

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

    async def process_tax_return_with_progress(
        self,
        db: AsyncSession,
        tax_return_data: TaxReturnCreate,
        file_contents: List[Dict],
        progress_tracker=None
    ) -> TaxReturnReview:
        """
        Process a complete tax return with real-time progress updates via SSE.

        Args:
            db: Database session
            tax_return_data: Tax return data
            file_contents: List of dicts with filename, content_type, content
            progress_tracker: Optional ProgressTracker for SSE streaming
        """
        async def emit(stage: str, message: str, detail: str = None, sub_progress: float = 0.0):
            if progress_tracker:
                await progress_tracker.emit(stage, message, detail, sub_progress)

        try:
            await emit("initializing", "Starting document processing...", None, 0.5)

            # 1. Create or get client
            await emit("initializing", "Creating client record...", None, 0.8)
            client = await self._get_or_create_client(db, tax_return_data.client_name)

            # 2. Create tax return record
            await emit("loading_documents", "Creating tax return...", None, 0.0)
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

            # 3. Save all files
            await emit("loading_documents", f"Saving {len(file_contents)} files...", None, 0.3)
            saved_files = []
            upload_hashes: Dict[str, Document] = {}
            upload_filenames: Dict[str, Document] = {}
            duplicate_documents = []

            total_files = len(file_contents)
            for idx, file_data in enumerate(file_contents):
                file_progress = idx / total_files
                await emit(
                    "loading_documents",
                    f"Saving file {idx + 1}/{total_files}",
                    file_data["filename"],
                    0.3 + (file_progress * 0.5)
                )

                try:
                    stored_filename, file_path, file_size, content_hash = (
                        await self.file_handler.save_upload_from_bytes(
                            file_data["content"],
                            file_data["filename"],
                            str(tax_return.id)
                        )
                    )

                    # Check for duplicates within this upload and against existing documents
                    dup_info = await self._check_for_duplicates(
                        db, file_data["filename"], content_hash, upload_filenames, upload_hashes, tax_return.id
                    )

                    # Create document record
                    document = Document(
                        tax_return_id=tax_return.id,
                        original_filename=file_data["filename"],
                        stored_filename=stored_filename,
                        file_path=file_path,
                        mime_type=file_data["content_type"] or "application/octet-stream",
                        file_size=file_size,
                        content_hash=content_hash,
                        is_duplicate=dup_info.is_duplicate,
                        duplicate_of_id=dup_info.original_document_id,
                        status=DocumentStatus.PENDING,
                    )
                    db.add(document)
                    await db.commit()
                    await db.refresh(document)

                    if not dup_info.is_duplicate:
                        upload_hashes[content_hash] = document
                        upload_filenames[file_data["filename"]] = document
                        saved_files.append((document, file_data["filename"], dup_info))
                    else:
                        duplicate_documents.append((document, file_data["filename"], dup_info))

                except Exception as e:
                    logger.error(f"Error saving file {file_data['filename']}: {e}")
                    continue

            saved_files.extend(duplicate_documents)
            await emit("loading_documents", f"Saved {len(saved_files)} files", None, 1.0)

            # 4. Process each document with Claude
            await emit("reading_transactions", "Analyzing documents with AI...", None, 0.0)
            document_analyses = []
            document_summaries = []

            context = {
                "client_name": tax_return_data.client_name,
                "property_address": tax_return_data.property_address,
                "tax_year": tax_return_data.tax_year,
                "property_type": tax_return_data.property_type.value,
            }

            total_docs = len(saved_files)
            for idx, (document, original_filename, dup_info) in enumerate(saved_files):
                doc_progress = idx / total_docs
                await emit(
                    "categorizing",
                    f"Analyzing document {idx + 1}/{total_docs}",
                    original_filename,
                    doc_progress
                )

                try:
                    if dup_info.is_duplicate:
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
                    error_summary = DocumentSummary(
                        document_id=document.id,
                        filename=original_filename,
                        document_type="error",
                        key_details={},
                        flags=["processing_error", str(e)],
                    )
                    document_summaries.append(error_summary)

            await emit("categorizing", f"Analyzed {len(document_summaries)} documents", None, 1.0)

            # 5. Run final review
            await emit("generating_summaries", "Running completeness review...", None, 0.3)

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

            await emit("generating_summaries", "Processing review results...", None, 0.7)

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

            status_str = review_result.get("status", "pending")
            status = self.STATUS_MAP.get(status_str, TaxReturnStatus.PENDING)

            flagged_transactions_summary = self._collect_flagged_transactions(document_analyses)

            if flagged_transactions_summary and flagged_transactions_summary.critical_count > 0:
                if status == TaxReturnStatus.COMPLETE:
                    status = TaxReturnStatus.INCOMPLETE
                    status_str = "incomplete"

            # 6.5. Run cross-document validation if enabled
            if settings.ENABLE_EXTRACTION_VERIFICATION:
                await emit("validating", "Running cross-document validation...", None, 0.4)
                try:
                    docs_for_validation = []
                    for analysis in document_analyses:
                        doc_data = {
                            "id": str(analysis.document_id),
                            "document_type": analysis.classification.document_type if analysis.classification else None,
                            "filename": analysis.filename,
                            "extracted_data": analysis.extracted_data or {},
                        }
                        if analysis.classification and analysis.classification.key_details:
                            tool_use = analysis.classification.key_details.get("tool_use_extraction")
                            if tool_use:
                                doc_data["extracted_data"] = tool_use
                        docs_for_validation.append(doc_data)

                    validator = get_extraction_validator()
                    cross_validation_result = validator.cross_validate_documents(docs_for_validation)

                    if not cross_validation_result.get("is_valid"):
                        logger.warning(
                            f"Cross-document validation found discrepancies: "
                            f"{len(cross_validation_result.get('discrepancies', []))} issues"
                        )
                        review_result.setdefault("recommendations", []).append(
                            "Cross-document validation found discrepancies - please review interest and income figures"
                        )

                    review_result["cross_validation"] = cross_validation_result

                except Exception as e:
                    logger.error(f"Cross-document validation failed: {e}")
                    review_result["cross_validation_error"] = str(e)

            await emit("finalizing", "Saving results...", None, 0.5)

            # 7. Create review object
            tax_return_review = TaxReturnReview(
                tax_return_id=tax_return.id,
                status=status_str,
                documents_processed=len(file_contents),
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

            # 8. Update tax return
            tax_return.status = status
            if flagged_transactions_summary:
                review_result["flagged_transactions_summary"] = (
                    flagged_transactions_summary.model_dump()
                )
            tax_return.review_result = review_result
            await db.commit()

            await emit("finalizing", "Saving results...", None, 1.0)

            # Emit complete event with tax_return_id for frontend redirect
            if progress_tracker:
                await progress_tracker.complete(
                    detail=str(tax_return.id),
                    message=f"Processed {len(file_contents)} documents successfully"
                )

            return tax_return_review

        except Exception as e:
            logger.error(f"Error processing tax return with progress: {e}")
            if progress_tracker:
                await progress_tracker.fail(str(e))
            await db.rollback()
            raise

    async def _analyze_document(
        self, db: AsyncSession, document: Document, original_filename: str, context: dict,
        progress_tracker=None
    ) -> Tuple[DocumentAnalysis, DocumentSummary]:
        """Analyze a single document with Claude."""

        # Process file content
        processed = await self.file_handler.process_file(document.file_path, original_filename)

        # Determine if this is a financial document that needs batch processing
        total_pages = len(processed.image_paths) if processed.image_paths else 0
        batch_size = settings.FINANCIAL_DOC_BATCH_SIZE

        # For initial classification, use first 5 pages
        image_data = None
        if processed.image_paths:
            image_data = await self.claude_client.prepare_image_data(
                processed.image_paths[:5]  # First 5 pages for classification
            )

        # Fetch relevant learnings from ALL Pinecone namespaces
        transaction_learnings = []
        skill_learnings = []
        try:
            # Search across all relevant namespaces for comprehensive context
            # 1. Document-specific learnings (document classification patterns)
            doc_learnings = await knowledge_store.search_for_document_processing(
                document_type=None,  # Will be determined during classification
                context=f"{context.get('property_address', '')} {context.get('client_name', '')}",
                top_k=5
            )

            # 2. Transaction-related learnings (for financial documents)
            transaction_learnings = await knowledge_store.search_for_categorization(
                description="transaction rental property expense",
                other_party=None,
                top_k=10
            )

            # 3. Skill learnings (tax rules, teachings, domain knowledge)
            skill_learnings = await knowledge_store.search(
                query="NZ rental property tax rules deductibility interest insurance rates",
                top_k=5,
                min_score=0.3,
                namespace="skill_learnings"
            )

            # Combine all learnings
            all_learnings = doc_learnings + transaction_learnings + skill_learnings

            # Deduplicate by ID while preserving order
            seen_ids = set()
            unique_learnings = []
            for learning in all_learnings:
                learning_id = learning.get("id")
                if learning_id and learning_id not in seen_ids:
                    seen_ids.add(learning_id)
                    unique_learnings.append(learning)

            transaction_learnings = unique_learnings
            if transaction_learnings:
                logger.info(
                    f"Found {len(transaction_learnings)} relevant learnings from RAG "
                    f"(doc: {len(doc_learnings)}, txn: {len(transaction_learnings)}, skill: {len(skill_learnings)})"
                )
        except Exception as e:
            logger.warning(f"Could not fetch learnings from RAG: {e}")

        # Analyze with Claude, passing any learnings context
        classification = await self.claude_client.analyze_document(
            processed.text_content, image_data, context, transaction_learnings=transaction_learnings
        )

        # Check if this is a financial document that needs structured extraction
        is_financial_doc = classification.document_type in FINANCIAL_DOCUMENT_TYPES
        needs_batch_processing = (
            settings.ENABLE_BATCH_PROCESSING
            and classification.document_type in BATCH_PROCESSING_TYPES
            and total_pages > batch_size
        )

        # Process transaction analysis if present (for financial documents)
        transaction_analysis = classification.key_details.get("transaction_analysis")
        all_transactions = []
        tool_use_extraction = None

        if needs_batch_processing:
            # Multi-page transaction documents - process in batches
            logger.info(
                f"Document {original_filename} has {total_pages} pages, "
                f"processing in batches of {batch_size}"
            )

            # Process all pages in batches
            batch_results = await self._process_financial_document_batches(
                db=db,
                document=document,
                processed=processed,
                context=context,
                batch_size=batch_size,
                progress_tracker=progress_tracker,
            )

            # Merge batch results
            all_transactions = batch_results.get("all_transactions", [])

            # Update classification with batch results
            classification.key_details["transaction_analysis"] = {
                "transactions": all_transactions,
                "total_extracted": len(all_transactions),
                "pages_processed": total_pages,
                "batches_used": batch_results.get("batches_processed", 1),
                "extraction_warnings": batch_results.get("warnings", []),
            }

            if batch_results.get("interest_analysis"):
                classification.key_details["interest_analysis"] = batch_results["interest_analysis"]

            # Store Tool Use extraction data
            tool_use_extraction = batch_results

        elif is_financial_doc and settings.ENABLE_TOOL_USE:
            # Financial document (any size) - use Tool Use for structured extraction
            extraction_tool = get_extraction_tool_for_document_type(classification.document_type)
            if extraction_tool:
                logger.info(
                    f"Using Tool Use extraction for {classification.document_type}: {original_filename}"
                )
                try:
                    if progress_tracker:
                        await progress_tracker.emit(
                            "extracting_batch",
                            f"Extracting {classification.document_type} data...",
                            detail=original_filename,
                            sub_progress=0.5,
                        )

                    tool_use_extraction = await self.claude_client.analyze_document_with_tool_use(
                        processed.text_content,
                        image_data,
                        context,
                        extraction_tool,
                    )

                    # Store the structured extraction in key_details
                    classification.key_details["tool_use_extraction"] = tool_use_extraction
                    classification.key_details["extraction_schema"] = extraction_tool["name"]

                    # Extract transactions if present in the tool output
                    if "transactions" in tool_use_extraction:
                        all_transactions = tool_use_extraction.get("transactions", [])
                        classification.key_details["transaction_analysis"] = {
                            "transactions": all_transactions,
                            "total_extracted": len(all_transactions),
                            "pages_processed": total_pages,
                        }

                    logger.info(
                        f"Tool Use extraction complete for {classification.document_type}"
                    )

                    # Run extraction validation if enabled
                    if settings.ENABLE_EXTRACTION_VERIFICATION:
                        validator = get_extraction_validator(self.claude_client)
                        validation_result = await validator.validate_extraction(
                            document_type=classification.document_type,
                            extracted_data=tool_use_extraction,
                            document_content=processed.text_content,
                            image_data=image_data,
                            context=context,
                            run_verification_pass=True,
                        )

                        # Store validation results
                        classification.key_details["validation"] = validation_result.to_dict()

                        if not validation_result.is_valid:
                            logger.warning(
                                f"Extraction validation failed for {original_filename}: "
                                f"{len(validation_result.issues)} issues found"
                            )
                            classification.flags.append("extraction_validation_failed")

                            # If there are suggested corrections, attempt to merge them
                            if validation_result.suggested_corrections:
                                logger.info(
                                    f"Applying {len(validation_result.suggested_corrections)} "
                                    f"suggested corrections from verification pass"
                                )
                                existing_txns = tool_use_extraction.get("transactions", [])
                                for correction in validation_result.suggested_corrections:
                                    existing_txns.append({
                                        "date": correction.get("date"),
                                        "description": correction.get("description"),
                                        "amount": correction.get("amount"),
                                        "transaction_type": correction.get("transaction_type"),
                                        "categorization": {
                                            "suggested_category": correction.get("suggested_category", "unknown"),
                                            "confidence": 0.7,
                                            "is_deductible": True,
                                        },
                                        "review_flags": {
                                            "needs_review": True,
                                            "reasons": ["added_by_verification_pass"],
                                            "severity": "info",
                                        },
                                    })
                                tool_use_extraction["transactions"] = existing_txns

                        if validation_result.reconciliation:
                            classification.key_details["reconciliation"] = validation_result.reconciliation
                            if not validation_result.reconciliation.get("is_reconciled"):
                                classification.flags.append("balance_not_reconciled")

                except Exception as e:
                    logger.warning(f"Tool Use extraction failed, using standard extraction: {e}")
                    # Fall back to standard extraction (already done above)

        elif transaction_analysis:
            all_transactions = transaction_analysis.get("transactions", [])

        # Flag transactions that need review
        if transaction_analysis or all_transactions:
            flagged_transactions = [t for t in all_transactions if t.get("needs_review")]
            flagged_count = len(flagged_transactions)
            if flagged_count > 0:
                classification.flags.append(f"flagged_transactions_{flagged_count}")
            if any(t.get("requires_invoice") for t in all_transactions):
                classification.flags.append("requires_source_documents")

        # Update document record
        document.document_type = classification.document_type
        document.classification_confidence = classification.confidence
        document.extracted_data = {
            "reasoning": classification.reasoning,
            "flags": classification.flags,
            "key_details": classification.key_details,
            "pages_processed": total_pages,
            "batch_processing_used": needs_batch_processing,
            "tool_use_enabled": settings.ENABLE_TOOL_USE and is_financial_doc,
        }

        # Add Tool Use extraction data if available
        if tool_use_extraction:
            document.extracted_data["tool_use_extraction"] = tool_use_extraction
            if extraction_tool := get_extraction_tool_for_document_type(classification.document_type):
                document.extracted_data["extraction_schema"] = extraction_tool["name"]

            # Populate new metadata columns
            if isinstance(tool_use_extraction, dict):
                extraction_meta = tool_use_extraction.get("extraction_metadata", {})
                document.pages_processed = extraction_meta.get("pages_processed", total_pages)
                document.extraction_batches = extraction_meta.get("total_batches", 1)
                document.data_quality_score = extraction_meta.get("data_quality_score")
                document.verification_status = "extracted"

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

    async def _process_financial_document_batches(
        self,
        db: AsyncSession,
        document: Document,
        processed,
        context: dict,
        batch_size: int,
        progress_tracker=None,
    ) -> Dict:
        """
        Process a financial document in batches to extract all transactions.

        Args:
            db: Database session
            document: Document record
            processed: ProcessedFile with image_paths
            context: Property/client context
            batch_size: Number of pages per batch
            progress_tracker: Optional progress tracker for SSE updates

        Returns:
            Dict with all_transactions, interest_analysis, warnings
        """
        all_transactions = []
        interest_analysis = {
            "total_interest_debits": 0,
            "total_interest_credits": 0,
            "interest_transactions": [],
        }
        warnings = []
        failed_batches = []

        total_pages = len(processed.image_paths)
        total_batches = (total_pages + batch_size - 1) // batch_size
        previous_balance = None

        logger.info(f"Processing {total_pages} pages in {total_batches} batches")

        for batch_num in range(total_batches):
            start_idx = batch_num * batch_size
            end_idx = min(start_idx + batch_size, total_pages)
            batch_pages = processed.image_paths[start_idx:end_idx]

            try:
                # Update progress if tracker available
                if progress_tracker:
                    progress = int(15 + (batch_num / total_batches) * 45)  # 15-60% for extraction
                    await progress_tracker.emit(
                        stage="extracting_batch",
                        message=f"Extracting transactions (batch {batch_num + 1}/{total_batches})",
                        detail=f"Processing pages {start_idx + 1}-{end_idx}",
                        sub_progress=batch_num / total_batches,
                    )

                # Prepare image data for this batch
                image_data = await self.claude_client.prepare_image_data(batch_pages)

                # Extract transactions for this batch
                batch_result = await self.claude_client.extract_bank_statement_batch(
                    text_content=processed.text_content,
                    image_data=image_data,
                    context=context,
                    batch_info={
                        "batch": batch_num + 1,
                        "total": total_batches,
                        "previous_balance": previous_balance,
                    },
                )

                # Extract transactions from result
                batch_transactions = batch_result.get("transactions", [])
                all_transactions.extend(batch_transactions)

                # Update previous balance for next batch
                statement_period = batch_result.get("statement_period", {})
                if statement_period.get("closing_balance"):
                    previous_balance = statement_period["closing_balance"]

                # Aggregate interest analysis
                batch_interest = batch_result.get("interest_analysis", {})
                if batch_interest:
                    interest_analysis["total_interest_debits"] += batch_interest.get(
                        "total_interest_debits", 0
                    )
                    interest_analysis["total_interest_credits"] += batch_interest.get(
                        "total_interest_credits", 0
                    )

                logger.info(
                    f"Batch {batch_num + 1}/{total_batches}: "
                    f"extracted {len(batch_transactions)} transactions"
                )

                # Delay between batches to avoid rate limits
                if batch_num < total_batches - 1:
                    await asyncio.sleep(settings.BATCH_DELAY_SECONDS)

            except Exception as e:
                logger.error(f"Batch {batch_num + 1} failed: {e}")
                failed_batches.append({
                    "batch": batch_num + 1,
                    "pages": f"{start_idx + 1}-{end_idx}",
                    "error": str(e),
                })
                warnings.append(
                    f"Batch {batch_num + 1} (pages {start_idx + 1}-{end_idx}) failed: {str(e)}"
                )
                # Continue with next batch
                continue

        # Deduplicate transactions
        deduplicated = self._deduplicate_transactions(all_transactions)

        # Update progress
        if progress_tracker:
            await progress_tracker.emit(
                stage="merging_batches",
                message="Merging batch results",
                detail=f"Extracted {len(deduplicated)} unique transactions",
                sub_progress=1.0,
            )

        return {
            "all_transactions": deduplicated,
            "interest_analysis": interest_analysis,
            "batches_processed": total_batches,
            "failed_batches": failed_batches,
            "warnings": warnings,
            "partial_extraction": len(failed_batches) > 0,
        }

    def _deduplicate_transactions(self, transactions: List[Dict]) -> List[Dict]:
        """
        Remove duplicate transactions based on date, amount, and description.

        Returns transactions sorted by date.
        """
        seen = set()
        unique = []

        for txn in transactions:
            # Create a unique key from date, amount, and description
            key = (
                txn.get("date", ""),
                txn.get("amount", 0),
                txn.get("description", "")[:50],  # First 50 chars of description
            )

            if key not in seen:
                seen.add(key)
                unique.append(txn)

        # Sort by date
        unique.sort(key=lambda x: x.get("date", ""))

        return unique

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
        tax_return_id: Optional[uuid.UUID] = None,
    ) -> DuplicateInfo:
        """
        Check if a file is a duplicate.

        Checks for duplicates both:
        1. Within the current upload (using upload_filenames and upload_hashes dicts)
        2. Against existing documents in the database for the same tax return

        Args:
            db: Database session
            filename: Original filename
            content_hash: SHA-256 hash of file content
            upload_filenames: Dict of filenames already in this upload
            upload_hashes: Dict of content hashes already in this upload
            tax_return_id: Tax return ID to check against existing documents

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

        # Check against existing documents in the database for the same tax return
        if tax_return_id:
            # Check by content hash first (most reliable)
            result = await db.execute(
                select(Document).where(
                    Document.tax_return_id == tax_return_id,
                    Document.content_hash == content_hash,
                    Document.is_duplicate.is_(False),  # Don't match against other duplicates
                )
            )
            existing_hash_match = result.scalar_one_or_none()

            if existing_hash_match:
                logger.info(
                    f"Duplicate detected: '{filename}' has same content as existing document "
                    f"'{existing_hash_match.original_filename}' (content hash match)"
                )
                return DuplicateInfo(
                    is_duplicate=True,
                    duplicate_type="content",
                    original_document_id=existing_hash_match.id,
                    original_filename=existing_hash_match.original_filename,
                )

            # Also check by filename for potential duplicates
            result = await db.execute(
                select(Document).where(
                    Document.tax_return_id == tax_return_id,
                    Document.original_filename == filename,
                    Document.is_duplicate.is_(False),
                )
            )
            existing_filename_match = result.scalar_one_or_none()

            if existing_filename_match:
                logger.info(
                    f"Potential duplicate detected: '{filename}' has same filename as existing document "
                    f"(different content - may be updated version)"
                )
                # Note: Same filename but different content could be an updated version
                # We flag it but still process it - user can review
                return DuplicateInfo(
                    is_duplicate=True,
                    duplicate_type="filename",
                    original_document_id=existing_filename_match.id,
                    original_filename=existing_filename_match.original_filename,
                )

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

        # 2. Save new files - check for duplicates against existing documents
        saved_files = []
        duplicate_documents = []
        upload_filenames: Dict[str, Document] = {}
        upload_hashes: Dict[str, Document] = {}

        for file in files:
            try:
                stored_filename, file_path, file_size, content_hash = (
                    await self.file_handler.save_upload(file, str(tax_return_id))
                )

                # Check for duplicates against existing documents in this tax return
                dup_info = await self._check_for_duplicates(
                    db, file.filename, content_hash, upload_filenames, upload_hashes, tax_return_id
                )

                document = Document(
                    tax_return_id=tax_return_id,
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

                if dup_info.is_duplicate and dup_info.duplicate_type == "content":
                    # Same content - skip analysis, use existing document's data
                    duplicate_documents.append((document, file.filename, dup_info))
                    logger.warning(
                        f"Skipping duplicate document: '{file.filename}' has same content as "
                        f"'{dup_info.original_filename}' - transactions already extracted"
                    )
                else:
                    # New document or different content - analyze it
                    upload_hashes[content_hash] = document
                    upload_filenames[file.filename] = document
                    saved_files.append((document, file.filename, dup_info))
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
        status = self.STATUS_MAP.get(status_str, TaxReturnStatus.PENDING)

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
            "missing_documents": [m.model_dump() for m in missing_documents],
            "blocking_issues": blocking_issues,
            "recommendations": review_result.get("recommendations", []),
            "completeness_score": review_result.get("completeness_score", 0.0),
            "summary": review_result.get("summary", review_result.get("review_summary", "")),
            "flagged_transactions_summary": (
                flagged_transactions_summary.model_dump() if flagged_transactions_summary else None
            ),
        }
        await db.commit()

        return tax_return_review
