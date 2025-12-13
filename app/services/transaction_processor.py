"""Transaction processing integration layer.

Orchestrates the flow: extraction → categorization → storage
"""
import logging
from typing import List, Optional, Dict, Any
from uuid import UUID
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update, delete
from decimal import Decimal

from app.models.db_models import (
    Document, TaxReturn, Transaction, TransactionSummary,
    TransactionPattern, CategoryFeedback, TaxRule, PLRowMapping
)
from app.schemas.transactions import (
    TransactionCreate, TransactionResponse,
    TransactionSummaryResponse, ProcessingResult
)
# Toggle between old and new extractor here
# from app.services.transaction_extractor import TransactionExtractor  # Old code-based
from app.services.transaction_extractor_claude import TransactionExtractorClaude as TransactionExtractor  # New Claude-based
from app.services.transaction_categorizer import TransactionCategorizer
from app.services.tax_rules_service import TaxRulesService

logger = logging.getLogger(__name__)


class TransactionProcessor:
    """Orchestrates transaction processing pipeline."""

    def __init__(self):
        self.extractor = TransactionExtractor()
        self.categorizer = TransactionCategorizer()
        self.tax_service = TaxRulesService()

    async def process_document(
        self,
        db: AsyncSession,
        document_id: UUID,
        tax_return_id: UUID,
        force_reprocess: bool = False
    ) -> ProcessingResult:
        """
        Process a document through the full pipeline.

        Args:
            db: Database session
            document_id: Document to process
            tax_return_id: Associated tax return
            force_reprocess: Whether to reprocess existing transactions

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

            # Load file content if it's a CSV (AFTER retrieving document but BEFORE further async operations)
            file_content = None
            if doc_filename.lower().endswith('.csv') and doc_file_path:
                try:
                    file_path = Path(doc_file_path)
                    if file_path.exists():
                        # Read file synchronously
                        file_content = file_path.read_bytes()
                        logger.info(f"Loaded CSV file content: {len(file_content)} bytes from {doc_file_path}")
                    else:
                        logger.warning(f"File path not found: {doc_file_path}")
                except Exception as e:
                    logger.error(f"Failed to load file content: {e}")

            # Extract transactions
            logger.info(f"Extracting transactions from document {document_id}")

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

            # Batch categorize all transactions for efficiency
            logger.info(f"Categorizing {len(extraction_result.transactions)} transactions in batches...")
            categorized_results = await self.categorizer.categorize_batch(
                db, extraction_result.transactions, tax_return, use_claude=True
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

                # Update transaction with categorization (category_result is TransactionCreate object)
                transaction.category_code = category_result.category_code
                transaction.gst_inclusive = category_result.gst_inclusive if hasattr(category_result, 'gst_inclusive') else False
                transaction.deductible_percentage = category_result.deductible_percentage if hasattr(category_result, 'deductible_percentage') else 100.0
                transaction.categorization_source = category_result.categorization_source if hasattr(category_result, 'categorization_source') else 'unknown'
                transaction.categorization_trace = category_result.categorization_trace if hasattr(category_result, 'categorization_trace') else None
                transaction.confidence = category_result.confidence if hasattr(category_result, 'confidence') else 0.0
                transaction.needs_review = category_result.needs_review if hasattr(category_result, 'needs_review') else True
                # Truncate review_reason as safety fallback (even though DB now supports TEXT)
                review_reason = category_result.review_reason if hasattr(category_result, 'review_reason') else None
                if review_reason and len(review_reason) > 10000:  # Safety limit for extremely long text
                    review_reason = review_reason[:9997] + "..."
                transaction.review_reason = review_reason

                # Apply tax rules
                tax_result = await self.tax_service.apply_tax_rules(
                    db, transaction, tax_return
                )
                # tax_result is likely a dict or object - handle both cases
                if isinstance(tax_result, dict):
                    transaction.deductible_percentage = tax_result.get('deductible_percentage', transaction.deductible_percentage)
                    transaction.gst_inclusive = tax_result.get('gst_inclusive', transaction.gst_inclusive)
                else:
                    transaction.deductible_percentage = getattr(tax_result, 'deductible_percentage', transaction.deductible_percentage)
                    transaction.gst_inclusive = getattr(tax_result, 'gst_inclusive', transaction.gst_inclusive)

                db.add(transaction)
                processed_transactions.append(transaction)

            # Commit transactions
            await db.commit()

            # Generate summary (currently returns list of per-category summaries)
            summaries = await self._generate_summary(db, tax_return_id)
            # TODO: Decide how to handle multiple category summaries in ProcessingResult
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
    TRANSACTION_DOC_TYPES = ["bank_statement", "loan_statement", "property_manager_statement"]

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

                # Update transaction (category_result is TransactionCreate object)
                transaction.category_code = category_result.category_code
                transaction.gst_inclusive = category_result.gst_inclusive if hasattr(category_result, 'gst_inclusive') else False
                transaction.deductible_percentage = category_result.deductible_percentage if hasattr(category_result, 'deductible_percentage') else 100.0
                transaction.categorization_source = category_result.categorization_source if hasattr(category_result, 'categorization_source') else 'unknown'
                transaction.categorization_trace = category_result.categorization_trace if hasattr(category_result, 'categorization_trace') else None
                transaction.confidence = category_result.confidence if hasattr(category_result, 'confidence') else 0.0
                transaction.needs_review = category_result.needs_review if hasattr(category_result, 'needs_review') else True
                # Truncate review_reason as safety fallback
                review_reason = category_result.review_reason if hasattr(category_result, 'review_reason') else None
                if review_reason and len(review_reason) > 10000:  # Safety limit for extremely long text
                    review_reason = review_reason[:9997] + "..."
                transaction.review_reason = review_reason

                # Reapply tax rules
                tax_result = await self.tax_service.apply_tax_rules(
                    db, transaction, tax_return
                )
                # tax_result is likely a dict or object - handle both cases
                if isinstance(tax_result, dict):
                    transaction.deductible_percentage = tax_result.get('deductible_percentage', transaction.deductible_percentage)
                    transaction.gst_inclusive = tax_result.get('gst_inclusive', transaction.gst_inclusive)
                else:
                    transaction.deductible_percentage = getattr(tax_result, 'deductible_percentage', transaction.deductible_percentage)
                    transaction.gst_inclusive = getattr(tax_result, 'gst_inclusive', transaction.gst_inclusive)

                updated_transactions.append(transaction)

            # Commit updates
            await db.commit()

            # Regenerate summary (currently returns list of per-category summaries)
            summaries = await self._generate_summary(db, tax_return_id)
            # TODO: Decide how to handle multiple category summaries in ProcessingResult
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
        # TODO: Decide how to handle multiple category summaries in ProcessingResult
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


# Singleton instance
_processor_instance: Optional[TransactionProcessor] = None


def get_transaction_processor() -> TransactionProcessor:
    """Get or create TransactionProcessor singleton."""
    global _processor_instance
    if _processor_instance is None:
        _processor_instance = TransactionProcessor()
    return _processor_instance