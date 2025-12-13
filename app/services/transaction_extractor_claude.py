"""Transaction extractor using Claude AI for universal bank/loan statement parsing."""
import json
import logging
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Dict, List, Optional

from app.models.db_models import Document
from app.schemas.transactions import DocumentExtractionResult, ExtractedTransaction
from app.services.phase1_document_intake.claude_client import ClaudeClient

logger = logging.getLogger(__name__)


class TransactionExtractorClaude:
    """Extract transactions from financial documents using Claude AI."""

    def __init__(self):
        self.claude_client = ClaudeClient()

    async def extract_from_document(
        self,
        document: Document,
        tax_return,  # Accept TaxReturn model, not context dict
        file_content: bytes = None  # Add this parameter for compatibility
    ) -> DocumentExtractionResult:
        """
        Extract transactions from a document using Claude AI.

        Args:
            document: Document to extract from
            tax_return: TaxReturn model object
            file_content: Optional pre-loaded file content

        Returns:
            DocumentExtractionResult with extracted transactions
        """
        # Determine document type
        doc_type = document.document_type or "bank_statement"

        # Try to read the file
        try:
            # Use provided file_content if available, otherwise read from disk
            if file_content:
                logger.info(f"Using provided file content ({len(file_content)} bytes)")
                content = file_content.decode('utf-8', errors='ignore')
            else:
                file_path = Path(document.file_path)
                if not file_path.exists():
                    logger.error(f"File not found: {file_path}")
                    return DocumentExtractionResult(
                        document_id=document.id,
                        document_type=doc_type,
                        filename=document.original_filename,
                        transactions=[],
                        extraction_method="claude_ai",
                        total_transactions=0,
                        total_income=Decimal("0"),
                        total_expenses=Decimal("0"),
                        errors=[f"File not found: {document.file_path}"],
                        warnings=[]
                    )

                # Read file content
                content = file_path.read_text(encoding='utf-8', errors='ignore')

        except Exception as e:
            logger.error(f"Error reading file {document.file_path}: {e}")
            return DocumentExtractionResult(
                document_id=document.id,
                document_type=doc_type,
                filename=document.original_filename,
                transactions=[],
                extraction_method="claude_ai",
                total_transactions=0,
                total_income=Decimal("0"),
                total_expenses=Decimal("0"),
                errors=[f"Error reading file: {str(e)}"],
                warnings=[]
            )

        # Extract transactions using Claude
        transactions = await self._extract_with_claude(
            content=content,
            filename=document.original_filename,
            document_type=doc_type,
            tax_year=tax_return.tax_year if tax_return else "FY25"
        )

        # Calculate totals
        total_income = sum(t.amount for t in transactions if t.amount > 0)
        total_expenses = abs(sum(t.amount for t in transactions if t.amount < 0))

        return DocumentExtractionResult(
            document_id=document.id,
            document_type=doc_type,
            filename=document.original_filename,
            transactions=transactions,
            extraction_method="claude_ai",
            total_transactions=len(transactions),
            total_income=total_income,
            total_expenses=total_expenses,
            errors=[],
            warnings=[]
        )

    async def _extract_with_claude(
        self,
        content: str,
        filename: str,
        document_type: str,
        tax_year: str
    ) -> List[ExtractedTransaction]:
        """
        Extract transactions using Claude AI.

        Args:
            content: File content
            filename: Original filename
            document_type: Type of document
            tax_year: Tax year (e.g., "FY24")

        Returns:
            List of extracted transactions
        """
        # Truncate if too long (Claude context limit)
        if len(content) > 50000:
            logger.warning(f"File content truncated from {len(content)} to 50000 chars")
            content = content[:50000]

        # Calculate tax year date range
        # FY24 = 1 April 2023 to 31 March 2024
        try:
            fy_num = int(tax_year[2:])  # "FY24" -> 24
            start_date = f"20{fy_num - 1:02d}-04-01"
            end_date = f"20{fy_num:02d}-03-31"
        except:
            # Default to FY25 if parsing fails
            start_date = "2024-04-01"
            end_date = "2025-03-31"

        # Build extraction prompt
        prompt = f"""Extract all financial transactions from this {document_type}.

DOCUMENT: {filename}
TAX YEAR: {tax_year} (NZ tax year: {start_date} to {end_date})

CONTENT:
{content}

INSTRUCTIONS:
1. Extract EVERY transaction within the tax year date range ({start_date} to {end_date})
2. Skip header rows, totals, opening/closing balances, and metadata rows
3. Parse dates in any format (DD/MM/YYYY, DD-Mon-YY, etc.)
4. Determine if each transaction is income or expense based on context and cash flow

FOR LOAN STATEMENTS:
- "LOAN INTEREST", "Interest Charged", "Interest" = deductible expense (category: "interest", confidence: 0.95)
- "Payment", "Direct Credit", "AP#" from owner = principal repayment, NOT deductible (category: "principal_repayment", confidence: 0.95)
- Skip initial loan drawdown/transfer
- Interest rates and facility info are not transactions

FOR BANK STATEMENTS:
- Rent/rental payments IN = rental_income
- Payments OUT = categorize appropriately (rates, insurance, repairs, agent_fees, etc.)
- Internal transfers between accounts = transfer (not deductible)

Return a JSON array where each transaction has:
- date: "YYYY-MM-DD" format (convert all dates to this format)
- description: exact transaction description as shown
- other_party: counterparty/reference if identifiable, else null
- amount: decimal number (NEGATIVE for expenses/money out, POSITIVE for income/money in)
- balance: running balance after transaction if shown, else null
- suggested_category: one of ["rental_income", "interest", "rates", "water_rates", "insurance", "repairs_maintenance", "agent_fees", "body_corporate", "legal_fees", "accounting_fee", "bank_fees", "principal_repayment", "transfer", "other_expense", "other_income", "unknown"]
- confidence: 0.0-1.0 (how confident in the categorization)
- needs_review: true if unclear or unusual
- review_reason: brief reason if needs_review is true, else null

IMPORTANT:
- For loan statements, clearly distinguish between INTEREST (deductible) and PRINCIPAL (not deductible)
- Amount signs: expenses/interest are NEGATIVE, income/payments in are POSITIVE
- Skip any rows that aren't actual transactions (headers, totals, balances, etc.)
- If unsure about a transaction, set needs_review: true

RESPOND WITH ONLY THE JSON ARRAY. NO EXPLANATIONS OR MARKDOWN.

Example for loan statement:
[
  {{"date": "2023-08-03", "description": "LOAN INTEREST", "other_party": null, "amount": -833.40, "balance": 639874.33, "suggested_category": "interest", "confidence": 0.95, "needs_review": false, "review_reason": null}},
  {{"date": "2023-08-03", "description": "AP#22200442 FROM R R CHAND", "other_party": "R R CHAND", "amount": 959.07, "balance": 639040.93, "suggested_category": "principal_repayment", "confidence": 0.95, "needs_review": false, "review_reason": null}}
]
"""

        try:
            logger.info(f"Extracting transactions from {filename} using Claude AI")

            # Call Claude
            response = await self.claude_client.extract_with_prompt(prompt)

            # Clean response (remove markdown if present)
            response_text = response.strip()
            if response_text.startswith("```"):
                # Remove markdown code blocks
                lines = response_text.split('\n')
                start_idx = 0
                end_idx = len(lines)

                for i, line in enumerate(lines):
                    if line.startswith("```"):
                        if start_idx == 0:
                            start_idx = i + 1
                        else:
                            end_idx = i
                            break

                response_text = '\n'.join(lines[start_idx:end_idx])

            # Parse JSON response
            transactions_data = json.loads(response_text)

            logger.info(f"Claude extracted {len(transactions_data)} transactions from {filename}")

            # Convert to ExtractedTransaction objects
            transactions = []
            for txn in transactions_data:
                try:
                    # Parse date
                    transaction_date = datetime.strptime(txn["date"], "%Y-%m-%d").date()

                    # Create transaction
                    transaction = ExtractedTransaction(
                        transaction_date=transaction_date,
                        description=txn.get("description", ""),
                        other_party=txn.get("other_party"),
                        amount=Decimal(str(txn["amount"])),
                        balance=Decimal(str(txn["balance"])) if txn.get("balance") is not None else None,
                        suggested_category=txn.get("suggested_category", "unknown"),
                        confidence=float(txn.get("confidence", 0.5)),
                        needs_review=txn.get("needs_review", False),
                        review_reason=txn.get("review_reason"),
                        raw_data=txn  # Store original for debugging
                    )

                    # Additional validation for loan statements
                    if document_type == "loan_statement":
                        # Principal repayments should not be deductible
                        if transaction.suggested_category == "principal_repayment":
                            transaction.needs_review = False  # We're confident about this
                            transaction.review_reason = "Principal repayment - not tax deductible"

                    transactions.append(transaction)

                except Exception as e:
                    logger.warning(f"Failed to parse transaction: {txn}, error: {e}")
                    continue

            # Log summary
            total_interest = sum(abs(t.amount) for t in transactions if t.suggested_category == "interest")
            total_principal = sum(abs(t.amount) for t in transactions if t.suggested_category == "principal_repayment")

            if document_type == "loan_statement":
                logger.info(f"Loan statement summary: Interest=${total_interest:.2f}, Principal=${total_principal:.2f}")

            return transactions

        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse Claude response as JSON: {e}")
            logger.error(f"Response was: {response_text[:500] if 'response_text' in locals() else 'N/A'}")
            return []
        except Exception as e:
            logger.error(f"Error extracting transactions with Claude: {e}")
            return []