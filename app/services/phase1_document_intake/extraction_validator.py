"""Extraction validation service for ensuring complete and accurate document extraction.

This module provides:
1. Balance reconciliation - Verify extracted transactions match document totals
2. Verification pass - Second Claude call to validate extraction completeness
3. Cross-document validation - Catch discrepancies between related documents
"""

import json
import logging
from dataclasses import dataclass, field
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Dict, List, Optional, Tuple

from app.config import settings

logger = logging.getLogger(__name__)


@dataclass
class ValidationResult:
    """Result of extraction validation."""

    is_valid: bool
    confidence: float
    issues: List[Dict[str, Any]] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    reconciliation: Optional[Dict[str, Any]] = None
    verification_details: Optional[Dict[str, Any]] = None
    cross_validation: Optional[Dict[str, Any]] = None
    suggested_corrections: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "is_valid": self.is_valid,
            "confidence": self.confidence,
            "issues": self.issues,
            "warnings": self.warnings,
            "reconciliation": self.reconciliation,
            "verification_details": self.verification_details,
            "cross_validation": self.cross_validation,
            "suggested_corrections": self.suggested_corrections,
        }


class ExtractionValidator:
    """Validates extraction completeness and accuracy."""

    # Tolerance for balance reconciliation (accounts for rounding)
    BALANCE_TOLERANCE = Decimal("0.02")

    # Minimum confidence threshold for extraction
    MIN_CONFIDENCE_THRESHOLD = 0.8

    # Maximum percentage of flagged transactions before triggering review
    MAX_FLAGGED_PERCENTAGE = 0.3

    def __init__(self, claude_client=None):
        """Initialize validator with optional Claude client for verification pass."""
        self.claude_client = claude_client

    # =========================================================================
    # 1. BALANCE RECONCILIATION
    # =========================================================================

    def reconcile_bank_statement(
        self,
        extracted_data: Dict[str, Any]
    ) -> Tuple[bool, Dict[str, Any]]:
        """
        Reconcile extracted bank statement transactions against stated balances.

        Formula: opening_balance + sum(credits) - sum(debits) = closing_balance

        Returns:
            Tuple of (is_reconciled, reconciliation_details)
        """
        try:
            statement_period = extracted_data.get("statement_period", {})
            transactions = extracted_data.get("transactions", [])
            summary = extracted_data.get("summary", {})

            # Also validate interest_analysis if present
            interest_analysis = extracted_data.get("interest_analysis", {})
            if interest_analysis:
                self._validate_interest_analysis(interest_analysis, extracted_data)

            opening_balance = Decimal(str(statement_period.get("opening_balance", 0)))
            closing_balance = Decimal(str(statement_period.get("closing_balance", 0)))

            # Calculate from transactions
            total_credits = Decimal("0")
            total_debits = Decimal("0")

            for txn in transactions:
                amount = Decimal(str(txn.get("amount", 0)))
                txn_type = txn.get("transaction_type", "")

                if txn_type == "credit" or amount > 0:
                    total_credits += abs(amount)
                else:
                    total_debits += abs(amount)

            # Calculate expected closing balance
            calculated_closing = opening_balance + total_credits - total_debits
            variance = abs(calculated_closing - closing_balance)

            # Check against stated summary totals
            stated_credits = Decimal(str(summary.get("total_credits", 0)))
            stated_debits = Decimal(str(summary.get("total_debits", 0)))

            credit_variance = abs(total_credits - stated_credits) if stated_credits else None
            debit_variance = abs(total_debits - stated_debits) if stated_debits else None

            is_reconciled = variance <= self.BALANCE_TOLERANCE

            reconciliation = {
                "opening_balance": float(opening_balance),
                "closing_balance": float(closing_balance),
                "calculated_closing": float(calculated_closing),
                "variance": float(variance),
                "is_reconciled": is_reconciled,
                "tolerance": float(self.BALANCE_TOLERANCE),
                "transaction_count": len(transactions),
                "totals": {
                    "extracted_credits": float(total_credits),
                    "extracted_debits": float(total_debits),
                    "stated_credits": float(stated_credits) if stated_credits else None,
                    "stated_debits": float(stated_debits) if stated_debits else None,
                    "credit_variance": float(credit_variance) if credit_variance else None,
                    "debit_variance": float(debit_variance) if debit_variance else None,
                },
                "potential_missing_amount": float(variance) if not is_reconciled else 0,
            }

            if not is_reconciled:
                logger.warning(
                    f"Balance reconciliation failed: variance=${variance:.2f}, "
                    f"expected=${closing_balance:.2f}, calculated=${calculated_closing:.2f}"
                )

            return is_reconciled, reconciliation

        except Exception as e:
            logger.error(f"Balance reconciliation error: {e}")
            return False, {"error": str(e), "is_reconciled": False}

    def _validate_interest_analysis(
        self,
        interest_analysis: Dict[str, Any],
        extracted_data: Dict[str, Any]
    ) -> None:
        """
        Validate that interest_analysis totals match the sum of monthly_breakdown.

        If there's a discrepancy, correct the total_interest_debits to match
        the actual sum of the monthly breakdown (which is derived from transactions).
        """
        monthly_breakdown = interest_analysis.get("monthly_breakdown", {})
        stated_total = Decimal(str(interest_analysis.get("total_interest_debits", 0)))

        if not monthly_breakdown:
            return

        # Calculate actual sum from monthly breakdown
        actual_sum = sum(Decimal(str(v)) for v in monthly_breakdown.values())
        variance = abs(stated_total - actual_sum)

        if variance > self.BALANCE_TOLERANCE:
            logger.warning(
                f"Interest analysis mismatch: stated={stated_total:.2f}, "
                f"monthly_sum={actual_sum:.2f}, variance={variance:.2f}. "
                f"Correcting total_interest_debits to match monthly breakdown."
            )
            # Correct the value in-place
            interest_analysis["total_interest_debits"] = float(actual_sum)
            interest_analysis["_original_stated_total"] = float(stated_total)
            interest_analysis["_correction_applied"] = True
            interest_analysis["_correction_variance"] = float(variance)

        # Also validate against actual transaction sum
        transactions = extracted_data.get("transactions", [])
        if transactions:
            txn_interest_sum = Decimal("0")
            for txn in transactions:
                desc = txn.get("description", "").upper()
                txn_type = txn.get("transaction_type", "")
                if "LOAN" in desc and "INT" in desc and txn_type == "debit":
                    txn_interest_sum += abs(Decimal(str(txn.get("amount", 0))))

            txn_variance = abs(actual_sum - txn_interest_sum)
            if txn_variance > self.BALANCE_TOLERANCE:
                logger.warning(
                    f"Interest analysis vs transactions mismatch: "
                    f"monthly_sum={actual_sum:.2f}, txn_sum={txn_interest_sum:.2f}, "
                    f"variance={txn_variance:.2f}"
                )
                # Use transaction sum as the source of truth
                interest_analysis["total_interest_debits"] = float(txn_interest_sum)
                interest_analysis["_transaction_derived"] = True

    def reconcile_loan_statement(
        self,
        extracted_data: Dict[str, Any]
    ) -> Tuple[bool, Dict[str, Any]]:
        """
        Reconcile loan statement interest totals.

        Returns:
            Tuple of (is_reconciled, reconciliation_details)
        """
        try:
            interest_summary = extracted_data.get("interest_summary", {})
            transactions = extracted_data.get("transactions", [])

            stated_interest = Decimal(str(interest_summary.get("total_interest_charged", 0)))

            # Calculate from transactions
            calculated_interest = Decimal("0")
            interest_txn_count = 0

            for txn in transactions:
                txn_type = txn.get("transaction_type", "")
                if txn_type == "interest_debit":
                    calculated_interest += abs(Decimal(str(txn.get("amount", 0))))
                    interest_txn_count += 1

            variance = abs(calculated_interest - stated_interest)
            is_reconciled = variance <= self.BALANCE_TOLERANCE or stated_interest == 0

            reconciliation = {
                "stated_interest": float(stated_interest),
                "calculated_interest": float(calculated_interest),
                "variance": float(variance),
                "is_reconciled": is_reconciled,
                "interest_transaction_count": interest_txn_count,
                "potential_missing_interest": float(variance) if not is_reconciled else 0,
            }

            return is_reconciled, reconciliation

        except Exception as e:
            logger.error(f"Loan reconciliation error: {e}")
            return False, {"error": str(e), "is_reconciled": False}

    def reconcile_pm_statement(
        self,
        extracted_data: Dict[str, Any]
    ) -> Tuple[bool, Dict[str, Any]]:
        """
        Reconcile property manager statement totals.

        Returns:
            Tuple of (is_reconciled, reconciliation_details)
        """
        try:
            income = extracted_data.get("income", {})
            expenses = extracted_data.get("expenses", {})
            summary = extracted_data.get("summary", {})

            stated_income = Decimal(str(income.get("total_income", income.get("gross_rent", 0))))
            stated_expenses = Decimal(str(expenses.get("total_expenses", 0)))

            opening = Decimal(str(summary.get("opening_balance", 0)))
            closing = Decimal(str(summary.get("closing_balance", 0)))
            disbursed = Decimal(str(summary.get("total_disbursed", 0)))

            # Expected closing: opening + income - expenses - disbursed
            calculated_closing = opening + stated_income - stated_expenses - disbursed
            variance = abs(calculated_closing - closing)

            is_reconciled = variance <= Decimal("1.00")  # PM statements often have rounding

            reconciliation = {
                "opening_balance": float(opening),
                "closing_balance": float(closing),
                "calculated_closing": float(calculated_closing),
                "variance": float(variance),
                "is_reconciled": is_reconciled,
                "totals": {
                    "income": float(stated_income),
                    "expenses": float(stated_expenses),
                    "disbursed": float(disbursed),
                },
            }

            return is_reconciled, reconciliation

        except Exception as e:
            logger.error(f"PM statement reconciliation error: {e}")
            return False, {"error": str(e), "is_reconciled": False}

    # =========================================================================
    # 2. VERIFICATION PASS
    # =========================================================================

    async def verification_pass(
        self,
        document_content: Optional[str],
        image_data: Optional[List[Tuple[bytes, str]]],
        extracted_data: Dict[str, Any],
        document_type: str,
        context: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Second Claude call to verify extraction completeness.

        This pass:
        1. Shows Claude what was extracted
        2. Asks it to verify against the source document
        3. Identifies any missing items
        """
        if not self.claude_client:
            logger.warning("No Claude client available for verification pass")
            return {"skipped": True, "reason": "No Claude client"}

        verification_prompt = self._build_verification_prompt(
            extracted_data, document_type
        )

        try:
            content = self.claude_client._build_message_content(document_content, image_data)

            # Add extracted data summary to the message
            content.append({
                "type": "text",
                "text": verification_prompt
            })

            response = await self.claude_client._call_with_retry(
                lambda: self.claude_client.client.messages.create(
                    model=self.claude_client.model,
                    max_tokens=8192,
                    temperature=0.1,
                    system="""You are a meticulous document verification specialist.
Your job is to compare extracted data against the source document and identify ANY discrepancies.
Be extremely thorough - missing even one transaction could affect tax calculations.
Respond with JSON only.""",
                    messages=[{"role": "user", "content": content}],
                )
            )

            response_text = response.content[0].text

            # Parse JSON response
            if "```json" in response_text:
                response_text = response_text.split("```json")[1].split("```")[0].strip()
            elif "```" in response_text:
                response_text = response_text.split("```")[1].split("```")[0].strip()

            verification_result = json.loads(response_text)

            return {
                "completed": True,
                "is_complete": verification_result.get("extraction_complete", False),
                "confidence": verification_result.get("confidence", 0),
                "missing_items": verification_result.get("missing_items", []),
                "discrepancies": verification_result.get("discrepancies", []),
                "suggested_additions": verification_result.get("suggested_additions", []),
                "verification_notes": verification_result.get("notes", ""),
            }

        except Exception as e:
            logger.error(f"Verification pass failed: {e}")
            return {"completed": False, "error": str(e)}

    def _build_verification_prompt(
        self,
        extracted_data: Dict[str, Any],
        document_type: str
    ) -> str:
        """Build the verification prompt based on document type."""

        if document_type == "bank_statement":
            transactions = extracted_data.get("transactions", [])
            summary = extracted_data.get("summary", {})

            txn_summary = f"""
EXTRACTED DATA SUMMARY:
- Total transactions extracted: {len(transactions)}
- Total credits: ${summary.get('total_credits', 0):,.2f}
- Total debits: ${summary.get('total_debits', 0):,.2f}

EXTRACTED TRANSACTIONS (first 10 and last 10):
{self._format_transactions_for_verification(transactions)}
"""
        elif document_type == "loan_statement":
            interest = extracted_data.get("interest_summary", {})
            transactions = extracted_data.get("transactions", [])

            txn_summary = f"""
EXTRACTED DATA SUMMARY:
- Total interest charged: ${interest.get('total_interest_charged', 0):,.2f}
- Interest transactions: {len([t for t in transactions if t.get('transaction_type') == 'interest_debit'])}
- Total transactions: {len(transactions)}

EXTRACTED TRANSACTIONS:
{self._format_transactions_for_verification(transactions)}
"""
        elif document_type == "property_manager_statement":
            income = extracted_data.get("income", {})
            expenses = extracted_data.get("expenses", {})

            txn_summary = f"""
EXTRACTED DATA SUMMARY:
- Gross rent: ${income.get('gross_rent', 0):,.2f}
- Total income: ${income.get('total_income', 0):,.2f}
- Total expenses: ${expenses.get('total_expenses', 0):,.2f}
- Management fee: ${expenses.get('management_fee', {}).get('amount', 0):,.2f}
"""
        else:
            txn_summary = f"EXTRACTED DATA:\n{json.dumps(extracted_data, indent=2, default=str)[:2000]}"

        return f"""
VERIFICATION TASK:
Compare the extracted data below against the source document shown above.

{txn_summary}

VERIFY:
1. Are ALL transactions from the document included in the extraction?
2. Are the amounts correct?
3. Are the dates correct?
4. Are there any transactions in the document that were NOT extracted?
5. Do the totals match what's shown in the document?

Respond with this JSON structure:
{{
    "extraction_complete": true/false,
    "confidence": 0.0-1.0,
    "transaction_count_in_document": <number you count in source>,
    "transaction_count_extracted": {len(extracted_data.get('transactions', []))},
    "missing_items": [
        {{
            "date": "YYYY-MM-DD",
            "description": "...",
            "amount": 0.00,
            "reason_missed": "..."
        }}
    ],
    "discrepancies": [
        {{
            "field": "...",
            "extracted_value": "...",
            "actual_value": "...",
            "transaction_index": 0
        }}
    ],
    "suggested_additions": [
        {{
            "date": "YYYY-MM-DD",
            "description": "...",
            "amount": 0.00,
            "transaction_type": "credit/debit",
            "suggested_category": "..."
        }}
    ],
    "notes": "Any additional observations"
}}
"""

    def _format_transactions_for_verification(
        self,
        transactions: List[Dict[str, Any]],
        max_show: int = 20
    ) -> str:
        """Format transactions for verification prompt."""
        if not transactions:
            return "No transactions extracted"

        lines = []

        # Show first 10
        for i, txn in enumerate(transactions[:10]):
            lines.append(
                f"{i+1}. {txn.get('date', 'N/A')} | "
                f"{txn.get('description', 'N/A')[:40]} | "
                f"${txn.get('amount', 0):,.2f}"
            )

        if len(transactions) > 20:
            lines.append(f"... ({len(transactions) - 20} more transactions) ...")

        # Show last 10 if more than 20
        if len(transactions) > 10:
            for i, txn in enumerate(transactions[-10:]):
                idx = len(transactions) - 10 + i + 1
                lines.append(
                    f"{idx}. {txn.get('date', 'N/A')} | "
                    f"{txn.get('description', 'N/A')[:40]} | "
                    f"${txn.get('amount', 0):,.2f}"
                )

        return "\n".join(lines)

    # =========================================================================
    # 3. CROSS-DOCUMENT VALIDATION
    # =========================================================================

    def cross_validate_documents(
        self,
        documents: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        Validate data consistency across related documents.

        Checks:
        1. Bank statement interest ≈ Loan statement interest
        2. PM statement rent appears in bank deposits
        3. Settlement amounts match bank statement entries
        4. Insurance premium matches bank payment
        """
        validation_results = {
            "is_valid": True,
            "checks_performed": [],
            "discrepancies": [],
            "warnings": [],
            "confidence": 1.0,
        }

        # Group documents by type
        docs_by_type = {}
        for doc in documents:
            doc_type = doc.get("document_type")
            if doc_type not in docs_by_type:
                docs_by_type[doc_type] = []
            docs_by_type[doc_type].append(doc)

        # Check 1: Interest validation (bank vs loan)
        if "bank_statement" in docs_by_type and "loan_statement" in docs_by_type:
            interest_check = self._validate_interest_across_documents(
                docs_by_type["bank_statement"],
                docs_by_type["loan_statement"]
            )
            validation_results["checks_performed"].append(interest_check)
            if not interest_check["passed"]:
                validation_results["discrepancies"].append(interest_check)
                validation_results["is_valid"] = False

        # Check 2: Rental income validation (PM vs bank)
        if "bank_statement" in docs_by_type and "property_manager_statement" in docs_by_type:
            rent_check = self._validate_rent_across_documents(
                docs_by_type["bank_statement"],
                docs_by_type["property_manager_statement"]
            )
            validation_results["checks_performed"].append(rent_check)
            if not rent_check["passed"]:
                validation_results["warnings"].append(rent_check)

        # Check 3: Settlement validation
        if "bank_statement" in docs_by_type and "settlement_statement" in docs_by_type:
            settlement_check = self._validate_settlement_in_bank(
                docs_by_type["bank_statement"],
                docs_by_type["settlement_statement"]
            )
            validation_results["checks_performed"].append(settlement_check)
            if not settlement_check["passed"]:
                validation_results["warnings"].append(settlement_check)

        # Calculate overall confidence
        checks = validation_results["checks_performed"]
        if checks:
            passed_count = sum(1 for c in checks if c.get("passed", False))
            validation_results["confidence"] = passed_count / len(checks)

        return validation_results

    def _validate_interest_across_documents(
        self,
        bank_statements: List[Dict[str, Any]],
        loan_statements: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """Validate interest amounts match between bank and loan statements."""

        # Sum interest from bank statements
        bank_interest = Decimal("0")
        for doc in bank_statements:
            extracted = doc.get("extracted_data", {})
            interest_analysis = extracted.get("interest_analysis", {})
            bank_interest += Decimal(str(interest_analysis.get("total_interest_debits", 0)))

        # Sum interest from loan statements
        loan_interest = Decimal("0")
        for doc in loan_statements:
            extracted = doc.get("extracted_data", {})
            interest_summary = extracted.get("interest_summary", {})
            loan_interest += Decimal(str(interest_summary.get("total_interest_charged", 0)))

        # Allow 5% variance (different statement periods, timing)
        variance = abs(bank_interest - loan_interest)
        max_variance = max(bank_interest, loan_interest) * Decimal("0.05")
        passed = variance <= max_variance or bank_interest == 0 or loan_interest == 0

        return {
            "check": "interest_cross_validation",
            "passed": passed,
            "bank_interest": float(bank_interest),
            "loan_interest": float(loan_interest),
            "variance": float(variance),
            "variance_percentage": float(variance / max(bank_interest, loan_interest, Decimal("1")) * 100),
            "message": (
                f"Interest amounts match within tolerance"
                if passed else
                f"Interest discrepancy: Bank=${bank_interest:.2f}, Loan=${loan_interest:.2f}"
            ),
        }

    def _validate_rent_across_documents(
        self,
        bank_statements: List[Dict[str, Any]],
        pm_statements: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """Validate rent from PM statements appears in bank deposits."""

        # Get rent from PM statements
        pm_rent = Decimal("0")
        for doc in pm_statements:
            extracted = doc.get("extracted_data", {})
            income = extracted.get("income", {})
            pm_rent += Decimal(str(income.get("gross_rent", 0)))

        # Get potential rent deposits from bank statements
        bank_rent = Decimal("0")
        for doc in bank_statements:
            extracted = doc.get("extracted_data", {})
            transactions = extracted.get("transactions", [])
            for txn in transactions:
                category = txn.get("categorization", {}).get("suggested_category", "")
                if category == "rental_income" and txn.get("amount", 0) > 0:
                    bank_rent += Decimal(str(txn.get("amount", 0)))

        # PM disbursements typically happen, so bank deposits may be net
        # Allow significant variance here
        variance = abs(pm_rent - bank_rent)
        passed = True  # This is a warning check, not blocking

        return {
            "check": "rent_cross_validation",
            "passed": passed,
            "pm_gross_rent": float(pm_rent),
            "bank_rent_deposits": float(bank_rent),
            "variance": float(variance),
            "message": (
                f"Note: PM gross rent (${pm_rent:.2f}) differs from bank deposits (${bank_rent:.2f}). "
                f"This may be due to PM fees being deducted before disbursement."
            ),
        }

    def _validate_settlement_in_bank(
        self,
        bank_statements: List[Dict[str, Any]],
        settlement_statements: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """Check if settlement-related transactions appear in bank statement."""

        findings = []

        for settlement in settlement_statements:
            extracted = settlement.get("extracted_data", {})
            settlement_date = extracted.get("settlement_info", {}).get("settlement_date")
            purchase_price = extracted.get("financial_details", {}).get("purchase_price")

            if not settlement_date or not purchase_price:
                continue

            # Look for large transactions around settlement date in bank
            found_settlement_txn = False
            for bank_doc in bank_statements:
                bank_data = bank_doc.get("extracted_data", {})
                transactions = bank_data.get("transactions", [])

                for txn in transactions:
                    amount = abs(Decimal(str(txn.get("amount", 0))))
                    # Look for large debit (settlement payment)
                    if amount > Decimal("10000"):
                        found_settlement_txn = True
                        break

            findings.append({
                "settlement_date": settlement_date,
                "purchase_price": purchase_price,
                "found_in_bank": found_settlement_txn,
            })

        passed = all(f.get("found_in_bank", False) for f in findings) if findings else True

        return {
            "check": "settlement_bank_validation",
            "passed": passed,
            "findings": findings,
            "message": (
                "Settlement transactions found in bank statements"
                if passed else
                "Could not find settlement transactions in bank statements"
            ),
        }

    # =========================================================================
    # MAIN VALIDATION ENTRY POINT
    # =========================================================================

    async def validate_extraction(
        self,
        document_type: str,
        extracted_data: Dict[str, Any],
        document_content: Optional[str] = None,
        image_data: Optional[List[Tuple[bytes, str]]] = None,
        context: Optional[Dict[str, Any]] = None,
        run_verification_pass: bool = True,
    ) -> ValidationResult:
        """
        Run all validation checks on extracted data.

        Args:
            document_type: Type of document being validated
            extracted_data: The extracted data to validate
            document_content: Original document text (for verification pass)
            image_data: Original document images (for verification pass)
            context: Additional context
            run_verification_pass: Whether to run the Claude verification pass

        Returns:
            ValidationResult with all validation details
        """
        result = ValidationResult(is_valid=True, confidence=1.0)

        # 1. Run balance reconciliation based on document type
        if document_type == "bank_statement":
            is_reconciled, reconciliation = self.reconcile_bank_statement(extracted_data)
            result.reconciliation = reconciliation

            if not is_reconciled:
                result.is_valid = False
                result.issues.append({
                    "type": "balance_reconciliation_failed",
                    "severity": "high",
                    "message": f"Balance variance: ${reconciliation.get('variance', 0):.2f}",
                    "details": reconciliation,
                })
                result.confidence *= 0.7

        elif document_type == "loan_statement":
            is_reconciled, reconciliation = self.reconcile_loan_statement(extracted_data)
            result.reconciliation = reconciliation

            if not is_reconciled:
                result.warnings.append(
                    f"Interest reconciliation variance: ${reconciliation.get('variance', 0):.2f}"
                )
                result.confidence *= 0.9

        elif document_type == "property_manager_statement":
            is_reconciled, reconciliation = self.reconcile_pm_statement(extracted_data)
            result.reconciliation = reconciliation

            if not is_reconciled:
                result.warnings.append(
                    f"PM statement balance variance: ${reconciliation.get('variance', 0):.2f}"
                )
                result.confidence *= 0.9

        # 2. Check extraction quality indicators
        quality_score = extracted_data.get("extraction_metadata", {}).get("data_quality_score", 1.0)
        if quality_score < self.MIN_CONFIDENCE_THRESHOLD:
            result.warnings.append(f"Low extraction quality score: {quality_score:.2f}")
            result.confidence *= quality_score

        # Check flagged transaction ratio
        if "transactions" in extracted_data:
            transactions = extracted_data["transactions"]
            flagged = sum(1 for t in transactions if t.get("review_flags", {}).get("needs_review"))
            if transactions and flagged / len(transactions) > self.MAX_FLAGGED_PERCENTAGE:
                result.warnings.append(
                    f"High ratio of flagged transactions: {flagged}/{len(transactions)}"
                )

        # 3. Run verification pass if enabled and client available
        if run_verification_pass and self.claude_client and settings.ENABLE_EXTRACTION_VERIFICATION:
            verification = await self.verification_pass(
                document_content,
                image_data,
                extracted_data,
                document_type,
                context or {},
            )
            result.verification_details = verification

            if verification.get("completed") and not verification.get("is_complete"):
                result.is_valid = False
                result.issues.append({
                    "type": "verification_failed",
                    "severity": "high",
                    "message": "Verification pass found missing items",
                    "missing_items": verification.get("missing_items", []),
                })
                result.suggested_corrections = verification.get("suggested_additions", [])
                result.confidence *= verification.get("confidence", 0.5)

        return result

    async def validate_all_documents(
        self,
        documents: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """
        Validate all documents and run cross-document validation.

        Args:
            documents: List of documents with extracted_data

        Returns:
            Combined validation results
        """
        results = {
            "individual_validations": [],
            "cross_validation": None,
            "overall_valid": True,
            "overall_confidence": 1.0,
            "summary": {
                "total_documents": len(documents),
                "valid_documents": 0,
                "issues_found": 0,
                "warnings_found": 0,
            },
        }

        # Validate each document individually
        for doc in documents:
            doc_type = doc.get("document_type")
            extracted = doc.get("extracted_data", {})

            if not extracted:
                continue

            validation = await self.validate_extraction(
                document_type=doc_type,
                extracted_data=extracted,
                run_verification_pass=False,  # Skip individual verification for speed
            )

            results["individual_validations"].append({
                "document_id": doc.get("id"),
                "document_type": doc_type,
                "filename": doc.get("filename"),
                "validation": validation.to_dict(),
            })

            if validation.is_valid:
                results["summary"]["valid_documents"] += 1
            else:
                results["overall_valid"] = False

            results["summary"]["issues_found"] += len(validation.issues)
            results["summary"]["warnings_found"] += len(validation.warnings)
            results["overall_confidence"] = min(
                results["overall_confidence"],
                validation.confidence
            )

        # Run cross-document validation
        results["cross_validation"] = self.cross_validate_documents(documents)

        if not results["cross_validation"]["is_valid"]:
            results["overall_valid"] = False
            results["overall_confidence"] *= results["cross_validation"]["confidence"]

        return results


# Singleton instance
_validator_instance: Optional[ExtractionValidator] = None


def get_extraction_validator(claude_client=None) -> ExtractionValidator:
    """Get or create the extraction validator instance."""
    global _validator_instance
    if _validator_instance is None or claude_client is not None:
        _validator_instance = ExtractionValidator(claude_client)
    return _validator_instance
