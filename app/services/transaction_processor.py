"""Transaction processing integration layer.

Orchestrates the flow: extraction → categorization → storage

OPTIMIZED FLOW (Phase 1 → Phase 2):
1. Phase 1 extracts ALL transactions during document classification
2. Phase 2 reads pre-extracted transactions from Document.extracted_data
3. Phase 1 feedback (user corrections) is applied before categorization
4. RAG patterns from transaction-coding namespace are queried
5. Only falls back to Claude extraction if no pre-extracted data exists

Note: Phase 2 combines the original Phase 2 (Knowledge & Learning) and Phase 3
(Transaction Processing) into a unified workflow.
"""
import logging
from typing import List, Optional, Dict, Any
from uuid import UUID
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete
from decimal import Decimal

from app.models.db_models import (
    Document, TaxReturn, Transaction, TransactionSummary,
    TransactionPattern, CategoryFeedback
)
from app.schemas.transactions import (
    TransactionResponse,
    TransactionSummaryResponse, ProcessingResult, ExtractedTransaction
)
from app.services.transaction_extractor_claude import TransactionExtractorClaude as TransactionExtractor
from app.services.transaction_categorizer import TransactionCategorizer
from app.services.tax_rules_service import TaxRulesService
from app.services.phase2_feedback_learning.knowledge_store import knowledge_store

logger = logging.getLogger(__name__)


class TransactionProcessor:
    """Orchestrates transaction processing pipeline."""

    def __init__(self):
        self.extractor = TransactionExtractor()
        self.categorizer = TransactionCategorizer()
        self.tax_service = TaxRulesService()

    def _apply_category_result(self, transaction: Transaction, category_result) -> None:
        """Apply categorization result attributes to a transaction.

        Handles both object and dict-like category results with safe attribute access.
        """
        transaction.category_code = category_result.category_code
        transaction.gst_inclusive = getattr(category_result, 'gst_inclusive', False)
        transaction.deductible_percentage = getattr(category_result, 'deductible_percentage', 100.0)
        transaction.categorization_source = getattr(category_result, 'categorization_source', 'unknown')
        transaction.categorization_trace = getattr(category_result, 'categorization_trace', None)
        transaction.confidence = getattr(category_result, 'confidence', 0.0)
        transaction.needs_review = getattr(category_result, 'needs_review', True)

        # Truncate review_reason as safety fallback (even though DB now supports TEXT)
        review_reason = getattr(category_result, 'review_reason', None)
        if review_reason and len(review_reason) > 10000:  # Safety limit for extremely long text
            review_reason = review_reason[:9997] + "..."
        transaction.review_reason = review_reason

    def _apply_tax_result(self, transaction: Transaction, tax_result) -> None:
        """Apply tax rules result to a transaction.

        Handles both dict and object tax results.
        """
        if isinstance(tax_result, dict):
            transaction.deductible_percentage = tax_result.get('deductible_percentage', transaction.deductible_percentage)
            transaction.gst_inclusive = tax_result.get('gst_inclusive', transaction.gst_inclusive)
        else:
            transaction.deductible_percentage = getattr(tax_result, 'deductible_percentage', transaction.deductible_percentage)
            transaction.gst_inclusive = getattr(tax_result, 'gst_inclusive', transaction.gst_inclusive)

    async def process_document(
        self,
        db: AsyncSession,
        document_id: UUID,
        tax_return_id: UUID,
        force_reprocess: bool = False,
        document_context: Optional[Dict[str, Any]] = None
    ) -> ProcessingResult:
        """
        Process a document through the full pipeline.

        OPTIMIZED: First checks for pre-extracted transactions from Phase 1.
        Only falls back to Claude extraction if no pre-extracted data exists.

        Args:
            db: Database session
            document_id: Document to process
            tax_return_id: Associated tax return
            force_reprocess: Whether to reprocess existing transactions
            document_context: Cross-document context (loan accounts, client names, etc.)

        Returns:
            ProcessingResult with transaction details
        """
        from pathlib import Path

        try:
            # Get document and tax return
            document = await db.get(Document, document_id)
            if not document:
                raise ValueError(f"Document {document_id} not found")

            tax_return = await db.get(TaxReturn, tax_return_id)
            if not tax_return:
                raise ValueError(f"Tax return {tax_return_id} not found")

            # IMPORTANT: Store file_path in a variable to use after session operations
            doc_file_path = document.file_path
            doc_filename = document.original_filename

            # Check if already processed
            if not force_reprocess:
                existing = await db.execute(
                    select(Transaction).where(
                        Transaction.document_id == document_id
                    ).limit(1)
                )
                if existing.scalar_one_or_none():
                    logger.info(f"Document {document_id} already processed, skipping")
                    return await self._get_processing_result(db, document_id, tax_return_id)

            # === OPTIMIZATION: Check for pre-extracted transactions from Phase 1 ===
            extracted_transactions = await self._get_phase1_transactions(document)

            # Get any user feedback from Phase 1
            phase1_feedback = await self._get_phase1_feedback(db, document_id)

            # Query RAG for learned transaction patterns
            rag_patterns = await self._get_rag_patterns(db)

            if extracted_transactions:
                logger.info(f"Using {len(extracted_transactions)} pre-extracted transactions from Phase 1")
                extraction_result = type('ExtractionResult', (), {'transactions': extracted_transactions})()
            else:
                # Fallback: Extract using Claude (legacy path)
                logger.info("No pre-extracted transactions found, falling back to Claude extraction")

                # Load file content if it's a CSV
                file_content = None
                if doc_filename.lower().endswith('.csv') and doc_file_path:
                    try:
                        file_path = Path(doc_file_path)
                        if file_path.exists():
                            file_content = file_path.read_bytes()
                            logger.info(f"Loaded CSV file content: {len(file_content)} bytes from {doc_file_path}")
                        else:
                            logger.warning(f"File path not found: {doc_file_path}")
                    except Exception as e:
                        logger.error(f"Failed to load file content: {e}")

                extraction_result = await self.extractor.extract_from_document(
                    document, tax_return, file_content=file_content
                )

            if not extraction_result or not extraction_result.transactions:
                logger.warning(f"No transactions extracted from document {document_id}")
                return ProcessingResult(
                    success=False,
                    message="No transactions found in document",
                    transactions=[],
                    summary=None
                )

            # === Apply Phase 1 feedback and RAG patterns before categorization ===
            if phase1_feedback or rag_patterns:
                extraction_result.transactions = await self._apply_phase1_feedback(
                    extraction_result.transactions,
                    phase1_feedback,
                    rag_patterns
                )
                logger.info("Applied Phase 1 feedback and RAG patterns to transactions")

            # Batch categorize all transactions for efficiency
            # Note: Transactions with high-confidence categories from Phase 1/RAG will be used as-is
            logger.info(f"Categorizing {len(extraction_result.transactions)} transactions in batches...")
            categorized_results = await self.categorizer.categorize_batch(
                db, extraction_result.transactions, tax_return, use_claude=True,
                document_context=document_context
            )

            # Store and process each transaction with its categorization
            processed_transactions = []
            for trans_data, category_result in zip(extraction_result.transactions, categorized_results):
                # Create transaction record
                transaction = Transaction(
                    tax_return_id=tax_return_id,
                    document_id=document_id,
                    transaction_date=trans_data.transaction_date,
                    description=trans_data.description,
                    other_party=trans_data.other_party,
                    amount=trans_data.amount,
                    balance=trans_data.balance,
                    # Set initial categorization fields
                    confidence=trans_data.confidence,
                    categorization_source='extraction'
                )

                # Apply categorization result
                self._apply_category_result(transaction, category_result)

                # Apply tax rules
                tax_result = await self.tax_service.apply_tax_rules(
                    db, transaction, tax_return
                )
                self._apply_tax_result(transaction, tax_result)

                db.add(transaction)
                processed_transactions.append(transaction)

            # Commit transactions
            await db.commit()

            # Generate summary (currently returns list of per-category summaries)
            summaries = await self._generate_summary(db, tax_return_id)
            # Note: ProcessingResult expects a single summary; using first if multiple exist
            summary = summaries[0] if summaries else None

            # Update document status
            document.processing_status = 'completed'
            document.processed_at = datetime.utcnow()
            await db.commit()

            logger.info(f"Successfully processed {len(processed_transactions)} transactions")

            return ProcessingResult(
                success=True,
                message=f"Processed {len(processed_transactions)} transactions",
                transactions=[
                    TransactionResponse.model_validate(t)
                    for t in processed_transactions
                ],
                summary=summary
            )

        except Exception as e:
            logger.error(f"Error processing document: {e}")
            await db.rollback()

            # Update document with error status
            if document:
                document.processing_status = 'error'
                document.error_message = str(e)
                await db.commit()

            return ProcessingResult(
                success=False,
                message=f"Processing failed: {str(e)}",
                transactions=[],
                summary=None
            )

    # Define document types that contain transactions
    TRANSACTION_DOC_TYPES = [
        "bank_statement",
        "loan_statement",
        "property_manager_statement",
        "settlement_statement",
        "depreciation_schedule",
        "body_corporate",
        "rates",
        "landlord_insurance",
        "maintenance_invoice",
    ]

    async def process_tax_return_transactions(
        self,
        db: AsyncSession,
        tax_return_id: UUID,
        use_claude: bool = True
    ) -> ProcessingResult:
        """
        Process all transaction-bearing documents for a tax return.

        Args:
            db: Database session
            tax_return_id: Tax return ID
            use_claude: Whether to use Claude for categorization

        Returns:
            ProcessingResult with aggregated results
        """
        try:
            # Get all transaction-bearing documents for this tax return
            result = await db.execute(
                select(Document).where(
                    Document.tax_return_id == tax_return_id,
                    Document.document_type.in_(self.TRANSACTION_DOC_TYPES)
                )
            )
            documents = result.scalars().all()

            if not documents:
                return ProcessingResult(
                    success=False,
                    message="No transaction documents found",
                    total_transactions=0,
                    transactions_categorized=0,
                    transactions_needing_review=0,
                    documents_processed=[],
                    blocking_issues=["No transaction documents (bank/loan/property manager statements) uploaded"]
                )

            all_transactions = []
            documents_processed = []
            blocking_issues = []

            # Process each document
            for document in documents:
                try:
                    doc_result = await self.process_document(
                        db=db,
                        document_id=document.id,
                        tax_return_id=tax_return_id,
                        force_reprocess=False
                    )

                    if doc_result.success:
                        all_transactions.extend(doc_result.transactions)
                        documents_processed.append(str(document.id))
                    else:
                        blocking_issues.append(f"Document {document.original_filename}: {doc_result.message}")

                except Exception as e:
                    logger.error(f"Failed to process document {document.id}: {e}")
                    blocking_issues.append(f"Document {document.original_filename}: {str(e)}")

            # Count categorized transactions
            categorized = sum(1 for t in all_transactions if t.category_code)
            needs_review = len(all_transactions) - categorized

            return ProcessingResult(
                success=len(all_transactions) > 0,
                message=f"Processed {len(documents_processed)} documents, {len(all_transactions)} transactions",
                total_transactions=len(all_transactions),
                transactions_categorized=categorized,
                transactions_needing_review=needs_review,
                documents_processed=documents_processed,
                blocking_issues=blocking_issues if blocking_issues else None
            )

        except Exception as e:
            logger.error(f"Failed to process tax return transactions: {e}")
            return ProcessingResult(
                success=False,
                message=str(e),
                total_transactions=0,
                transactions_categorized=0,
                transactions_needing_review=0,
                documents_processed=[],
                blocking_issues=[str(e)]
            )

    async def process_tax_return_transactions_with_progress(
        self,
        db: AsyncSession,
        tax_return_id: UUID,
        use_claude: bool = True,
        progress_tracker=None
    ) -> ProcessingResult:
        """
        Process all transaction-bearing documents with real-time progress updates.

        Args:
            db: Database session
            tax_return_id: Tax return ID
            use_claude: Whether to use Claude for categorization
            progress_tracker: Optional ProgressTracker for SSE streaming

        Returns:
            ProcessingResult with aggregated results
        """

        async def emit(stage: str, message: str, detail: str = None, sub_progress: float = 0.0):
            if progress_tracker:
                await progress_tracker.emit(stage, message, detail, sub_progress)

        try:
            await emit("initializing", "Starting transaction processing...", None, 0.5)

            # Get all transaction-bearing documents for this tax return
            await emit("loading_documents", "Loading documents...", None, 0.0)

            result = await db.execute(
                select(Document).where(
                    Document.tax_return_id == tax_return_id,
                    Document.document_type.in_(self.TRANSACTION_DOC_TYPES)
                )
            )
            documents = result.scalars().all()

            if not documents:
                await emit("error", "No transaction documents found")
                if progress_tracker:
                    await progress_tracker.fail("No transaction documents found")
                return ProcessingResult(
                    success=False,
                    message="No transaction documents found",
                    total_transactions=0,
                    transactions_categorized=0,
                    transactions_needing_review=0,
                    documents_processed=[],
                    blocking_issues=["No transaction documents (bank/loan/property manager statements) uploaded"]
                )

            await emit("loading_documents", f"Found {len(documents)} documents", None, 1.0)

            # Build cross-document context for intelligent categorization
            await emit("loading_documents", "Building document context...", None, 0.8)
            document_context = await self._build_document_context(db, tax_return_id)
            logger.info(f"Document context: {document_context}")

            all_transactions = []
            documents_processed = []
            blocking_issues = []

            # Process each document with progress updates
            total_docs = len(documents)
            for idx, document in enumerate(documents):
                doc_progress = idx / total_docs

                await emit(
                    "reading_transactions",
                    f"Processing document {idx + 1}/{total_docs}",
                    document.original_filename,
                    doc_progress
                )

                try:
                    # Check for pre-extracted transactions from Phase 1
                    extracted_transactions = await self._get_phase1_transactions(document)

                    if extracted_transactions:
                        await emit(
                            "reading_transactions",
                            f"Found {len(extracted_transactions)} pre-extracted transactions",
                            document.original_filename,
                            doc_progress + (0.5 / total_docs)
                        )
                    else:
                        await emit(
                            "reading_transactions",
                            "Extracting transactions from document...",
                            document.original_filename,
                            doc_progress + (0.3 / total_docs)
                        )

                    doc_result = await self.process_document(
                        db=db,
                        document_id=document.id,
                        tax_return_id=tax_return_id,
                        force_reprocess=False,
                        document_context=document_context
                    )

                    if doc_result.success:
                        all_transactions.extend(doc_result.transactions)
                        documents_processed.append(str(document.id))
                        # Emit progress after each document to keep SSE connection alive
                        await emit(
                            "categorizing",
                            f"Processed {len(documents_processed)}/{total_docs} documents ({len(all_transactions)} transactions)",
                            document.original_filename,
                            (idx + 1) / total_docs
                        )
                    else:
                        blocking_issues.append(f"Document {document.original_filename}: {doc_result.message}")

                except Exception as e:
                    logger.error(f"Failed to process document {document.id}: {e}")
                    blocking_issues.append(f"Document {document.original_filename}: {str(e)}")

            # Apply feedback stage
            await emit("applying_feedback", "Applying Phase 1 corrections...", None, 0.5)

            # Query RAG patterns
            await emit("querying_rag", "Searching learned patterns...", None, 0.5)

            # Categorization progress (already done in process_document, but show stage)
            total_txns = len(all_transactions)
            await emit(
                "categorizing",
                f"Categorized {total_txns} transactions",
                None,
                1.0
            )

            # Apply tax rules
            await emit("applying_tax_rules", "Applying tax rules...", None, 0.5)

            # Generate summaries
            await emit("generating_summaries", "Generating transaction summaries...", None, 0.5)
            summaries = await self._generate_summary(db, tax_return_id)
            await emit("generating_summaries", f"Created {len(summaries)} category summaries", None, 1.0)

            # Finalize
            await emit("finalizing", "Saving results...", None, 0.5)

            # Count categorized transactions
            categorized = sum(1 for t in all_transactions if t.category_code)
            needs_review = sum(1 for t in all_transactions if t.needs_review)

            await emit("finalizing", "Saving results...", None, 1.0)

            # Emit complete event with tax_return_id for frontend redirect
            if progress_tracker:
                await progress_tracker.complete(
                    detail=str(tax_return_id),
                    message=f"Processed {len(documents_processed)} documents, {total_txns} transactions"
                )

            return ProcessingResult(
                success=len(all_transactions) > 0,
                message=f"Processed {len(documents_processed)} documents, {total_txns} transactions",
                total_transactions=total_txns,
                transactions_categorized=categorized,
                transactions_needing_review=needs_review,
                documents_processed=documents_processed,
                blocking_issues=blocking_issues if blocking_issues else None
            )

        except Exception as e:
            logger.error(f"Failed to process tax return transactions: {e}")
            if progress_tracker:
                await progress_tracker.fail(str(e))
            return ProcessingResult(
                success=False,
                message=str(e),
                total_transactions=0,
                transactions_categorized=0,
                transactions_needing_review=0,
                documents_processed=[],
                blocking_issues=[str(e)]
            )

    async def reprocess_transactions(
        self,
        db: AsyncSession,
        tax_return_id: UUID,
        transaction_ids: Optional[List[UUID]] = None
    ) -> ProcessingResult:
        """
        Reprocess transactions with updated rules/patterns.

        Args:
            db: Database session
            tax_return_id: Tax return to reprocess
            transaction_ids: Specific transactions to reprocess (None = all)

        Returns:
            ProcessingResult with updated transactions
        """
        try:
            # Get tax return
            tax_return = await db.get(TaxReturn, tax_return_id)
            if not tax_return:
                raise ValueError(f"Tax return {tax_return_id} not found")

            # Get transactions to reprocess
            query = select(Transaction).where(
                Transaction.tax_return_id == tax_return_id
            )
            if transaction_ids:
                query = query.where(Transaction.id.in_(transaction_ids))

            result = await db.execute(query)
            transactions = result.scalars().all()

            if not transactions:
                return ProcessingResult(
                    success=False,
                    message="No transactions to reprocess",
                    transactions=[],
                    summary=None
                )

            # Reprocess each transaction
            updated_transactions = []
            for transaction in transactions:
                # Convert Transaction model to ExtractedTransaction for categorizer
                from app.schemas.transactions import ExtractedTransaction
                extracted = ExtractedTransaction(
                    transaction_date=transaction.transaction_date,
                    description=transaction.description,
                    other_party=transaction.other_party,
                    amount=transaction.amount,
                    balance=transaction.balance,
                    raw_data=getattr(transaction, 'raw_data', {}),
                    confidence=transaction.confidence,
                    suggested_category=transaction.category_code,
                    needs_review=transaction.needs_review,
                    review_reason=transaction.review_reason
                )

                # Re-categorize
                category_result = await self.categorizer.categorize_transaction(
                    db, extracted, tax_return
                )

                # Apply categorization result
                self._apply_category_result(transaction, category_result)

                # Reapply tax rules
                tax_result = await self.tax_service.apply_tax_rules(
                    db, transaction, tax_return
                )
                self._apply_tax_result(transaction, tax_result)

                updated_transactions.append(transaction)

            # Commit updates
            await db.commit()

            # Regenerate summary
            summaries = await self._generate_summary(db, tax_return_id)
            summary = summaries[0] if summaries else None

            logger.info(f"Reprocessed {len(updated_transactions)} transactions")

            return ProcessingResult(
                success=True,
                message=f"Reprocessed {len(updated_transactions)} transactions",
                transactions=[
                    TransactionResponse.model_validate(t)
                    for t in updated_transactions
                ],
                summary=summary
            )

        except Exception as e:
            logger.error(f"Error reprocessing transactions: {e}")
            await db.rollback()
            return ProcessingResult(
                success=False,
                message=f"Reprocessing failed: {str(e)}",
                transactions=[],
                summary=None
            )

    async def learn_from_feedback(
        self,
        db: AsyncSession,
        transaction_id: UUID,
        correct_category: str,
        confidence_adjustment: float = 0.1
    ) -> bool:
        """
        Learn from user correction and create/update patterns.

        Args:
            db: Database session
            transaction_id: Transaction that was corrected
            correct_category: The correct category code
            confidence_adjustment: How much to adjust pattern confidence

        Returns:
            True if learning successful
        """
        try:
            # Get transaction
            transaction = await db.get(Transaction, transaction_id)
            if not transaction:
                logger.error(f"Transaction {transaction_id} not found")
                return False

            # Record feedback
            feedback = CategoryFeedback(
                transaction_id=transaction_id,
                original_category=transaction.category_code,
                corrected_category=correct_category,
                feedback_type='correction'
            )
            db.add(feedback)

            # Update transaction
            transaction.category_code = correct_category
            transaction.needs_review = False
            transaction.manually_reviewed = True

            # Learn pattern if high confidence correction
            if transaction.confidence < 0.7:
                # Look for existing pattern
                result = await db.execute(
                    select(TransactionPattern).where(
                        TransactionPattern.other_party_normalized == transaction.other_party,
                        TransactionPattern.category_code == correct_category
                    )
                )
                pattern = result.scalar_one_or_none()

                if pattern:
                    # Increase confidence of existing pattern
                    pattern.confidence = min(1.0, pattern.confidence + confidence_adjustment)
                    pattern.times_applied += 1
                    pattern.times_confirmed += 1
                else:
                    # Create new pattern
                    new_pattern = TransactionPattern(
                        other_party_normalized=transaction.other_party.lower().strip() if transaction.other_party else None,
                        description_normalized=transaction.description.lower().strip() if transaction.description else "",
                        category_code=correct_category,
                        confidence=0.7,
                        times_applied=1,
                        source='user_correction'
                    )
                    db.add(new_pattern)

            await db.commit()
            logger.info(f"Learned from feedback for transaction {transaction_id}")
            return True

        except Exception as e:
            logger.error(f"Error learning from feedback: {e}")
            await db.rollback()
            return False

    async def _generate_summary(
        self,
        db: AsyncSession,
        tax_return_id: UUID
    ) -> List[TransactionSummaryResponse]:
        """Generate or update transaction summaries per category."""
        logger.info(f"Starting summary generation for tax_return_id: {tax_return_id}")
        try:
            # Get all transactions for tax return
            result = await db.execute(
                select(Transaction).where(
                    Transaction.tax_return_id == tax_return_id
                )
            )
            transactions = result.scalars().all()

            # Group transactions by category
            category_data = {}
            for transaction in transactions:
                if transaction.category_code:
                    if transaction.category_code not in category_data:
                        category_data[transaction.category_code] = {
                            'count': 0,
                            'gross_amount': Decimal(0),
                            'deductible_amount': Decimal(0),
                            'gst_amount': Decimal(0) if transaction.gst_inclusive else None
                        }

                    category_data[transaction.category_code]['count'] += 1
                    category_data[transaction.category_code]['gross_amount'] += transaction.amount

                    # Calculate deductible amount
                    deductible = transaction.amount * Decimal(transaction.deductible_percentage) / Decimal(100)
                    category_data[transaction.category_code]['deductible_amount'] += deductible

                    # Calculate GST if applicable
                    if transaction.gst_inclusive and category_data[transaction.category_code]['gst_amount'] is not None:
                        gst = transaction.amount * Decimal(3) / Decimal(23)  # GST is 3/23 of GST-inclusive amount
                        category_data[transaction.category_code]['gst_amount'] += gst

            # Delete existing summaries for this tax return
            await db.execute(
                delete(TransactionSummary).where(
                    TransactionSummary.tax_return_id == tax_return_id
                )
            )
            await db.flush()  # Ensure delete is executed before inserts

            # Create new summaries per category
            summaries = []
            for category_code, data in category_data.items():
                summary = TransactionSummary(
                    tax_return_id=tax_return_id,
                    category_code=category_code,
                    transaction_count=data['count'],
                    gross_amount=data['gross_amount'],
                    deductible_amount=data['deductible_amount'],
                    gst_amount=data['gst_amount']
                )
                db.add(summary)
                summaries.append(summary)

            # Flush to get IDs but don't commit yet
            await db.flush()

            # Eagerly load the category_mapping relationship for all summaries
            from sqlalchemy.orm import selectinload
            result = await db.execute(
                select(TransactionSummary)
                .options(selectinload(TransactionSummary.category_mapping))
                .where(TransactionSummary.tax_return_id == tax_return_id)
            )
            summaries = result.scalars().all()

            await db.commit()
            logger.info(f"Successfully created {len(summaries)} summaries for tax_return_id: {tax_return_id}")

            # Return response objects using custom method to include relationship data
            return [TransactionSummaryResponse.from_orm_with_mapping(s) for s in summaries]

        except Exception as e:
            logger.error(f"Error generating summary: {e}", exc_info=True)
            await db.rollback()
            return []

    async def _get_processing_result(
        self,
        db: AsyncSession,
        document_id: UUID,
        tax_return_id: UUID
    ) -> ProcessingResult:
        """Get existing processing results."""
        result = await db.execute(
            select(Transaction).where(
                Transaction.document_id == document_id
            )
        )
        transactions = result.scalars().all()

        summaries = await self._generate_summary(db, tax_return_id)
        summary = summaries[0] if summaries else None

        return ProcessingResult(
            success=True,
            message=f"Found {len(transactions)} existing transactions",
            transactions=[
                TransactionResponse.model_validate(t)
                for t in transactions
            ],
            summary=summary
        )

    async def _get_phase1_transactions(
        self,
        document: Document
    ) -> List[ExtractedTransaction]:
        """
        Get pre-extracted transactions from Phase 1 document classification.

        Phase 1 stores transactions in Document.extracted_data["key_details"]["transactions"]
        This avoids a second Claude API call for transaction extraction.

        For document types without explicit transactions (settlement_statement, depreciation_schedule, etc.),
        we convert their key_details into transaction format.

        Args:
            document: Document with extracted_data from Phase 1

        Returns:
            List of ExtractedTransaction objects, or empty list if none found
        """
        try:
            if not document.extracted_data:
                return []

            key_details = document.extracted_data.get("key_details", {})
            if not key_details:
                # Check if extracted_data IS the key_details (different storage format)
                key_details = document.extracted_data

            transactions_data = key_details.get("transactions", [])

            # If no explicit transactions, try to convert key_details to transactions
            if not transactions_data:
                transactions_data = self._convert_document_to_transactions(
                    document.document_type,
                    key_details,
                    document.original_filename
                )

            if not transactions_data:
                return []

            extracted = []
            for txn in transactions_data:
                try:
                    # Parse date
                    txn_date = None
                    if txn.get("date"):
                        try:
                            from datetime import datetime as dt
                            txn_date = dt.strptime(txn["date"], "%Y-%m-%d").date()
                        except ValueError:
                            # Try alternative formats
                            for fmt in ["%d/%m/%Y", "%d-%m-%Y", "%Y/%m/%d"]:
                                try:
                                    txn_date = dt.strptime(txn["date"], fmt).date()
                                    break
                                except ValueError:
                                    continue

                    # Create ExtractedTransaction
                    extracted_txn = ExtractedTransaction(
                        transaction_date=txn_date,
                        description=txn.get("description", ""),
                        other_party=txn.get("other_party"),
                        amount=Decimal(str(txn.get("amount", 0))),
                        balance=Decimal(str(txn.get("balance"))) if txn.get("balance") is not None else None,
                        raw_data=txn,
                        confidence=txn.get("confidence", 0.8),
                        suggested_category=txn.get("suggested_category"),
                        needs_review=txn.get("needs_review", False),
                        review_reason=", ".join(txn.get("review_reasons", [])) if txn.get("review_reasons") else None
                    )
                    extracted.append(extracted_txn)
                except Exception as e:
                    logger.warning(f"Failed to parse Phase 1 transaction: {e}")
                    continue

            logger.info(f"Retrieved {len(extracted)} transactions from Phase 1 extraction")
            return extracted

        except Exception as e:
            logger.error(f"Error getting Phase 1 transactions: {e}")
            return []

    async def _get_phase1_feedback(
        self,
        db: AsyncSession,
        document_id: UUID
    ) -> Dict[str, Any]:
        """
        Get user feedback submitted during Phase 1 document review.

        This includes:
        - Transaction corrections (user marked as legitimate/not legitimate)
        - Category assignments
        - Notes and explanations

        Args:
            db: Database session
            document_id: Document ID to get feedback for

        Returns:
            Dict with feedback keyed by transaction description/amount
        """
        try:
            # Get CategoryFeedback entries related to this document's transactions
            result = await db.execute(
                select(CategoryFeedback)
                .join(Transaction, CategoryFeedback.transaction_id == Transaction.id)
                .where(Transaction.document_id == document_id)
            )
            feedbacks = result.scalars().all()

            feedback_map = {}
            for fb in feedbacks:
                # Get the transaction to build a key
                txn = await db.get(Transaction, fb.transaction_id)
                if txn:
                    key = f"{txn.description}|{txn.amount}"
                    feedback_map[key] = {
                        "corrected_category": fb.corrected_category,
                        "original_category": fb.original_category,
                        "feedback_type": fb.feedback_type,
                        "notes": fb.notes if hasattr(fb, 'notes') else None
                    }

            if feedback_map:
                logger.info(f"Found {len(feedback_map)} Phase 1 feedback entries for document {document_id}")

            return feedback_map

        except Exception as e:
            logger.error(f"Error getting Phase 1 feedback: {e}")
            return {}

    async def _get_rag_patterns(
        self,
        db: AsyncSession
    ) -> Dict[str, Any]:
        """
        Query RAG (Pinecone) for learned transaction patterns.

        Searches MULTIPLE namespaces for comprehensive pattern matching:
        - transaction-coding: Specific transaction categorization learnings
        - skill_learnings: Tax rules, teachings, and domain knowledge

        Args:
            db: Database session (for category lookup if needed)

        Returns:
            Dict mapping description patterns to suggested categories
        """
        try:
            patterns = {}

            if not knowledge_store.enabled:
                return patterns

            # Search across all relevant namespaces for transaction patterns (7 namespaces)
            # Including tax-rules, gst-rules, pnl-mapping for comprehensive categorization
            transaction_namespaces = [
                "transaction-coding",  # Transaction patterns
                "skill_learnings",     # Domain knowledge
                "tax-rules",           # Tax treatment rules
                "gst-rules",           # GST treatment
                "pnl-mapping",         # P&L row mapping
                "common-errors",       # Common errors to avoid
                "document-review"      # Document patterns
            ]
            all_namespace_results = await knowledge_store.search_all_namespaces(
                query="transaction categorization rental property expense deductible GST",
                top_k=20,
                min_score=0.4,
                namespaces=transaction_namespaces
            )

            # Process results from all namespaces
            for namespace, results in all_namespace_results.items():
                for result in results:
                    content = result.get("content", "")
                    category = result.get("category", "")
                    score = result.get("score", 0.7)

                    # Extract vendor/description patterns from content
                    if "'" in content:
                        # Try to extract the transaction description
                        parts = content.split("'")
                        if len(parts) >= 2:
                            description_pattern = parts[1].lower()
                            # Only add if this pattern doesn't exist or has higher confidence
                            if description_pattern not in patterns or patterns[description_pattern]["confidence"] < score:
                                patterns[description_pattern] = {
                                    "category": category,
                                    "confidence": score,
                                    "source": f"rag_{namespace}"
                                }

            # Also search for skill learnings that contain tax rules
            skill_results = await knowledge_store.search(
                query="NZ tax deductibility interest rates insurance body corporate",
                top_k=10,
                min_score=0.5,
                namespace="skill_learnings"
            )

            # Add skill-based rules (these are more general)
            for result in skill_results:
                content = result.get("content", "").lower()
                category = result.get("category", "")

                # Extract keywords that indicate specific categories
                if "interest" in content and "deductible" in content:
                    if "interest" not in patterns:
                        patterns["interest"] = {
                            "category": "interest",
                            "confidence": 0.8,
                            "source": "rag_skill_rule"
                        }
                if "insurance" in content and "landlord" in content:
                    if "landlord insurance" not in patterns:
                        patterns["landlord insurance"] = {
                            "category": "insurance",
                            "confidence": 0.8,
                            "source": "rag_skill_rule"
                        }
                if "body corporate" in content:
                    if "body corporate" not in patterns:
                        patterns["body corporate"] = {
                            "category": "body_corporate",
                            "confidence": 0.8,
                            "source": "rag_skill_rule"
                        }
                if "council" in content and "rates" in content:
                    if "council rates" not in patterns:
                        patterns["council rates"] = {
                            "category": "rates",
                            "confidence": 0.8,
                            "source": "rag_skill_rule"
                        }

            logger.info(f"Loaded {len(patterns)} patterns from RAG (multi-namespace)")

            return patterns

        except Exception as e:
            logger.error(f"Error getting RAG patterns: {e}")
            return {}

    def _convert_document_to_transactions(
        self,
        document_type: str,
        key_details: Dict[str, Any],
        filename: str
    ) -> List[Dict[str, Any]]:
        """
        Convert document key_details into transaction format.

        This handles document types that don't have explicit transaction lists
        but have structured data that should appear as reviewable transactions.

        Args:
            document_type: Type of document (settlement_statement, depreciation_schedule, etc.)
            key_details: Extracted key details from the document
            filename: Original filename for reference

        Returns:
            List of transaction dictionaries
        """
        transactions = []

        if document_type == "settlement_statement":
            # Extract settlement date for transaction dates
            settlement_date = key_details.get("settlement_date")
            vendor_name = key_details.get("vendor_name") or "Vendor"

            # Settlement statement line items in typical statement order
            # Each item: (field_name, display_name, category, is_expense, related_field_for_calc)
            # related_field_for_calc is used for apportionment calculations (e.g., instalment - apportionment)
            settlement_line_items = [
                # Capital items (not deductible but shown for completeness)
                ("purchase_price", "Purchase Price", "capital_purchase", True, None),
                ("deposit", "Deposit Paid", "capital_purchase", True, None),

                # Rates adjustment - deductible = instalment - apportionment
                ("rates_apportionment", "Rates", "rates", True, "rates_instalment_paid_by_vendor"),

                # Water rates adjustment
                ("water_rates_apportionment", "Water Rates", "water_rates", True, None),

                # Body corporate adjustment
                ("body_corporate_apportionment", "Body Corporate Levy", "body_corporate", True, None),

                # Insurance adjustment
                ("insurance_apportionment", "Insurance", "insurance", True, None),

                # Legal and professional fees
                ("legal_fees", "Legal/Conveyancing Fees", "legal_fees", True, None),
                ("disbursements", "Solicitor Disbursements", "legal_fees", True, None),
                ("agent_commission", "Real Estate Agent Commission", "agent_fees", True, None),

                # Other
                ("land_tax", "Land Tax", "rates", True, None),

                # Interest earned on deposit (income, not expense)
                ("interest_on_deposit", "Interest on Deposit", "other_income", False, None),
            ]

            for field_name, display_name, category, is_expense, related_field in settlement_line_items:
                value = key_details.get(field_name)
                if not value:
                    # Try alternative field names for interest
                    if field_name == "interest_on_deposit":
                        value = (
                            key_details.get("deposit_interest") or
                            key_details.get("interest_earned") or
                            key_details.get("interest_on_stakeholder_deposit")
                        )

                if value:
                    amount = self._parse_amount(value)
                    if amount:
                        # Calculate the actual deductible/transaction amount
                        if related_field:
                            # This is an apportionment with a related instalment field
                            # Deductible = Instalment - Apportionment
                            instalment = self._parse_amount(key_details.get(related_field))
                            if instalment:
                                transaction_amount = round(instalment - amount, 2)
                            else:
                                transaction_amount = amount
                        else:
                            transaction_amount = amount

                        # Create transaction with clean description
                        transactions.append({
                            "date": settlement_date,
                            "description": f"Settlement - {display_name}",
                            "amount": -abs(transaction_amount) if is_expense else abs(transaction_amount),
                            "other_party": vendor_name if category == "capital_purchase" else "Settlement",
                            "suggested_category": category,
                            "confidence": 0.95,
                            "needs_review": False,
                            "review_reasons": [],
                            "raw_data": {
                                "source": "settlement_statement",
                                "field": field_name,
                                "raw_value": str(value),
                                "related_field": related_field,
                                "related_value": str(key_details.get(related_field)) if related_field else None
                            }
                        })

            # Track already processed items to avoid duplicates
            processed_amounts = set()
            for txn in transactions:
                # Create a key from amount to detect duplicates
                # Round to 2 decimal places to handle float precision
                amt_key = round(abs(txn.get("amount", 0)), 2)
                processed_amounts.add(amt_key)

            # Handle other_adjustments array
            other_adjustments = key_details.get("other_adjustments", [])
            if isinstance(other_adjustments, list):
                logger.debug(f"Processing {len(other_adjustments)} other adjustments, {len(processed_amounts)} amounts already processed")

                for adj in other_adjustments:
                    if isinstance(adj, dict):
                        desc = adj.get("description", "Other Adjustment")
                        amt = self._parse_amount(adj.get("amount"))
                        if amt:
                            amt_key = round(abs(amt), 2)

                            # Skip if this amount was already processed
                            if amt_key in processed_amounts:
                                logger.debug(f"Skipping duplicate adjustment: {desc} ${amt} (amount already processed)")
                                continue

                            # Skip if the description contains keywords we've already handled
                            skip_keywords = [
                                'body corporate', 'body corp', 'bc levy', 'levy',
                                'rates instalment', 'rates apportionment',
                                'water rates', 'water apportionment',
                                'insurance apportionment', 'insurance premium',
                                'legal fees', 'solicitor fees', 'conveyancing',
                                'agent commission', 'real estate commission'
                            ]
                            desc_lower = desc.lower()
                            if any(kw in desc_lower for kw in skip_keywords):
                                logger.debug(f"Skipping adjustment already handled by specific field: {desc}")
                                continue

                            # Determine if expense or income based on context/sign
                            # Body corporate/levy/society fees should always be expenses
                            is_expense = (amt < 0 or
                                        "fee" in desc_lower or
                                        "cost" in desc_lower or
                                        "levy" in desc_lower or
                                        "society" in desc_lower)

                            # Determine category based on description
                            if "society" in desc_lower or "levy" in desc_lower:
                                category = "body_corporate"  # Society levies are like body corporate
                            elif is_expense:
                                category = "other_deductible"
                            else:
                                category = "other_income"

                            transactions.append({
                                "date": settlement_date,
                                "description": f"Settlement - {desc}",
                                "amount": -abs(amt) if is_expense else abs(amt),
                                "other_party": "Settlement",
                                "suggested_category": category,
                                "confidence": 0.80,
                                "needs_review": True,
                                "review_reasons": ["settlement_adjustment"]
                            })
                            processed_amounts.add(amt_key)

            logger.info(f"Converted settlement statement to {len(transactions)} transactions")

        elif document_type == "property_manager_statement":
            # Property manager statements should have transactions extracted by Claude,
            # but if not, convert key_details to transactions
            period = key_details.get("period")
            pm_company = key_details.get("pm_company", "Property Manager")

            # Define PM statement items to extract from key_details
            pm_items = [
                # (field_name, description, category, is_income)
                ("gross_rent_collected", "Gross Rent Collected", "rental_income", True),
                ("management_fee", "Property Management Fee", "agent_fees", False),
                ("letting_fee", "Letting/Tenant Finding Fee", "listing_fees", False),
                ("inspection_fee", "Property Inspection Fee", "agent_fees", False),
                ("advertising_fee", "Tenant Advertising", "advertising", False),
                ("maintenance_expenses", "Maintenance & Repairs", "repairs_maintenance", False),
                ("rates_paid", "Council Rates (paid by PM)", "rates", False),
                ("water_rates_paid", "Water Rates (paid by PM)", "water_rates", False),
                ("body_corporate_paid", "Body Corporate (paid by PM)", "body_corporate", False),
                ("sundry_expenses", "Sundry Expenses", "other_deductible", False),
            ]

            for field_name, desc, category, is_income in pm_items:
                value = key_details.get(field_name)
                if value:
                    amount = self._parse_amount(value)
                    if amount:
                        transactions.append({
                            "date": period,
                            "description": f"{desc} ({filename})",
                            "amount": abs(amount) if is_income else -abs(amount),
                            "other_party": pm_company,
                            "suggested_category": category,
                            "confidence": 0.95,
                            "needs_review": False,
                            "review_reasons": []
                        })

            # Insurance claims (income)
            insurance_claims = key_details.get("insurance_claims")
            if insurance_claims:
                amount = self._parse_amount(insurance_claims)
                if amount:
                    transactions.append({
                        "date": period,
                        "description": f"Insurance Claim Received ({filename})",
                        "amount": abs(amount),  # Income
                        "other_party": pm_company,
                        "suggested_category": "insurance_payout",
                        "confidence": 0.95,
                        "needs_review": False,
                        "review_reasons": []
                    })

            logger.info(f"Converted property manager statement to {len(transactions)} transactions")

        elif document_type == "depreciation_schedule":
            # Annual depreciation as a single transaction
            annual_dep = key_details.get("annual_depreciation")
            valuation_date = key_details.get("valuation_date")

            if annual_dep:
                amount = self._parse_amount(annual_dep)
                if amount:
                    transactions.append({
                        "date": valuation_date,
                        "description": f"Annual Depreciation ({filename})",
                        "amount": -abs(amount),  # Expense (negative)
                        "other_party": key_details.get("provider") or "Depreciation",
                        "suggested_category": "depreciation",
                        "confidence": 0.98,
                        "needs_review": False,
                        "review_reasons": []
                    })

            logger.info(f"Converted depreciation schedule to {len(transactions)} transactions")

        elif document_type == "body_corporate":
            # Body corporate levies
            period = key_details.get("period")
            total = key_details.get("total_amount")

            if total:
                amount = self._parse_amount(total)
                if amount:
                    transactions.append({
                        "date": period,
                        "description": f"Body Corporate Levy ({filename})",
                        "amount": -abs(amount),  # Expense
                        "other_party": key_details.get("bc_name") or "Body Corporate",
                        "suggested_category": "body_corporate",
                        "confidence": 0.98,
                        "needs_review": False,
                        "review_reasons": []
                    })

            logger.info(f"Converted body corporate to {len(transactions)} transactions")

        elif document_type == "rates":
            # Council rates - try multiple field names for amount
            rates_amount = (
                key_details.get("instalment_amount_incl_gst") or
                key_details.get("instalment_amount") or
                key_details.get("amount_due") or
                key_details.get("rates_amount") or
                key_details.get("total_rates") or
                key_details.get("annual_rates_incl_gst")
            )

            # Try multiple field names for date
            rates_date = (
                key_details.get("due_date") or
                key_details.get("invoice_date") or
                key_details.get("date")
            )

            # Build description with instalment info if available
            instalment = key_details.get("instalment_number")
            rating_year = key_details.get("rating_year")
            council_name = key_details.get("council_name") or "Council"

            if instalment:
                description = f"Council Rates - Instalment {instalment}"
            elif rating_year:
                description = f"Council Rates {rating_year}"
            else:
                description = f"Council Rates ({filename})"

            if rates_amount:
                amount = self._parse_amount(rates_amount)
                if amount:
                    # Parse the date properly
                    parsed_date = self._parse_date(rates_date) if rates_date else None

                    transactions.append({
                        "date": parsed_date,
                        "description": description,
                        "amount": -abs(amount),  # Expense
                        "other_party": council_name,
                        "suggested_category": "rates",
                        "confidence": 0.98,
                        "needs_review": False,
                        "review_reasons": []
                    })

            logger.info(f"Converted rates notice to {len(transactions)} transactions")

        elif document_type == "landlord_insurance":
            # Insurance premium
            premium = key_details.get("premium_amount") or key_details.get("annual_premium")
            period_start = key_details.get("period_start")

            if premium:
                amount = self._parse_amount(premium)
                if amount:
                    transactions.append({
                        "date": period_start,
                        "description": f"Landlord Insurance Premium ({filename})",
                        "amount": -abs(amount),  # Expense
                        "other_party": key_details.get("insurer") or "Insurance",
                        "suggested_category": "insurance",
                        "confidence": 0.98,
                        "needs_review": False,
                        "review_reasons": []
                    })

            logger.info(f"Converted insurance to {len(transactions)} transactions")

        elif document_type == "maintenance_invoice":
            # Maintenance/repair invoice
            total = key_details.get("total_amount") or key_details.get("amount")
            invoice_date = key_details.get("invoice_date") or key_details.get("date")

            if total:
                amount = self._parse_amount(total)
                if amount:
                    transactions.append({
                        "date": invoice_date,
                        "description": f"Maintenance - {key_details.get('description', filename)}",
                        "amount": -abs(amount),  # Expense
                        "other_party": key_details.get("vendor") or key_details.get("supplier") or "Tradesperson",
                        "suggested_category": "repairs_maintenance",
                        "confidence": 0.90,
                        "needs_review": True,
                        "review_reasons": ["large_payment"] if abs(amount) > 500 else []
                    })

            logger.info(f"Converted maintenance invoice to {len(transactions)} transactions")

        elif document_type == "personal_expenditure_claims":
            # Lighthouse Personal Expenditure Claims template
            # Contains up to 3 claim types: home_office, mobile_phone, mileage
            # Tax year from extracted data, or None (transactions will use tax return's year)
            tax_year = key_details.get("tax_year")

            # 1. Home Office Claim
            home_office = key_details.get("home_office", {})
            home_office_claim = home_office.get("claim_amount")
            if home_office_claim:
                amount = self._parse_amount(home_office_claim)
                if amount and amount > 0:
                    business_pct = home_office.get("business_use_percentage", 0)
                    total_exp = home_office.get("total_expenses", 0)
                    transactions.append({
                        "date": tax_year,
                        "description": f"Home Office ({business_pct:.1f}% of ${total_exp:,.2f})" if business_pct and total_exp else f"Home Office Claim ({filename})",
                        "amount": -abs(amount),  # Expense
                        "other_party": "Personal Expenditure Claims",
                        "suggested_category": "home_office",
                        "confidence": 0.98,
                        "needs_review": False,
                        "review_reasons": []
                    })

            # 2. Mobile Phone Claim
            mobile_phone = key_details.get("mobile_phone", {})
            mobile_claim = mobile_phone.get("claim_amount")
            if mobile_claim:
                amount = self._parse_amount(mobile_claim)
                if amount and amount > 0:
                    annual_exp = mobile_phone.get("annual_expense", 0)
                    transactions.append({
                        "date": tax_year,
                        "description": f"Mobile Phone (50% of ${annual_exp:,.2f})" if annual_exp else f"Mobile Phone Claim ({filename})",
                        "amount": -abs(amount),  # Expense
                        "other_party": "Personal Expenditure Claims",
                        "suggested_category": "mobile_phone",
                        "confidence": 0.98,
                        "needs_review": False,
                        "review_reasons": []
                    })

            # 3. Mileage Claim
            mileage = key_details.get("mileage", {})
            mileage_claim = mileage.get("claim_amount")
            if mileage_claim:
                amount = self._parse_amount(mileage_claim)
                if amount and amount > 0:
                    kms = mileage.get("kilometres_travelled", 0)
                    transactions.append({
                        "date": tax_year,
                        "description": f"Mileage ({kms:,.0f} km x $0.99)" if kms else f"Mileage Claim ({filename})",
                        "amount": -abs(amount),  # Expense
                        "other_party": "Personal Expenditure Claims",
                        "suggested_category": "mileage",
                        "confidence": 0.98,
                        "needs_review": False,
                        "review_reasons": []
                    })

            logger.info(f"Converted personal expenditure claims to {len(transactions)} transactions")

        return transactions

    def _parse_amount(self, value: Any) -> Optional[float]:
        """Parse an amount from various formats."""
        if value is None:
            return None

        if isinstance(value, (int, float)):
            return float(value)

        if isinstance(value, str):
            # Remove currency symbols, commas, spaces
            cleaned = value.replace("$", "").replace(",", "").replace(" ", "").strip()
            # Handle parentheses for negative
            if cleaned.startswith("(") and cleaned.endswith(")"):
                cleaned = "-" + cleaned[1:-1]
            try:
                return float(cleaned)
            except ValueError:
                return None

        return None

    def _parse_date(self, value: Any) -> Optional[str]:
        """Parse a date from various formats and return ISO format (YYYY-MM-DD)."""
        from datetime import datetime

        if value is None:
            return None

        if isinstance(value, str):
            value = value.strip()

            # Try various date formats
            date_formats = [
                "%d/%m/%Y",      # 20/02/2024
                "%d-%m-%Y",      # 20-02-2024
                "%Y-%m-%d",      # 2024-02-20 (ISO)
                "%d %B %Y",      # 20 February 2024
                "%d %b %Y",      # 20 Feb 2024
                "%B %d, %Y",     # February 20, 2024
                "%b %d, %Y",     # Feb 20, 2024
                "%d %B, %Y",     # 27 January, 2024
                "%d %B %Y",      # 27 January 2024
            ]

            for fmt in date_formats:
                try:
                    parsed = datetime.strptime(value, fmt)
                    return parsed.strftime("%Y-%m-%d")
                except ValueError:
                    continue

            logger.warning(f"Could not parse date: {value}")
            return None

        return None

    def _calculate_settlement_prorate(
        self,
        settlement_date_str: str,
        total_amount: float,
        apportionment_type: str
    ) -> Dict[str, Any]:
        """
        Calculate the tax-year deductible portion of a settlement apportionment.

        Settlement apportionments (rates, water, BC, insurance) cover from settlement
        date to end of the relevant period (e.g., rating year 30 June).
        Only the portion that falls within the NZ tax year (1 April - 31 March) is deductible.

        Args:
            settlement_date_str: Settlement date string (various formats)
            total_amount: Total apportionment amount from settlement statement
            apportionment_type: Type of apportionment (rates, water_rates, etc.)

        Returns:
            Dict with deductible_amount, note, and calculation details
        """
        from datetime import datetime, date

        # Parse settlement date
        settlement_date = None
        for fmt in ["%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d %B %Y", "%d %b %Y"]:
            try:
                settlement_date = datetime.strptime(settlement_date_str, fmt).date()
                break
            except (ValueError, TypeError):
                continue

        if not settlement_date:
            # Can't parse date, return full amount
            return {
                "deductible_amount": total_amount,
                "note": "full amount",
                "calculation": "Unable to parse settlement date"
            }

        # Determine the tax year end (31 March)
        # If settlement is Jan-Mar, tax year ends same calendar year
        # If settlement is Apr-Dec, tax year ends next calendar year
        if settlement_date.month <= 3:
            tax_year_end = date(settlement_date.year, 3, 31)
        else:
            tax_year_end = date(settlement_date.year + 1, 3, 31)

        # Determine the rating/coverage period end based on apportionment type
        # Council rates typically run 1 July - 30 June
        # Water rates, BC, insurance may vary but often align with rating year
        if apportionment_type in ["rates_apportionment", "water_rates_apportionment"]:
            # Rating year ends 30 June
            if settlement_date.month <= 6:
                rating_period_end = date(settlement_date.year, 6, 30)
            else:
                rating_period_end = date(settlement_date.year + 1, 6, 30)
        elif apportionment_type == "body_corporate_apportionment":
            # BC typically aligns with rating year or calendar year
            if settlement_date.month <= 6:
                rating_period_end = date(settlement_date.year, 6, 30)
            else:
                rating_period_end = date(settlement_date.year + 1, 6, 30)
        elif apportionment_type == "insurance_apportionment":
            # Insurance typically annual from policy inception
            # Assume 12 months from settlement for simplicity
            rating_period_end = date(settlement_date.year + 1, settlement_date.month, settlement_date.day)
        else:
            # Default to rating year
            if settlement_date.month <= 6:
                rating_period_end = date(settlement_date.year, 6, 30)
            else:
                rating_period_end = date(settlement_date.year + 1, 6, 30)

        # Calculate days
        total_days = (rating_period_end - settlement_date).days
        if total_days <= 0:
            return {
                "deductible_amount": total_amount,
                "note": "full amount",
                "calculation": "Settlement after period end"
            }

        # Days within tax year
        effective_end = min(tax_year_end, rating_period_end)
        tax_year_days = (effective_end - settlement_date).days

        if tax_year_days <= 0:
            return {
                "deductible_amount": 0.0,
                "note": "N/A this tax year",
                "calculation": f"Settlement {settlement_date} after tax year end"
            }

        # Pro-rata calculation
        prorate_ratio = tax_year_days / total_days
        deductible_amount = round(total_amount * prorate_ratio, 2)

        return {
            "deductible_amount": deductible_amount,
            "note": f"${deductible_amount:.2f} of ${total_amount:.2f}",
            "calculation": f"{tax_year_days} of {total_days} days = {prorate_ratio:.2%}",
            "settlement_date": str(settlement_date),
            "tax_year_end": str(tax_year_end),
            "rating_period_end": str(rating_period_end)
        }

    async def _build_document_context(
        self,
        db: AsyncSession,
        tax_return_id: UUID
    ) -> Dict[str, Any]:
        """
        Build cross-document context by extracting key information from all documents.

        This enables intelligent pattern matching, e.g., recognizing that a transfer
        to a loan account number is a principal repayment.

        Args:
            db: Database session
            tax_return_id: Tax return to build context for

        Returns:
            Dict containing:
            - loan_accounts: List of {account_number, lender, holder_name}
            - bank_accounts: List of {account_number, account_name, bank_name}
            - client_names: Set of names associated with the property
            - property_address: The property address
        """
        context = {
            "loan_accounts": [],
            "bank_accounts": [],
            "client_names": set(),
            "property_address": None
        }

        try:
            # Get all documents for this tax return
            result = await db.execute(
                select(Document).where(Document.tax_return_id == tax_return_id)
            )
            documents = result.scalars().all()

            for doc in documents:
                if not doc.extracted_data:
                    continue

                key_details = doc.extracted_data.get("key_details", {})
                if not key_details:
                    # Check if extracted_data IS the key_details (different storage format)
                    key_details = doc.extracted_data

                # Extract loan account information
                if doc.document_type == "loan_statement":
                    loan_account = key_details.get("loan_account_number")
                    if loan_account:
                        # Clean up the account number (remove spaces/dashes for matching)
                        clean_account = loan_account.replace(" ", "").replace("-", "")
                        context["loan_accounts"].append({
                            "account_number": loan_account,
                            "account_number_clean": clean_account,
                            "lender": key_details.get("lender"),
                            "holder_name": key_details.get("account_name") or key_details.get("borrower_name")
                        })
                        logger.info(f"Found loan account: {loan_account}")

                # Extract bank account information
                if doc.document_type == "bank_statement":
                    bank_account = key_details.get("account_number")
                    if bank_account:
                        context["bank_accounts"].append({
                            "account_number": bank_account,
                            "account_name": key_details.get("account_name"),
                            "bank_name": key_details.get("bank_name")
                        })
                        # Extract client name from account name
                        account_name = key_details.get("account_name")
                        if account_name:
                            context["client_names"].add(account_name.upper())

                # Extract property address
                if not context["property_address"]:
                    prop_addr = key_details.get("property_address")
                    if prop_addr:
                        context["property_address"] = prop_addr

            # Convert set to list for JSON serialization
            context["client_names"] = list(context["client_names"])

            logger.info(f"Built document context: {len(context['loan_accounts'])} loan accounts, "
                       f"{len(context['bank_accounts'])} bank accounts, "
                       f"{len(context['client_names'])} client names")

            return context

        except Exception as e:
            logger.error(f"Error building document context: {e}")
            return context

    async def _apply_phase1_feedback(
        self,
        transactions: List[ExtractedTransaction],
        feedback: Dict[str, Any],
        rag_patterns: Dict[str, Any]
    ) -> List[ExtractedTransaction]:
        """
        Apply Phase 1 feedback and RAG patterns to transactions before categorization.

        Args:
            transactions: List of extracted transactions
            feedback: Phase 1 user feedback keyed by description|amount
            rag_patterns: RAG patterns keyed by description patterns

        Returns:
            Transactions with feedback and patterns applied
        """
        for txn in transactions:
            # Check for direct feedback
            key = f"{txn.description}|{txn.amount}"
            if key in feedback:
                fb = feedback[key]
                txn.suggested_category = fb.get("corrected_category")
                txn.confidence = 1.0  # User-provided is 100% confidence
                txn.needs_review = False
                logger.debug(f"Applied Phase 1 feedback: {txn.description} -> {txn.suggested_category}")
                continue

            # Check for RAG pattern matches
            desc_lower = txn.description.lower() if txn.description else ""
            for pattern, pattern_info in rag_patterns.items():
                if pattern in desc_lower:
                    if not txn.suggested_category or txn.confidence < pattern_info.get("confidence", 0.7):
                        txn.suggested_category = pattern_info.get("category")
                        txn.confidence = pattern_info.get("confidence", 0.7)
                        logger.debug(f"Applied RAG pattern: {txn.description} -> {txn.suggested_category}")
                    break

        return transactions


# Singleton instance
_processor_instance: Optional[TransactionProcessor] = None


def get_transaction_processor() -> TransactionProcessor:
    """Get or create TransactionProcessor singleton."""
    global _processor_instance
    if _processor_instance is None:
        _processor_instance = TransactionProcessor()
    return _processor_instance