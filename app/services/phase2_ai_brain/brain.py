"""
AI Brain - The core orchestrator for tax return processing.

Implements the accountant workflow:
1. Review Property Manager Statements
2. Review Bank Statements (cross-reference with PM)
3. Review Loan Statements (match to bank payments)
4. Review Invoices (match to payments, check >$800 rule)
5. Cross-validation between documents
6. Completeness check (verify ALL documents processed)
7. QA validation (verify calculation accuracy)
8. Auto-correction if calculation errors detected
"""

import json
import logging
import time
from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional
from uuid import UUID

import httpx
from anthropic import AsyncAnthropic

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.config import settings
from sqlalchemy.orm.attributes import flag_modified

from app.models.db_models import (
    Document,
    TaxReturn,
    TaxReturnWorkings,
    WorkingsFlag,
    DocumentRequest,
    ClientQuestion,
    DocumentInventoryRecord,
    WorkingsStatus,
    FlagSeverity,
    FlagCategory,
)
from app.services.phase1_document_intake.claude_client import ClaudeClient
from app.services.phase1_document_intake.extraction_validator import ExtractionValidator
from app.services.phase2_ai_brain.workings_models import (
    TaxReturnWorkingsData,
    WorkingsSummary,
    IncomeWorkings,
    ExpenseWorkings,
    LineItem,
    RepairsLineItem,
    RepairItem,
    WorkingsFlag as WorkingsFlagData,
    DocumentRequestData,
    ClientQuestionData,
    DocumentsStatus,
    DocumentStatusData,
    VerificationStatus,
    FlagSeverity as FlagSeverityEnum,
    FlagCategory as FlagCategoryEnum,
    CalculationLogic,
)
from app.services.phase2_feedback_learning.knowledge_store import knowledge_store
from app.rules.loader import load_categorization_rules
from app.services.tax_rules_service import TaxRulesService

logger = logging.getLogger(__name__)


def _safe_abs(value, default=0):
    """Safely get absolute value, handling None values."""
    if value is None:
        return default
    try:
        return abs(value)
    except TypeError:
        return default


def _safe_decimal(value, default=Decimal("0")) -> Decimal:
    """Safely convert a value to Decimal, handling invalid inputs.

    Handles None, empty strings, non-numeric strings, and other edge cases
    that Claude might return.
    """
    if value is None:
        return default

    # Convert to string first
    str_val = str(value).strip()

    # Handle empty strings
    if not str_val:
        return default

    # Handle common non-numeric responses from Claude
    if str_val.lower() in ('none', 'null', 'n/a', 'na', 'missing', 'unknown', '-'):
        return default

    try:
        return Decimal(str_val)
    except Exception:
        # If conversion fails, try to extract a number
        try:
            # Remove currency symbols and commas
            cleaned = str_val.replace('$', '').replace(',', '').strip()
            if cleaned:
                return Decimal(cleaned)
        except Exception:
            pass
        return default


def _sanitize_monthly_breakdown(breakdown):
    """Sanitize monthly_breakdown dict, converting non-numeric values to None.

    Claude sometimes returns strings like 'MISSING' or 'Not available...' for months
    without data. This function filters those out to prevent Pydantic validation errors.
    """
    if not breakdown or not isinstance(breakdown, dict):
        return None

    sanitized = {}
    for month, value in breakdown.items():
        if value is None:
            continue
        try:
            # Try to convert to float
            sanitized[month] = float(value)
        except (ValueError, TypeError):
            # Skip non-numeric values like 'MISSING', 'Not available', etc.
            continue

    return sanitized if sanitized else None


class AIBrain:
    """
    AI Brain - Orchestrates tax return processing using accountant workflow.

    Instead of rule-based categorization, the AI Brain:
    1. Receives ALL context (documents, RAG learnings, rules)
    2. Processes documents in accountant order (PM → Bank → Loan → Invoices)
    3. Cross-references between documents
    4. Generates complete workings with flags and requests
    """

    PROMPT_VERSION = "2.0.0"

    def __init__(self):
        """Initialize AI Brain."""
        self.claude_client = ClaudeClient()
        self.yaml_rules = load_categorization_rules()
        self.tax_rules_service = TaxRulesService()

    async def process_tax_return(
        self,
        tax_return_id: UUID,
        db: AsyncSession,
        force_reprocess: bool = False
    ) -> TaxReturnWorkingsData:
        """
        Process a tax return and generate workings.

        Args:
            tax_return_id: Tax return to process
            db: Database session
            force_reprocess: Reprocess even if workings exist

        Returns:
            Complete TaxReturnWorkingsData
        """
        start_time = time.time()

        logger.info(f"AI Brain processing tax return: {tax_return_id}")

        # Check for existing workings
        if not force_reprocess:
            existing = await self._get_existing_workings(tax_return_id, db)
            if existing:
                logger.info(f"Found existing workings for {tax_return_id}")
                # Could return existing or continue to reprocess

        # Step 1: Load all context
        context = await self._load_context(tax_return_id, db)

        # Step 2: Build the comprehensive prompt
        prompt = self._build_accountant_prompt(context)

        # Step 3: Send to Claude
        logger.info("Sending to Claude AI Brain...")
        response = await self._call_claude(prompt)

        # Step 4: Parse response into workings
        workings = self._parse_claude_response(response, context)

        # Step 5: Calculate totals
        workings.calculate_all_totals()

        # Step 6: QA Validation - verify all calculations are accurate
        logger.info("Running QA validation on calculations...")
        qa_issues = await self._qa_validate_calculations(workings, context)

        # Step 7: If issues found, have Claude verify and correct
        if qa_issues:
            logger.info(f"QA found {len(qa_issues)} issues, running verification...")
            workings = await self._qa_verify_with_claude(workings, context, qa_issues)
            # Recalculate totals after corrections
            workings.calculate_all_totals()
        else:
            workings.processing_notes.append("QA: All calculations verified - no issues found")

        # Step 8: Save to database
        processing_time = time.time() - start_time
        await self._save_workings(workings, tax_return_id, processing_time, db)

        logger.info(
            f"AI Brain completed for {tax_return_id} in {processing_time:.2f}s. "
            f"Income: ${workings.summary.total_income}, "
            f"Deductions: ${workings.summary.total_deductions}, "
            f"Flags: {len(workings.flags)}"
        )

        return workings

    async def recalculate_line_item(
        self,
        db: AsyncSession,
        tax_return_id: UUID,
        category: str,  # 'income' or 'expense'
        item_key: str,  # e.g., 'rates', 'interest', 'rental_income'
        expected_value: Optional[float] = None
    ) -> Dict[str, Any]:
        """
        Recalculate a single line item using learnings and optionally expected value.

        This is much faster than full recalculation as it only updates one item.

        Args:
            db: Database session
            tax_return_id: Tax return ID
            category: 'income' or 'expense'
            item_key: The specific line item key
            expected_value: Optional user-provided expected value

        Returns:
            Dict with success status, old_value, new_value
        """
        logger.info(f"Recalculating {category}.{item_key} for tax return {tax_return_id}")

        try:
            # Get existing workings
            result = await db.execute(
                select(TaxReturnWorkings).where(
                    TaxReturnWorkings.tax_return_id == tax_return_id
                ).order_by(TaxReturnWorkings.version.desc()).limit(1)
            )
            workings_record = result.scalar_one_or_none()

            if not workings_record:
                return {"success": False, "error": "No workings found"}

            # Get the current workings data
            income_workings = workings_record.income_workings or {}
            expense_workings = workings_record.expense_workings or {}

            # Get the current value
            if category == 'income':
                current_item = income_workings.get(item_key, {})
            else:
                current_item = expense_workings.get(item_key, {})

            old_value = float(current_item.get('gross_amount', 0) if current_item else 0)

            # Determine new value - either from user or from Claude with learnings
            if expected_value is not None:
                new_value = expected_value
                source = "user_provided"
            else:
                # Call Claude to recalculate using learnings
                ai_result = await self._recalculate_with_claude(
                    db=db,
                    tax_return_id=tax_return_id,
                    category=category,
                    item_key=item_key,
                    current_item=current_item,
                    workings_record=workings_record
                )
                if not ai_result.get('success'):
                    logger.warning(f"AI recalculation failed for {item_key}: {ai_result.get('error', 'Unknown error')}")
                    return ai_result
                new_value = ai_result.get('new_value', old_value)
                source = "ai_recalculated"
                logger.info(f"AI recalculation succeeded for {item_key}: old={old_value}, new={new_value}")

            # Update the line item
            updated_item = dict(current_item) if current_item else {}
            updated_item['gross_amount'] = str(new_value)
            updated_item['deductible_amount'] = str(new_value)  # Assuming 100% deductible for expenses
            updated_item['verification_status'] = 'ai_recalculated' if source == 'ai_recalculated' else 'user_corrected'
            correction_note = f" [AI recalculated from ${old_value:.2f} to ${new_value:.2f} using learnings]" if source == 'ai_recalculated' else f" [User corrected from ${old_value:.2f} to ${new_value:.2f}]"
            updated_item['notes'] = (updated_item.get('notes', '') + correction_note).strip()

            # Save back to workings
            # IMPORTANT: SQLAlchemy doesn't detect changes to mutable JSONB fields
            # We must use flag_modified() to tell SQLAlchemy the field changed
            if category == 'income':
                income_workings[item_key] = updated_item
                workings_record.income_workings = income_workings
                flag_modified(workings_record, 'income_workings')
            else:
                expense_workings[item_key] = updated_item
                workings_record.expense_workings = expense_workings
                flag_modified(workings_record, 'expense_workings')

            # Recalculate totals
            total_income = Decimal("0")
            total_expenses = Decimal("0")

            for key, item in income_workings.items():
                if isinstance(item, dict) and item.get('gross_amount'):
                    try:
                        total_income += _safe_decimal(item.get('gross_amount'))
                    except (ValueError, TypeError):
                        pass

            for key, item in expense_workings.items():
                if isinstance(item, dict):
                    amt = item.get('deductible_amount') or item.get('gross_amount')
                    if amt:
                        try:
                            total_expenses += _safe_decimal(amt)
                        except (ValueError, TypeError):
                            pass

            workings_record.total_income = total_income
            workings_record.total_expenses = total_expenses
            workings_record.total_deductions = total_expenses
            workings_record.net_rental_income = total_income - total_expenses

            await db.commit()

            logger.info(f"Updated {category}.{item_key}: ${old_value:.2f} -> ${new_value:.2f}")

            return {
                "success": True,
                "old_value": old_value,
                "new_value": new_value,
                "item_key": item_key,
                "category": category
            }

        except Exception as e:
            logger.error(f"Error recalculating line item: {e}")
            await db.rollback()
            return {"success": False, "error": str(e)}

    async def _recalculate_with_claude(
        self,
        db: AsyncSession,
        tax_return_id: UUID,
        category: str,
        item_key: str,
        current_item: Dict[str, Any],
        workings_record: TaxReturnWorkings
    ) -> Dict[str, Any]:
        """
        Use Claude to recalculate a single line item using learnings.

        Args:
            db: Database session
            tax_return_id: Tax return ID
            category: 'income' or 'expense'
            item_key: The specific line item key
            current_item: Current item data
            workings_record: The workings record

        Returns:
            Dict with success, new_value, reasoning
        """
        try:
            # Get relevant learnings for this item from database
            # (Pinecone only stores embeddings and IDs, content is in PostgreSQL)
            learnings_text = ""
            try:
                from app.models.db_models import SkillLearning
                from sqlalchemy import select, or_

                # Search for learnings related to this item
                query = select(SkillLearning).where(
                    SkillLearning.skill_name == 'nz_rental_returns',
                    SkillLearning.is_active == True,
                    or_(
                        SkillLearning.category_code == item_key,
                        SkillLearning.title.ilike(f'%{item_key}%'),
                        SkillLearning.content.ilike(f'%{item_key}%')
                    )
                ).order_by(SkillLearning.created_at.desc()).limit(5)

                result = await db.execute(query)
                db_learnings = result.scalars().all()

                if db_learnings:
                    learnings_text = "\n\n=== RELEVANT LEARNINGS (YOU MUST APPLY THESE) ===\n"
                    for idx, learning in enumerate(db_learnings, 1):
                        learnings_text += f"{idx}. {learning.title}\n"
                        learnings_text += f"   Type: {learning.learning_type}\n"
                        learnings_text += f"   Content: {learning.content}\n\n"
                    logger.info(f"Found {len(db_learnings)} learnings from database for {item_key}")
                else:
                    logger.info(f"No learnings found in database for {item_key}")
            except Exception as e:
                logger.warning(f"Failed to fetch learnings from database: {e}")

            # Fetch actual transactions for this category from the database
            transactions_text = ""
            try:
                from app.models.db_models import Transaction

                # Query transactions matching this category
                txn_query = select(Transaction).where(
                    Transaction.tax_return_id == tax_return_id,
                    Transaction.category_code == item_key
                ).order_by(Transaction.transaction_date)

                txn_result = await db.execute(txn_query)
                transactions = txn_result.scalars().all()

                if transactions:
                    transactions_text = "\n\n=== ACTUAL TRANSACTIONS FOR THIS CATEGORY ===\n"
                    transactions_text += f"Total {len(transactions)} transactions found:\n\n"
                    total_amount = Decimal("0")
                    for txn in transactions:
                        txn_amount = abs(txn.amount) if txn.amount else Decimal("0")
                        total_amount += txn_amount
                        transactions_text += f"- {txn.transaction_date}: {txn.description[:60]}"
                        if txn.other_party:
                            transactions_text += f" ({txn.other_party})"
                        transactions_text += f" = ${txn_amount:.2f}\n"
                    transactions_text += f"\nSUM OF ALL TRANSACTIONS: ${total_amount:.2f}\n"
                    logger.info(f"Found {len(transactions)} transactions for {item_key}, total: ${total_amount:.2f}")
                else:
                    logger.info(f"No transactions found for category {item_key}")
            except Exception as e:
                logger.warning(f"Failed to fetch transactions: {e}")

            # Get document inventory for context
            document_inventory = workings_record.document_inventory or {}

            # Build focused prompt for Claude
            current_value = current_item.get('gross_amount', 0) if current_item else 0
            current_notes = current_item.get('notes', '') if current_item else ''
            current_calc = current_item.get('calculation_logic', {}) if current_item else {}

            prompt = f"""You are recalculating a SINGLE line item for a NZ rental property tax return.

ITEM TO RECALCULATE: {item_key} ({category})
CURRENT VALUE: ${current_value}
CURRENT NOTES: {current_notes}
CURRENT CALCULATION: {json.dumps(current_calc, indent=2) if current_calc else 'None'}
{transactions_text}
DOCUMENT INVENTORY:
{json.dumps(document_inventory, indent=2) if document_inventory else 'No inventory available'}
{learnings_text}

CRITICAL INSTRUCTIONS:
1. You have access to the ACTUAL TRANSACTIONS above - use these real numbers to calculate the correct total
2. If there are LEARNINGS provided, apply them to modify how you calculate (e.g., exclude certain items, apply adjustments)
3. The user is asking you to RECALCULATE using the actual transaction data and any learnings
4. Sum up the relevant transactions, applying any adjustments from the learnings

Respond with ONLY a JSON object in this exact format:
{{
    "new_value": <number - the RECALCULATED value based on transactions and learnings>,
    "reasoning": "<how you calculated the new value>",
    "calculation_steps": ["step 1", "step 2", ...]
}}

If you cannot determine a new value, respond with:
{{
    "new_value": null,
    "reasoning": "<explanation of why you cannot calculate>",
    "calculation_steps": []
}}
"""

            # Call Claude
            logger.info(f"Calling Claude to recalculate {item_key}")
            response_text = await self.claude_client.extract_with_prompt(prompt=prompt)

            # Log raw response for debugging
            logger.info(f"Claude raw response for {item_key}: {response_text[:500]}...")

            # Parse response

            # Extract JSON from response
            import re
            json_match = re.search(r'\{[^{}]*"new_value"[^{}]*\}', response_text, re.DOTALL)
            if not json_match:
                # Try to find any JSON object
                json_match = re.search(r'\{.*\}', response_text, re.DOTALL)

            if json_match:
                try:
                    result = json.loads(json_match.group())
                    new_value = result.get('new_value')

                    if new_value is not None:
                        logger.info(f"Claude recalculated {item_key}: {new_value} - {result.get('reasoning', '')}")
                        return {
                            "success": True,
                            "new_value": float(new_value),
                            "reasoning": result.get('reasoning', ''),
                            "calculation_steps": result.get('calculation_steps', [])
                        }
                    else:
                        logger.warning(f"Claude returned null value for {item_key}: {result.get('reasoning', 'No reason given')}")
                        return {
                            "success": False,
                            "error": f"Claude couldn't determine value: {result.get('reasoning', 'Unknown reason')}"
                        }
                except json.JSONDecodeError as e:
                    logger.error(f"Failed to parse Claude response: {e}")
                    return {"success": False, "error": f"Failed to parse AI response: {str(e)}"}
            else:
                return {"success": False, "error": "No valid JSON in AI response"}

        except Exception as e:
            logger.error(f"Error in Claude recalculation: {e}")
            return {"success": False, "error": str(e)}

    async def _load_context(self, tax_return_id: UUID, db: AsyncSession) -> Dict[str, Any]:
        """Load all context needed for processing."""

        # Get tax return
        result = await db.execute(
            select(TaxReturn).where(TaxReturn.id == tax_return_id)
        )
        tax_return = result.scalar_one_or_none()
        if not tax_return:
            raise ValueError(f"Tax return not found: {tax_return_id}")

        # Get all documents
        result = await db.execute(
            select(Document).where(
                Document.tax_return_id == tax_return_id,
                Document.is_excluded.is_(False)
            )
        )
        documents = result.scalars().all()

        # Get document inventory if exists
        result = await db.execute(
            select(DocumentInventoryRecord).where(
                DocumentInventoryRecord.tax_return_id == tax_return_id
            )
        )
        inventory_record = result.scalar_one_or_none()
        inventory_data = inventory_record.inventory_data if inventory_record else None

        # Get tax rules for this return
        tax_rules = await self.tax_rules_service.get_rules_for_return(
            tax_year=tax_return.tax_year,
            property_type=tax_return.property_type.value if hasattr(tax_return.property_type, 'value') else tax_return.property_type,
            db=db
        )

        # Get RAG learnings (if available) - expanded for better accuracy
        rag_learnings = []
        try:
            if knowledge_store:
                # Query for relevant transaction patterns
                transaction_learnings = await knowledge_store.search(
                    query=f"rental property tax transactions categorization {tax_return.property_address}",
                    namespace="transaction-coding",
                    top_k=20
                )
                rag_learnings.extend(transaction_learnings)

                # Also query for skill learnings (domain knowledge)
                skill_learnings = await knowledge_store.search(
                    query="rental property deductions interest rates insurance repairs",
                    namespace="skill_learnings",
                    top_k=10
                )
                rag_learnings.extend(skill_learnings)

                logger.info(f"Loaded {len(rag_learnings)} RAG learnings for context")
        except Exception as e:
            logger.warning(f"Could not load RAG learnings: {e}")

        # Organize documents by type
        documents_by_type = {}
        for doc in documents:
            doc_type = doc.document_type or "unknown"
            if doc_type not in documents_by_type:
                documents_by_type[doc_type] = []

            # Validate and correct extracted_data before using
            extracted_data = doc.extracted_data.copy() if doc.extracted_data else {}
            extracted_data = self._validate_extracted_data(extracted_data, doc_type)

            documents_by_type[doc_type].append({
                "id": str(doc.id),
                "filename": doc.original_filename,
                "file_path": doc.file_path,  # Include file path for raw content reading
                "document_type": doc_type,
                "extracted_data": extracted_data,
                "confidence": doc.classification_confidence
            })

        # Detect potential bank contributions that may have been missed
        potential_bank_contributions = self._detect_potential_bank_contributions(documents_by_type)
        if potential_bank_contributions:
            logger.info(f"Detected {len(potential_bank_contributions)} potential bank contributions for review")

        return {
            "tax_return": {
                "id": str(tax_return.id),
                "property_address": tax_return.property_address,
                "tax_year": tax_return.tax_year,
                "property_type": tax_return.property_type.value if hasattr(tax_return.property_type, 'value') else tax_return.property_type,
                "year_of_ownership": tax_return.year_of_ownership,
                "gst_registered": tax_return.gst_registered,
                "client_id": str(tax_return.client_id)
            },
            "documents": documents,
            "documents_by_type": documents_by_type,
            "inventory": inventory_data,
            "tax_rules": tax_rules,
            "yaml_rules": self.yaml_rules,
            "rag_learnings": rag_learnings,
            "potential_bank_contributions": potential_bank_contributions
        }

    def _build_accountant_prompt(self, context: Dict[str, Any]) -> str:
        """Build the comprehensive accountant workflow prompt."""

        tax_return = context["tax_return"]
        documents_by_type = context["documents_by_type"]
        tax_rules = context["tax_rules"]

        # Get interest deductibility percentage
        # Default based on property type and tax year (conservative approach)
        # - New builds (CCC after 27 March 2020): 100% deductible
        # - Existing properties: 80% (FY25), 100% (FY26+)
        # - Unknown/NOT_SURE: Use conservative 80% for FY25
        property_type = tax_return.get("property_type", "").lower()
        tax_year = tax_return.get("tax_year", "FY25")

        # Determine default based on property type and year
        if property_type == "new_build":
            interest_percentage = 100.0
        elif tax_year in ("FY25", "FY2025"):
            interest_percentage = 80.0  # Conservative default for FY25
        else:
            interest_percentage = 100.0  # FY26+ or unknown year

        # Override with tax_rules if available
        if tax_rules:
            # tax_rules is a dict keyed by rule_type
            if isinstance(tax_rules, dict):
                interest_rule = tax_rules.get("interest_deductibility", {})
                if isinstance(interest_rule, dict) and "percentage" in interest_rule:
                    interest_percentage = interest_rule.get("percentage")
            elif isinstance(tax_rules, list):
                # Legacy list format support
                for rule in tax_rules:
                    if isinstance(rule, dict) and rule.get("rule_type") == "interest_deductibility":
                        interest_percentage = rule.get("value", {}).get("percentage", interest_percentage)
                        break

        # Format documents summary
        docs_summary = self._format_documents_summary(documents_by_type)

        # Format extracted data
        extracted_data = self._format_extracted_data(documents_by_type)

        # Format RAG learnings
        rag_context = self._format_rag_learnings(context.get("rag_learnings", []))

        # Format potential bank contributions
        potential_contributions = context.get("potential_bank_contributions", [])
        bank_contrib_section = ""
        if potential_contributions:
            bank_contrib_section = "\n\n## ⚠️ DETECTED POTENTIAL BANK CONTRIBUTIONS (REQUIRES ACCOUNTANT REVIEW)\n"
            bank_contrib_section += "The following transactions have been flagged as potential bank contributions:\n"
            for contrib in potential_contributions:
                bank_contrib_section += f"- **${contrib['amount']:.2f}** on {contrib['date']}: {contrib['description']}\n"
                bank_contrib_section += f"  - Reason: {contrib['reason']}\n"
                bank_contrib_section += f"  - Settlement date: {contrib['settlement_date']} ({contrib['days_from_settlement']} days difference)\n"
            bank_contrib_section += "\n**ACTION REQUIRED**: Include these as Bank Contribution (Row 8) income with flag: 'Verify with accountant'\n"

        prompt = f"""You are an experienced New Zealand property tax accountant preparing workings for a rental property tax return.

## Property Details
- Address: {tax_return["property_address"]}
- Tax Year: {tax_return["tax_year"]}
- Property Type: {tax_return["property_type"]}
- Year of Ownership: {tax_return["year_of_ownership"]}
- Interest Deductibility: {interest_percentage}% (based on property type and tax year)

## Documents Provided
{docs_summary}{bank_contrib_section}

## Important Notes on Data Quality
- Transaction categories shown in brackets [category] are from Phase 1 AI - treat as SUGGESTIONS only
- Verify each categorization independently based on the description and context
- Flags marked with ⚠️ indicate items requiring attention
- Some transactions may be miscategorized - use your judgment

## Your Task
Follow this EXACT workflow to prepare the tax return workings:

### STEP 1: Property Manager Statements
- IF PM statement exists: This is your PRIMARY source for rent and PM-related expenses
- Extract: Gross rent collected, management fees, letting fees, repairs through PM
- IF NO PM statement: Note this and rely on bank statements for rent identification

### STEP 2: Bank Statements
- Cross-reference with PM statement: Do deposits from PM match?
- If no PM: Identify direct tenant rent payments
- Identify expenses: Rates (council), insurance, water rates, bank fees, loan payments
- Flag any expected expense not found (e.g., no rates = unusual)

⚠️ **CRITICAL: BANK CONTRIBUTION DETECTION** (usually on/near settlement date)
Look for credits that are bank cashback/incentives - these are TAXABLE INCOME:

**Patterns to detect:**
- "Bank Init", "Bank Initiated", "BANK INIT" + reference number
- "Cash Contribution", "Cash Contrib", "Cashback"
- Large unexplained credit ($2,000-$10,000) on settlement date

**If found:**
- Category: bank_contribution (Row 8 - Bank Contribution)
- This is TAXABLE INCOME - do NOT miss it!
- Do NOT categorize as transfer, capital, or unknown
- **FLAG for accountant review**: needs_review=true, reasons=["Bank contribution - verify with accountant"]

**If settlement statement mentions cashback but no bank statement evidence:**
- FLAG for review but do NOT include in P&L

### STEP 3: Loan Statements
- Match bank statement loan payments to loan statement
- Extract INTEREST component only (principal is NOT deductible)
- Apply {interest_percentage}% deductibility rule
- Flag if bank shows loan payments but no loan statement provided

### STEP 4: Invoices & Other Documents (CRITICAL - DO NOT SKIP)
**IMPORTANT: Review EVERY document that is not a bank/loan/PM statement**
This includes documents classified as: other, invoice, unknown, meth_test, healthy_homes, lim_report, etc.

For EACH non-standard document:
1. Identify what it is (invoice, certificate, report, etc.)
2. Determine if it's a deductible expense
3. Categorize appropriately (many go to Due Diligence - Row 18)

**Common invoices to look for:**
- Valocity / Property valuations → Due Diligence
- Meth testing reports → Due Diligence
- Healthy homes assessments → Due Diligence
- LIM reports → Due Diligence
- Building inspections → Due Diligence
- Depreciation schedules (FordBaker, Valuit) → Depreciation
- Repair/tradesperson invoices → Repairs & Maintenance
- Insurance certificates → Insurance

**RULE: Repairs over $800 MUST have invoice - flag if missing**
**RULE: Match each invoice to a bank or PM payment if possible**

### STEP 5: Cross-Validation
- Verify all sources reconcile
- Identify any mismatches or anomalies
- Generate flags for items needing attention

### STEP 6: Completeness Check (MANDATORY)
Before finalizing, verify you have:
1. ✓ Processed EVERY document listed above (count them!)
2. ✓ Checked ALL "other" or "unknown" documents for deductible expenses
3. ✓ Included Due Diligence costs (Valocity, meth tests, LIM, healthy homes, etc.)
4. ✓ Not missed any invoices - these often contain deductible expenses
5. ✓ Accounted for ALL transactions from bank statements

**If a document exists but you haven't extracted value from it, explain why in the processing_notes.**

{rag_context}

## CRITICAL BUSINESS RULES (Romulus Logic)

### INTEREST DETECTION RULES

**DATA SOURCE PRIORITY (CRITICAL):**
1. **CSV loan statements** - ALWAYS prefer CSV files with raw transactions (e.g., HomeLoan.CSV)
2. PDF loan statements - Use only if CSV not available
3. Bank statements - Use for cross-validation

**WHY CSV IS PREFERRED:**
- CSV contains individual transaction records with exact dates and amounts
- PDF statements may have summarized/rounded monthly totals that miss transactions
- Weekly interest payments can be miscounted when grouped by month in PDFs

**CALCULATION METHOD:**
1. Find ALL "LOAN INTEREST" transactions in the CSV file
2. Sum each individual transaction amount
3. Cross-validate total against PDF summary (flag if >$1 difference)

**INCLUDE as interest expense (must be DEBIT/money out):**
- "Debit Interest"
- "Loan Interest"
- "Interest Charged"
- "Mortgage Interest"

**EXCLUDE from interest expense (even if contains "interest"):**
- Interest CREDITS or refunds (money IN)
- "Interest Adjustment" entries (backdated corrections)
- "OFFSET Benefit" entries (shows savings, not charges)
- Interest on savings/deposit accounts (this is INCOME)
- Capitalised interest (added to loan principal)
- Any CREDIT transaction

**Interest Frequency Guide:**
- ~24-26 transactions/year = BI-WEEKLY charging (weekly payments)
- ~12-13 transactions/year = MONTHLY charging
- ~52 transactions/year = WEEKLY charging
- If significantly fewer: Check for offset account or partial year

**COMMON ERROR: December/March Interest**
- December and March often have 5 weekly payments (not 4)
- PDF summaries may incorrectly group week 5 into the next month
- ALWAYS count individual transactions from CSV to avoid this error

**Offset Account Handling:**
If interest appears unusually low, check for offset account indicators.
Low interest with offset is CORRECT - do NOT flag as error.
Sum only actual "LOAN INTEREST" debits, exclude "OFFSET Benefit" entries.

### INTEREST DEDUCTIBILITY PERCENTAGE RULES (NZ TAX LAW)

**⚠️ CRITICAL: Apply the correct deductibility percentage based on property type and tax year:**

| Property Type | FY25 (2024-25) | FY26 (2025-26) | FY27+ |
|--------------|----------------|----------------|-------|
| **New Build** (CCC after 27 March 2020) | 100% | 100% | 100% |
| **Existing Property** | 80% | 100% | 100% |
| **Unknown/NOT_SURE** | 80% (conservative) | 100% | 100% |

**Key Rules:**
1. **New Build = 100% deductible** ONLY if Code Compliance Certificate (CCC) was issued after 27 March 2020
2. **Existing = 80% for FY25** - This is the current year's phased deductibility rule
3. **When in doubt, use 80%** - It's safer to under-claim than over-claim
4. **Do NOT default to 100%** unless property type is CONFIRMED as new_build with CCC evidence

**Calculation:**
```
Deductible Interest = Gross Interest × Deductibility Percentage
Example (Existing, FY25): $10,000 × 80% = $8,000 deductible
```

**The current return uses: {interest_percentage}% deductibility**

### YEAR 1 SETTLEMENT STATEMENT RULES
If Year 1 (property purchased this FY), settlement statement is MANDATORY.

**Rates Calculation (Year 1):**
```
Total Deductible Rates = Bank Rates Paid + (Vendor Instalment − Vendor Credit)
```
Where:
- Bank Rates Paid = Sum of rates payments from bank statement/workbook
- Vendor Instalment = The rates instalment the vendor had already paid (from settlement statement)
- Vendor Credit = The vendor's share credited back to them at settlement (apportionment)
- The difference (Vendor Instalment − Vendor Credit) = Purchaser's share of the rates period

Example: If settlement shows Vendor paid $1,592.58, Vendor credit $1,522.96
→ Purchaser's settlement share = $1,592.58 − $1,522.96 = $69.62
→ Total Rates = Bank payments + $69.62

**Extract from Settlement Statement:**
- Rates apportionment (purchaser's share)
- Vendor credit (if any - subtract this)
- Body corporate pro-rata (add to BC total)
- Resident society pro-rata (add to RS total)
- Legal fees (deductible if investment property - see rule below)
- Interest on deposit → NET against Interest Expense (not separate income)

### LEGAL FEES RULE (CRITICAL)
**Legal fees for property purchase ARE DEDUCTIBLE if:**
1. Total legal fees for the year are $10,000 or less
2. Property was purchased as an investment (rental)

This includes: conveyancing, settlement, due diligence, title searches
Solicitors: Lane Neave, Pidgeon Judd, etc.

**DO NOT mark as "capital" or exclude** - legal fees under $10k = FULLY DEDUCTIBLE
Put in Legal Fees (Row 27)

### BODY CORPORATE RULES
**CRITICAL: Check invoice for operating vs reserve split**
- Operating Fund → DEDUCTIBLE (P&L Row 15)
- Reserve/Sinking Fund → EXCLUDE (capital contribution, not deductible)
- If invoice shows both: SPLIT the transaction

### RESIDENT SOCIETY vs BODY CORPORATE
These are SEPARATE categories:
- Body Corporate → Row 15
- Resident Society (RSI, Laneway, Community levy) → Row 36
Do NOT combine them.

### ADVERTISING vs AGENT FEES
These are SEPARATE categories:
- Advertising (Trade Me, tenant-find ads) → Row 12
- Agent Fees (PM management + letting fees) → Row 13
Do NOT combine them.

### PM FEES GST TREATMENT (IMPORTANT)
Property Management fees should be GST INCLUSIVE (fees + GST):
- If PM statement shows: Management Fee $3,049.30 + GST $457.40
- Total Agent Fees = $3,049.30 + $457.40 = $3,506.70 (GST inclusive)
- Do NOT just use the base fee amount - always ADD the GST component

### DUE DILIGENCE CONSOLIDATION (Row 18)
Professional assessment and valuation costs go to Due Diligence:
- LIM (Land Information Memorandum)
- Meth test / Methamphetamine testing
- Healthy homes ASSESSMENT/INSPECTION fees (NOT installation work)
- Smoke alarm certificates
- **Market valuations (Valocity, CoreLogic, QV)** - bank/lending requirement = DEDUCTIBLE
- Valuit / FordBaker / Depreciation schedule valuations
- Building inspections
- Pre-purchase reports

**IMPORTANT: Market Valuations Are Deductible**
Valuations from Valocity, CoreLogic, QV etc. are typically required by banks for lending.
These are DEDUCTIBLE due diligence costs, NOT capital expenses.
Do NOT exclude them just because they occur before settlement.
Example: Valocity Full Market Valuation $1,234.20 → Due Diligence (Row 18)

### HEALTHY HOMES COMPLIANCE COSTS (IRD QB 20/01) - CRITICAL
Per IRD guidance, Healthy Homes costs fall into THREE categories:

**1. DEDUCTIBLE (Revenue - immediate deduction):**
- Healthy homes ASSESSMENT/INSPECTION fees → Due Diligence (Row 18)
- REPAIRS to existing heating/ventilation systems
- TOPPING UP existing insulation (restoring to original condition)
- Compliance record-keeping and property management fees

**2. CAPITAL - NOT DEDUCTIBLE (part of building, 0% depreciation):**
- Installing NEW insulation where none existed
- Installing NEW fixed heating (heat pumps, wired panel heaters)
- Installing NEW extractor fans or ventilation systems
- Any NEW installation that becomes integral to the building

**3. CAPITAL - DEPRECIABLE (separate asset):**
- Portable plug-in heaters (not fixed to building)
- Standalone dehumidifiers
- Items removable without damaging building

**COMMON ERROR TO AVOID:**
Do NOT treat new heater/fan installations as "Repairs & Maintenance"!
New installations = CAPITAL (either exclude or depreciate)
Only REPAIRS to existing items are immediately deductible

**Example:**
- ✓ Healthy Homes Assessment fee $279 → Due Diligence (deductible)
- ✗ New panel heater installation $500 → CAPITAL (NOT repairs)
- ✓ Repairing existing heat pump $200 → Repairs (deductible)

### DEPRECIATION (Year 1)
If partial year, PRO-RATE depreciation:
```
Deductible = Full Year Depreciation × (Months Rented / 12)
```

### STANDARD AMOUNTS
- Accounting fees: $862.50 (always include in Row 16, source code "AF")

### GST TREATMENT
- Non-GST registered (most landlords): Use GST-INCLUSIVE amounts
- GST registered: Use GST-EXCLUSIVE amounts
Do NOT divide by 1.15 for non-registered taxpayers.

### P&L ROW MAPPING (Lighthouse Financial Template)
| Row | Category | Notes |
|-----|----------|-------|
| 6 | Rental Income | Primary income |
| 7 | Water Recovered | Tenant water recharges |
| 8 | Bank Contribution | Taxable - lender cashback/incentives |
| 12 | Advertising | Tenant-finding ads only |
| 13 | Agent Fees | PM fees + letting fees GST-INCLUSIVE (NOT advertising) |
| 14 | Bank Fees | Account fees, restructure fees |
| 15 | Body Corporate | OPERATING fund only |
| 16 | Consulting & Accounting | Standard $862.50 |
| 17 | Depreciation | Pro-rate if partial year |
| 18 | Due Diligence | ALL compliance costs |
| 24 | Insurance | LANDLORD insurance only |
| 25 | Interest Expense | After deductibility % applied |
| 27 | Legal Fees | Deductible if investment property |
| 34 | Rates | Council rates (Year 1: Bank paid + Vendor Instalment − Vendor Credit) |
| 35 | Repairs & Maintenance | Revenue repairs only |
| 36 | Resident Society | SEPARATE from Body Corporate |
| 41 | Water Rates | GST-inclusive for non-registered |

### COMMON ERRORS TO AVOID
1. ❌ Using only bank instalments for Year 1 rates → ✓ Add (Vendor Instalment − Vendor Credit) from settlement statement
2. ❌ Using loan statements for interest totals → ✓ Use bank statement as primary
3. ❌ Subtracting interest adjustments/credits → ✓ Sum gross DEBIT charges only
4. ❌ Including BC reserve fund → ✓ Operating fund only
5. ❌ Full year depreciation for partial year → ✓ Pro-rate by months
6. ❌ Forgetting accounting fees → ✓ Always include $862.50
7. ❌ Combining advertising with agent fees → ✓ Keep separate
8. ❌ Combining resident society with body corporate → ✓ Keep separate
9. ❌ Treating interest on deposit as income → ✓ Net against interest expense
10. ❌ Flagging low offset interest as error → ✓ Verify offset account first
11. ❌ Missing invoices in "other" documents → ✓ Check ALL documents for deductibles
12. ❌ Ignoring Valocity/valuation invoices → ✓ Add to Due Diligence (Row 18)
13. ❌ Skipping unknown document types → ✓ Review every document thoroughly
14. ❌ Treating market valuations as capital → ✓ Bank-required valuations are DEDUCTIBLE due diligence
15. ❌ Missing bank "Cash Contribution" as income → ✓ Bank cashback/incentives are TAXABLE INCOME (Row 8)
16. ❌ PM fees without GST → ✓ Always use GST-INCLUSIVE total (fees + GST)
17. ❌ Including settlement cashback without bank/loan verification → ✓ Verify cashback in bank statement before including
18. ❌ Inconsistent flat rate credit treatment across months → ✓ Apply same treatment to ALL months
19. ❌ Treating NEW heater/fan installation as Repairs → ✓ NEW installations = CAPITAL (per IRD QB 20/01)
20. ❌ Claiming all Healthy Homes costs as deductible → ✓ Only assessments and repairs to EXISTING items are deductible

### BACHCARE / HOLIDAY RENTAL FLAT RATE CREDITS (IMPORTANT)
When processing Bachcare or other short-term/holiday rental PM statements:
- Flat rate credits MUST be treated consistently across ALL months
- Positive flat rate credits = additional rental income (add to gross rent)
- If a flat rate credit appears in ANY month, check ALL months for similar credits
- These are platform adjustments/credits representing actual income to the owner
Example: March shows $92.05 flat rate credit → Add to rental income
Ensure the same rule applies to December, January, February, etc.

## Extracted Data from Documents
{extracted_data}

## Source Reference Codes
Use these codes when referencing sources:
- BS = Bank Statement
- SS = Settlement Statement
- PM = Property Manager Statement
- LS = Loan Statement
- INV = Invoice
- DEP = Depreciation Schedule
- CP = Client Provided
- AF = Accounting Fees (standard)

## Required Output Format
Return your analysis as a JSON object with this structure.
CRITICAL: Every line item MUST include "calculation_logic" explaining HOW the amount was derived.

```json
{{
  "summary": {{
    "total_income": 0.00,
    "total_expenses": 0.00,
    "total_deductions": 0.00,
    "interest_gross": 0.00,
    "interest_deductible_percentage": {interest_percentage},
    "interest_deductible_amount": 0.00,
    "net_rental_income": 0.00
  }},
  "income": {{
    "rental_income": {{
      "pl_row": 6,
      "amount": 0.00,
      "source_code": "PM",
      "source": "PM Statement (Company Name)",
      "verification": "verified",
      "notes": "explanation",
      "calculation_logic": {{
        "primary_source_code": "PM",
        "primary_source_name": "Property Manager Statement",
        "calculation_method": "Sum of monthly rent deposits",
        "calculation_steps": [
          "Apr: $4,583.33 (PM line 1)",
          "May: $4,583.33 (PM line 2)",
          "Total: $55,000.00"
        ],
        "cross_validated_with": ["Bank deposits from PM"],
        "validation_status": "matched"
      }},
      "transactions": [
        {{"date": "YYYY-MM-DD", "description": "...", "amount": 0.00, "source_code": "PM"}}
      ]
    }},
    "water_rates_recovered": null,
    "bank_contribution": null,
    "other_income": null
  }},
  "expenses": {{
    "interest": {{
      "pl_row": 26,
      "gross_amount": 0.00,
      "deductible_percentage": {interest_percentage},
      "deductible_amount": 0.00,
      "source_code": "LS",
      "source": "Loan Statement",
      "verification": "verified",
      "notes": "explanation",
      "calculation_logic": {{
        "primary_source_code": "LS",
        "primary_source_name": "Loan Statement",
        "calculation_method": "Sum of LOAN INTEREST debits (excluding adjustments/credits/offsets)",
        "calculation_steps": [
          "Oct: $1,041.67 (LOAN INTEREST)",
          "Nov: $1,041.67 (LOAN INTEREST)",
          "Gross: $12,500.00",
          "× {interest_percentage}% = Deductible amount"
        ],
        "cross_validated_with": ["Bank loan payments"],
        "validation_status": "matched"
      }},
      "monthly_breakdown": {{"Apr": 0.00, "May": 0.00}}
    }},
    "rates": {{
      "pl_row": 34,
      "amount": 0.00,
      "source_code": "BS",
      "source": "Bank Statement + Rates Notice",
      "verification": "verified",
      "notes": "explanation",
      "calculation_logic": {{
        "primary_source_code": "BS",
        "primary_source_name": "Bank Statement",
        "calculation_method": "Sum of payments to Council",
        "calculation_steps": ["Q1: $800 (15/05)", "Q2: $800 (15/08)", "Total: $3,200"]
      }}
    }},
    "insurance": null,
    "water_rates": null,
    "body_corporate": null,
    "resident_society": null,
    "agent_fees": null,
    "advertising": null,
    "bank_fees": null,
    "legal_fees": null,
    "depreciation": {{
      "pl_row": 17,
      "amount": 0.00,
      "source_code": "DEP",
      "source": "Depreciation Schedule",
      "notes": "Pro-rated if partial year",
      "calculation_logic": {{
        "primary_source_code": "DEP",
        "primary_source_name": "Depreciation Schedule",
        "calculation_method": "From depreciation schedule, pro-rated if partial year"
      }}
    }},
    "accounting_fees": {{
      "pl_row": 16,
      "amount": 862.50,
      "source_code": "AF",
      "source": "Standard Fee",
      "notes": "Standard accounting fee",
      "calculation_logic": {{
        "primary_source_code": "AF",
        "primary_source_name": "Standard Accounting Fee",
        "calculation_method": "Standard fee of $862.50 (always included)"
      }}
    }},
    "due_diligence": null,
    "repairs_maintenance": {{
      "pl_row": 35,
      "total_amount": 0.00,
      "source_code": "BS",
      "source": "Bank Statement + Invoices",
      "calculation_logic": {{
        "calculation_method": "Sum of repair payments",
        "calculation_steps": ["Plumber: $350 (no invoice needed < $800)", "Painter: $1,200 (invoice ✓)"]
      }},
      "items": [
        {{
          "date": "YYYY-MM-DD",
          "description": "...",
          "amount": 0.00,
          "payee": "...",
          "invoice_status": "verified / missing_required / not_required",
          "source_code": "BS"
        }}
      ]
    }},
    "other_expenses": null,
    "home_office": {{
      "pl_row": 37,
      "amount": 0.00,
      "source_code": "CP",
      "source": "Personal Expenditure Claims",
      "notes": "Home office deduction (business use % of home expenses)",
      "calculation_logic": {{
        "primary_source_code": "CP",
        "primary_source_name": "Personal Expenditure Claims",
        "calculation_method": "Business use % × Total home expenses"
      }}
    }},
    "mobile_phone": {{
      "pl_row": 37,
      "amount": 0.00,
      "source_code": "CP",
      "source": "Personal Expenditure Claims",
      "notes": "50% of mobile phone expenses",
      "calculation_logic": {{
        "primary_source_code": "CP",
        "primary_source_name": "Personal Expenditure Claims",
        "calculation_method": "50% of annual mobile expense"
      }}
    }},
    "mileage": {{
      "pl_row": 37,
      "amount": 0.00,
      "source_code": "CP",
      "source": "Personal Expenditure Claims",
      "notes": "Business km × IRD rate ($0.99/km)",
      "calculation_logic": {{
        "primary_source_code": "CP",
        "primary_source_name": "Personal Expenditure Claims",
        "calculation_method": "Business kilometres × $0.99 IRD rate"
      }}
    }}
  }},
  "excluded": {{
    "principal_repayment": {{
      "amount": 0.00,
      "source_code": "BS",
      "notes": "Not deductible",
      "calculation_logic": {{
        "calculation_method": "Total loan payments minus interest",
        "calculation_steps": ["Loan payments: $24,000", "Less interest: $12,500", "Principal: $11,500"]
      }}
    }},
    "bond": {{"amount": 0.00, "notes": "Not income - refundable deposit"}},
    "capital_expenses": null
  }},
  "flags": [
    {{
      "severity": "high / medium / low",
      "category": "missing_document / mismatch / review_required / invoice_required",
      "message": "Description of issue",
      "action_required": "What needs to be done"
    }}
  ],
  "document_requests": [
    {{
      "document_type": "loan_statement",
      "reason": "Bank shows loan payments but no loan statement provided",
      "priority": "required / recommended"
    }}
  ],
  "client_questions": [
    {{
      "question": "Is the $1,850 insurance payment landlord insurance?",
      "context": "Payment to Tower Insurance on 15/06/2024",
      "options": ["Yes - landlord insurance", "No - personal insurance"],
      "related_amount": 1850.00
    }}
  ],
  "documents_status": {{
    "pm_statement": {{"status": "received / missing", "notes": "..."}},
    "bank_statement": {{"status": "received / partial / missing", "notes": "..."}},
    "loan_statement": {{"status": "received / missing", "notes": "..."}},
    "rates_invoice": {{"status": "received / missing", "notes": "..."}},
    "insurance_policy": {{"status": "received / missing / wrong_type", "notes": "..."}}
  }},
  "processing_notes": [
    "Step 1: Found PM statement from Quinovic...",
    "Step 2: Bank statement shows 12 deposits matching PM...",
    "Step 6 COMPLETENESS: Processed X of Y documents. Other/invoice docs: [list what was found]"
  ],
  "documents_processed": [
    {{"filename": "doc1.pdf", "type": "bank_statement", "extracted": "12 transactions, interest $X"}},
    {{"filename": "valocity_invoice.pdf", "type": "other", "extracted": "Valuation fee $345 → Due Diligence"}}
  ]
}}
```

IMPORTANT RULES:
1. Use NEGATIVE amounts for expenses, POSITIVE for income
2. Only interest is deductible, NOT principal repayments
3. Bond payments are NOT income - exclude them
4. Repairs over $800 require invoice - flag if missing
5. Be thorough - every number must have a source
6. If data is missing or unclear, FLAG it - don't guess
7. Cross-reference ALL transactions between documents
8. Sum ALL rental income from bank deposits or PM statement
9. Verify totals by adding up individual transactions
10. **Process EVERY document** - especially "other" or "unknown" types which often contain invoices

## VERIFICATION CHECKLIST (Complete before responding)
Before finalizing your response, verify:
- [ ] **DOCUMENT COUNT**: You have processed ALL documents listed above
- [ ] **OTHER/UNKNOWN DOCS**: Checked all "other" type documents for invoices/expenses
- [ ] **DUE DILIGENCE**: Included Valocity, meth tests, LIM, healthy homes if present
- [ ] Total rental income matches sum of all rent transactions
- [ ] Interest amount comes from loan statement, not bank payment total
- [ ] All bank statement debits for property are captured
- [ ] Rates amount matches rates notices/invoices if provided
- [ ] Insurance is LANDLORD insurance, not personal home insurance
- [ ] No personal expenses included (shopping, dining, etc.)
- [ ] Principal repayments are EXCLUDED from deductions
- [ ] Settlement statement items (if first year) are properly categorized

Return ONLY the JSON object, no other text.
"""
        return prompt

    def _validate_extracted_data(self, extracted_data: Dict[str, Any], doc_type: str) -> Dict[str, Any]:
        """
        Validate and correct extracted data before using in workings.

        This catches cases where Claude's extraction has internal inconsistencies,
        such as interest_analysis.total_interest_debits not matching the sum of monthly_breakdown.
        """
        if not extracted_data:
            return extracted_data

        # Check tool_use_extraction for bank statements
        if doc_type == "bank_statement":
            tool_use = extracted_data.get("tool_use_extraction", {})
            if tool_use:
                interest_analysis = tool_use.get("interest_analysis", {})
                if interest_analysis:
                    self._validate_and_correct_interest_analysis(interest_analysis, tool_use)
                    # Also update top-level if exists
                    if "interest_analysis" in extracted_data:
                        extracted_data["interest_analysis"] = interest_analysis.copy()

            # Also check top-level interest_analysis
            top_level_interest = extracted_data.get("interest_analysis", {})
            if top_level_interest and not tool_use:
                transactions = extracted_data.get("transactions", [])
                self._validate_and_correct_interest_analysis(top_level_interest, {"transactions": transactions})

        return extracted_data

    def _validate_and_correct_interest_analysis(
        self,
        interest_analysis: Dict[str, Any],
        extracted_data: Dict[str, Any]
    ) -> None:
        """
        Validate that interest_analysis.total_interest_debits matches the sum of monthly_breakdown.

        If there's a discrepancy, correct total_interest_debits to match the actual sum.
        """
        if not interest_analysis:
            return

        monthly_breakdown = interest_analysis.get("monthly_breakdown", {})
        stated_total = float(interest_analysis.get("total_interest_debits", 0))

        if not monthly_breakdown:
            # No monthly breakdown to validate against
            return

        # Calculate actual sum from monthly breakdown
        actual_sum = sum(float(v) for v in monthly_breakdown.values())

        # Also verify against transaction sum if available
        transactions = extracted_data.get("transactions", [])
        txn_interest_sum = 0.0
        if transactions:
            for txn in transactions:
                desc = str(txn.get("description", "")).upper()
                txn_type = txn.get("transaction_type", "")
                if "LOAN" in desc and "INT" in desc and txn_type == "debit":
                    txn_interest_sum += abs(float(txn.get("amount", 0)))

        # Determine the correct value
        # Priority: transaction sum > monthly breakdown sum > stated total
        tolerance = 0.02
        correct_value = stated_total

        if txn_interest_sum > 0:
            if abs(txn_interest_sum - stated_total) > tolerance:
                logger.warning(
                    f"Interest analysis correction: stated={stated_total:.2f}, "
                    f"transaction_sum={txn_interest_sum:.2f}, monthly_sum={actual_sum:.2f}. "
                    f"Using transaction sum."
                )
                correct_value = txn_interest_sum
                interest_analysis["_correction_applied"] = True
                interest_analysis["_original_stated_total"] = stated_total
        elif abs(actual_sum - stated_total) > tolerance:
            logger.warning(
                f"Interest analysis correction: stated={stated_total:.2f}, "
                f"monthly_sum={actual_sum:.2f}. Using monthly sum."
            )
            correct_value = actual_sum
            interest_analysis["_correction_applied"] = True
            interest_analysis["_original_stated_total"] = stated_total

        interest_analysis["total_interest_debits"] = correct_value

    def _detect_potential_bank_contributions(
        self,
        documents_by_type: Dict[str, List[Dict[str, Any]]]
    ) -> List[Dict[str, Any]]:
        """
        Detect potential bank contributions that may have been missed.

        Looks for large unexplained credits on/near settlement date in bank statements.
        Returns list of potential bank contributions to flag for accountant review.
        """
        from datetime import datetime, timedelta

        potential_contributions = []

        # Step 1: Get settlement date from settlement statement
        settlement_date = None
        settlement_docs = documents_by_type.get("settlement_statement", [])
        for doc in settlement_docs:
            extracted = doc.get("extracted_data", {})
            tool_use = extracted.get("tool_use_extraction", {})

            # Try to find settlement date from various fields
            for field in ["settlement_date", "date"]:
                if tool_use.get(field):
                    try:
                        settlement_date = datetime.strptime(str(tool_use[field]), "%Y-%m-%d").date()
                        break
                    except ValueError:
                        pass

            # Also try to extract from line item descriptions (e.g., "23/08/24")
            if not settlement_date:
                line_items = tool_use.get("all_line_items", [])
                for item in line_items:
                    desc = item.get("description", "")
                    # Look for date patterns like "23/08/24" or "23/08/2024"
                    import re
                    date_match = re.search(r'(\d{1,2})/(\d{1,2})/(\d{2,4})', desc)
                    if date_match:
                        try:
                            day, month, year = date_match.groups()
                            if len(year) == 2:
                                year = "20" + year
                            settlement_date = datetime(int(year), int(month), int(day)).date()
                            break
                        except ValueError:
                            pass
                if settlement_date:
                    break

        if not settlement_date:
            logger.debug("No settlement date found - skipping bank contribution detection")
            return potential_contributions

        logger.info(f"Settlement date detected: {settlement_date}")

        # Step 2: Look through bank statement transactions for large unexplained credits
        bank_docs = documents_by_type.get("bank_statement", [])
        for doc in bank_docs:
            extracted = doc.get("extracted_data", {})
            tool_use = extracted.get("tool_use_extraction", {})
            transactions = tool_use.get("transactions", [])

            for txn in transactions:
                # Only look at credits
                if txn.get("transaction_type") != "credit":
                    continue

                amount = float(txn.get("amount", 0) or 0)

                # Only consider large credits ($1,500 - $15,000 range typical for bank contributions)
                if amount < 1500 or amount > 15000:
                    continue

                # Check if already categorized as bank_contribution
                categorization = txn.get("categorization", {})
                if categorization.get("suggested_category") == "bank_contribution":
                    continue

                # Skip if already clearly categorized as something else
                category = categorization.get("suggested_category", "unknown")
                if category in ["rental_income", "insurance_payout", "bond_received", "transfer_between_accounts"]:
                    # Check confidence - if high confidence, skip
                    if categorization.get("confidence", 0) > 0.8:
                        continue

                # Parse transaction date
                txn_date_str = txn.get("date", "")
                txn_date = None
                for fmt in ["%Y-%m-%d", "%d/%m/%Y", "%d/%m/%y"]:
                    try:
                        txn_date = datetime.strptime(txn_date_str, fmt).date()
                        break
                    except ValueError:
                        pass

                if not txn_date:
                    continue

                # Check if within 7 days of settlement date
                days_diff = abs((txn_date - settlement_date).days)
                if days_diff <= 7:
                    description = txn.get("description", "") or ""

                    # Check for bank contribution patterns in description
                    desc_upper = description.upper()
                    is_likely_bank_contrib = any(pattern in desc_upper for pattern in [
                        "BANK INIT", "BANK INITIATED", "CASH CONTRIB", "CASHBACK",
                        "BANK CONTRIBUTION", "SETTLEMENT CASHBACK", "BANK CASH"
                    ])

                    # Also flag if description is empty/minimal and amount is in typical range
                    is_unexplained = len(description.strip()) < 5 and 2000 <= amount <= 10000

                    if is_likely_bank_contrib or is_unexplained:
                        potential_contributions.append({
                            "date": txn_date_str,
                            "amount": amount,
                            "description": description or "(no description)",
                            "reason": "Likely bank contribution" if is_likely_bank_contrib else "Large unexplained credit near settlement date",
                            "document": doc.get("filename", ""),
                            "current_category": category,
                            "settlement_date": str(settlement_date),
                            "days_from_settlement": days_diff
                        })

                        # Flag the transaction in the extracted data
                        if "review_flags" not in txn:
                            txn["review_flags"] = {}
                        txn["review_flags"]["needs_review"] = True
                        txn["review_flags"]["severity"] = "warning"
                        if "reasons" not in txn["review_flags"]:
                            txn["review_flags"]["reasons"] = []
                        txn["review_flags"]["reasons"].append(
                            f"Potential bank contribution (${amount:.2f}) - verify with accountant"
                        )

                        # Update categorization suggestion
                        txn["categorization"]["suggested_category"] = "bank_contribution"
                        txn["categorization"]["confidence"] = 0.6
                        txn["categorization"]["is_deductible"] = False
                        txn["categorization"]["_flagged_for_review"] = True

                        logger.warning(
                            f"Potential bank contribution detected: ${amount:.2f} on {txn_date_str} "
                            f"({days_diff} days from settlement) - flagged for accountant review"
                        )

        return potential_contributions

    def _format_documents_summary(self, documents_by_type: Dict[str, List]) -> str:
        """Format documents summary for the prompt."""
        lines = []
        total_docs = 0
        other_docs = []

        for doc_type, docs in documents_by_type.items():
            count = len(docs)
            total_docs += count
            filenames = ", ".join(d["filename"] for d in docs[:3])
            if count > 3:
                filenames += f" (+{count - 3} more)"
            lines.append(f"- {doc_type}: {count} document(s) - {filenames}")

            # Track "other" type documents for special attention
            if doc_type in ("other", "unknown", "invoice"):
                other_docs.extend(docs)

        if not lines:
            return "No documents provided."

        # Add total count and highlight other docs
        summary = f"**TOTAL: {total_docs} documents to process**\n\n"
        summary += "\n".join(lines)

        if other_docs:
            summary += f"\n\n⚠️ **ATTENTION: {len(other_docs)} 'other/unknown/invoice' documents require careful review:**\n"
            for doc in other_docs:
                summary += f"   - {doc['filename']} (may contain deductible expenses like Valocity, meth test, etc.)\n"

        return summary

    def _format_extracted_data(self, documents_by_type: Dict[str, List]) -> str:
        """Format extracted data for the prompt."""
        sections = []

        # Process in accountant workflow order
        order = [
            "property_manager_statement",
            "bank_statement",
            "loan_statement",
            "rates",
            "insurance",
            "landlord_insurance",
            "settlement_statement",
            "personal_expenditure_claims",  # Home office, mobile, mileage claims
        ]

        # First process ordered types
        for doc_type in order:
            if doc_type in documents_by_type:
                for doc in documents_by_type[doc_type]:
                    sections.append(self._format_single_document(doc))

        # Then process remaining types
        for doc_type, docs in documents_by_type.items():
            if doc_type not in order:
                for doc in docs:
                    sections.append(self._format_single_document(doc))

        return "\n\n".join(sections) if sections else "No extracted data available."

    def _format_single_document(self, doc: Dict[str, Any]) -> str:
        """Format a single document's extracted data with full context."""
        extracted = doc.get("extracted_data", {})
        filename = doc.get('filename', '')
        file_path = doc.get('file_path', '')
        doc_type = doc.get('document_type', '')

        # CRITICAL: For CSV files, read raw content directly
        # CSV files contain individual transactions that may not be properly in extracted_data
        if filename.lower().endswith('.csv') and file_path:
            if doc_type == 'loan_statement':
                csv_content = self._read_csv_for_loan_interest(file_path, filename)
                if csv_content:
                    return csv_content
            elif doc_type == 'bank_statement':
                csv_content = self._read_csv_for_bank_transactions(file_path, filename)
                if csv_content:
                    return csv_content

        if not extracted:
            return f"### {doc_type.upper()}: {filename}\nNo data extracted."

        lines = [f"### {doc_type.upper()}: {filename}"]
        lines.append(f"Confidence: {doc.get('confidence', 'N/A')}")

        # Key details - show ALL fields with special handling for amounts and nested items
        key_details = extracted.get("key_details", {})
        if key_details and isinstance(key_details, dict):
            # First, highlight any amounts prominently
            amount_keys = ['total_amount', 'total', 'amount_due', 'subtotal', 'gst', 'invoice_total', 'purchase_price']
            amounts_found = {k: key_details.get(k) for k in amount_keys if key_details.get(k)}
            if amounts_found:
                lines.append("\n**💰 AMOUNTS:**")
                for key, value in amounts_found.items():
                    lines.append(f"  - **{key}**: {value}")

            lines.append("\n**Key Details:**")
            for key, value in key_details.items():
                if value is not None and key not in amount_keys:
                    # Handle nested line_items
                    if key == 'line_items' and isinstance(value, list):
                        lines.append(f"  - {key}: ({len(value)} items)")
                        for item in value:
                            if isinstance(item, dict):
                                desc = item.get('description', 'N/A')
                                amt = item.get('amount', 0)
                                lines.append(f"      • {desc}: {amt}")
                    # Handle nested transactions (don't show here, will be shown in transactions section)
                    elif key == 'transactions':
                        lines.append(f"  - {key}: ({len(value)} transactions - see below)")
                    # Handle other nested dicts
                    elif isinstance(value, dict):
                        lines.append(f"  - {key}:")
                        for k, v in value.items():
                            lines.append(f"      • {k}: {v}")
                    # Handle lists of adjustments
                    elif isinstance(value, list) and key in ['other_adjustments', 'adjustments']:
                        lines.append(f"  - {key}:")
                        for item in value:
                            if isinstance(item, dict):
                                desc = item.get('description', 'N/A')
                                amt = item.get('amount', 0)
                                lines.append(f"      • {desc}: {amt}")
                    else:
                        lines.append(f"  - {key}: {value}")

        # Financial summary if available
        financial = extracted.get("financial_summary", {})
        if financial:
            lines.append("\n**Financial Summary:**")
            for key, value in financial.items():
                if value is not None:
                    lines.append(f"  - {key}: ${value}" if isinstance(value, (int, float)) else f"  - {key}: {value}")

        # Line items (settlement statements, invoices)
        # Check both top-level and key_details.line_items
        line_items = extracted.get("line_items", [])
        if not line_items:
            kd = extracted.get("key_details", {})
            if isinstance(kd, dict):
                line_items = kd.get("line_items", [])
        if line_items:
            lines.append(f"\n**Line Items ({len(line_items)} items):**")
            for item in line_items:
                desc = item.get("description", "N/A")
                amount = item.get("amount", 0)
                category = item.get("category", "")
                is_deductible = item.get("is_deductible", True)
                deductible_marker = "✓" if is_deductible else "✗"
                line = f"  {deductible_marker} {desc}: ${amount}"
                if category:
                    line += f" [{category}]"
                lines.append(line)

        # Transactions - SHOW ALL for accurate analysis
        # Check both top-level and key_details.transactions (Phase 1 stores them in key_details)
        transactions = extracted.get("transactions", [])
        if not transactions:
            key_details = extracted.get("key_details", {})
            if isinstance(key_details, dict):
                transactions = key_details.get("transactions", [])
        if transactions:
            lines.append(f"\n**Transactions ({len(transactions)} items):**")
            # Show ALL transactions for accurate totals and categorization
            for txn in transactions:
                date_str = txn.get("date", "N/A")
                desc = txn.get("description", "N/A")[:60]  # Slightly longer description
                amount = txn.get("amount", 0)
                category = txn.get("category", "")
                flag = txn.get("flag", "")

                # Format with category/flag if available
                line = f"  {date_str} | {desc} | ${amount}"
                if category:
                    line += f" [{category}]"
                if flag:
                    line += f" ⚠️{flag}"
                lines.append(line)

        # Notes/warnings from Phase 1
        notes = extracted.get("notes", [])
        if notes:
            lines.append("\n**Notes:**")
            for note in notes:
                lines.append(f"  ⚠️ {note}")

        # Flags from Phase 1
        flags = extracted.get("flags", [])
        if flags:
            lines.append("\n**Flags:**")
            for flag in flags:
                lines.append(f"  🚩 {flag}")

        # Special handling for PM statements - show full breakdown with GST
        if doc_type == "property_manager_statement":
            tool_use = extracted.get("tool_use_extraction", {})
            if tool_use:
                # INCOME SECTION
                income = tool_use.get("income", {})
                if income:
                    lines.append("\n**📥 PM INCOME BREAKDOWN:**")
                    if income.get("gross_rent"):
                        lines.append(f"  - Gross Rent: ${income['gross_rent']:.2f}")
                    if income.get("water_recovered"):
                        lines.append(f"  - Water Recovered: ${income['water_recovered']:.2f}")
                    if income.get("insurance_payout") and isinstance(income["insurance_payout"], dict):
                        payout = income["insurance_payout"]
                        if payout.get("amount"):
                            lines.append(f"  - Insurance Payout: ${payout['amount']:.2f} ({payout.get('description', '')})")
                    if income.get("tenant_contribution") and isinstance(income["tenant_contribution"], dict):
                        contrib = income["tenant_contribution"]
                        if contrib.get("amount"):
                            lines.append(f"  - Tenant Contribution: ${contrib['amount']:.2f} ({contrib.get('description', '')})")
                    if income.get("interest_earned"):
                        lines.append(f"  - Interest Earned: ${income['interest_earned']:.2f}")
                    if income.get("bond_received"):
                        lines.append(f"  - Bond Received: ${income['bond_received']:.2f} (NOT income)")
                    for other in income.get("other_income", []):
                        if isinstance(other, dict):
                            lines.append(f"  - {other.get('description', 'Other')}: ${other.get('amount', 0):.2f}")
                    if income.get("total_income"):
                        lines.append(f"  **TOTAL INCOME: ${income['total_income']:.2f}**")

                # EXPENSES SECTION
                expenses = tool_use.get("expenses", {})
                if expenses:
                    lines.append("\n**📤 PM EXPENSES BREAKDOWN (CHECK GST!):**")

                    # Helper function to format fee with GST
                    def format_fee_with_gst(name: str, fee_data, default_gst_inclusive: bool = True):
                        if not fee_data:
                            return None
                        if isinstance(fee_data, (int, float)):
                            return f"  - {name}: ${fee_data:.2f}"
                        if isinstance(fee_data, dict):
                            base = fee_data.get("amount", 0) or 0
                            gst = fee_data.get("gst_amount", 0) or 0
                            gst_incl = fee_data.get("gst_inclusive", default_gst_inclusive)
                            if gst > 0:
                                total = base + gst
                                return f"  - {name}: ${base:.2f} + GST ${gst:.2f} = **${total:.2f}**"
                            elif not gst_incl and base > 0:
                                gst_calc = base * 0.15
                                total = base + gst_calc
                                return f"  - {name}: ${base:.2f} + GST ${gst_calc:.2f} = **${total:.2f}** (GST calculated)"
                            elif base > 0:
                                return f"  - {name}: ${base:.2f} (GST-inclusive: {gst_incl})"
                        return None

                    # Management fee
                    mgmt_line = format_fee_with_gst("Management Fee", expenses.get("management_fee"), False)
                    if mgmt_line:
                        lines.append(mgmt_line)

                    # Letting fee
                    letting_line = format_fee_with_gst("Letting Fee", expenses.get("letting_fee"))
                    if letting_line:
                        lines.append(letting_line)

                    # Inspection fee
                    insp_line = format_fee_with_gst("Inspection Fee", expenses.get("inspection_fee"))
                    if insp_line:
                        lines.append(insp_line)

                    # Advertising
                    ad_line = format_fee_with_gst("Advertising", expenses.get("advertising"))
                    if ad_line:
                        lines.append(ad_line)

                    # Repairs
                    for repair in expenses.get("repairs", []):
                        if isinstance(repair, dict) and repair.get("amount"):
                            desc = repair.get("description", "Repair")
                            amt = repair.get("amount", 0)
                            gst = repair.get("gst_amount", 0) or 0
                            vendor = repair.get("vendor", "")
                            if gst > 0:
                                lines.append(f"  - Repair: {desc} - ${amt:.2f} + GST ${gst:.2f} = ${amt + gst:.2f} ({vendor})")
                            else:
                                lines.append(f"  - Repair: {desc} - ${amt:.2f} ({vendor})")

                    # Insurance paid
                    if expenses.get("insurance_paid") and isinstance(expenses["insurance_paid"], dict):
                        ins = expenses["insurance_paid"]
                        if ins.get("amount"):
                            lines.append(f"  - Insurance Paid: ${ins['amount']:.2f} ({ins.get('description', '')})")

                    # Rates paid
                    if expenses.get("rates_paid") and isinstance(expenses["rates_paid"], dict):
                        rates = expenses["rates_paid"]
                        if rates.get("amount"):
                            lines.append(f"  - Rates Paid: ${rates['amount']:.2f} ({rates.get('period', '')})")

                    # Water rates paid
                    if expenses.get("water_rates_paid") and isinstance(expenses["water_rates_paid"], dict):
                        water = expenses["water_rates_paid"]
                        if water.get("amount"):
                            lines.append(f"  - Water Rates Paid: ${water['amount']:.2f} ({water.get('period', '')})")

                    # Body corporate paid
                    if expenses.get("body_corporate_paid") and isinstance(expenses["body_corporate_paid"], dict):
                        bc = expenses["body_corporate_paid"]
                        if bc.get("amount"):
                            lines.append(f"  - Body Corporate Paid: ${bc['amount']:.2f} ({bc.get('period', '')})")

                    # Compliance costs
                    for comp in expenses.get("compliance_costs", []):
                        if isinstance(comp, dict) and comp.get("amount"):
                            desc = comp.get("description", "Compliance")
                            amt = comp.get("amount", 0)
                            is_cap = comp.get("is_capital", False)
                            cap_label = "CAPITAL" if is_cap else "Deductible"
                            lines.append(f"  - {desc}: ${amt:.2f} [{cap_label}]")

                    # Sundry expenses
                    for sundry in expenses.get("sundry_expenses", []):
                        if isinstance(sundry, dict) and sundry.get("amount"):
                            lines.append(f"  - {sundry.get('description', 'Sundry')}: ${sundry.get('amount', 0):.2f}")

                    # Other expenses
                    for other in expenses.get("other_expenses", []):
                        if isinstance(other, dict) and other.get("amount"):
                            lines.append(f"  - {other.get('description', 'Other')}: ${other.get('amount', 0):.2f} [{other.get('category', '')}]")

                    # Totals
                    if expenses.get("total_gst"):
                        lines.append(f"  - Total GST: ${expenses['total_gst']:.2f}")
                    if expenses.get("total_expenses"):
                        lines.append(f"  **TOTAL EXPENSES: ${expenses['total_expenses']:.2f}**")

                # DISBURSEMENTS
                disbursements = tool_use.get("disbursements", {})
                if disbursements and disbursements.get("total_disbursed"):
                    lines.append(f"\n**💸 TOTAL DISBURSED TO OWNER: ${disbursements['total_disbursed']:.2f}**")

                # SUMMARY
                summary = tool_use.get("summary", {})
                if summary:
                    lines.append("\n**📊 PM SUMMARY:**")
                    if summary.get("opening_balance") is not None:
                        lines.append(f"  - Opening Balance: ${summary['opening_balance']:.2f}")
                    if summary.get("closing_balance") is not None:
                        lines.append(f"  - Closing Balance: ${summary['closing_balance']:.2f}")

        return "\n".join(lines)

    def _read_csv_for_loan_interest(self, file_path: str, filename: str) -> Optional[str]:
        """
        Read CSV loan statement and extract interest transactions.

        This is CRITICAL for accurate interest calculations - CSV files contain
        individual weekly transactions that may be summarized/lost in PDF statements.
        """
        import csv
        import os

        try:
            if not os.path.exists(file_path):
                logger.warning(f"CSV file not found: {file_path}")
                return None

            lines = [f"### LOAN_STATEMENT (CSV - PRIMARY SOURCE): {filename}"]
            lines.append("⚠️ **IMPORTANT: Use this CSV data for interest calculations - it contains individual transactions**")
            lines.append("")

            interest_transactions = []
            total_interest = 0.0
            all_transactions = []

            with open(file_path, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    # Try to get the description/memo field
                    description = row.get('Memo/Description', row.get('Description', row.get('Memo', '')))
                    date = row.get('Date', row.get('date', 'N/A'))

                    # Try to get amount - could be in different columns
                    amount_str = row.get('Amount (debit)', row.get('Amount', row.get('Debit', '0')))
                    try:
                        amount = abs(float(amount_str.replace(',', '').replace('$', ''))) if amount_str else 0
                    except (ValueError, AttributeError):
                        amount = 0

                    if description:
                        all_transactions.append({
                            'date': date,
                            'description': description.strip(),
                            'amount': amount
                        })

                        # Identify interest transactions
                        if 'LOAN INTEREST' in description.upper() or 'INTEREST' in description.upper():
                            interest_transactions.append({
                                'date': date,
                                'description': description.strip(),
                                'amount': amount
                            })
                            total_interest += amount

            # Format output with emphasis on interest
            if interest_transactions:
                lines.append(f"**🔴 LOAN INTEREST TRANSACTIONS ({len(interest_transactions)} entries):**")
                lines.append(f"**TOTAL INTEREST FROM CSV: ${total_interest:.2f}**")
                lines.append("")
                for txn in interest_transactions:
                    lines.append(f"  {txn['date']} | LOAN INTEREST | ${txn['amount']:.2f}")
                lines.append("")
                lines.append(f"**>>> USE THIS TOTAL: ${total_interest:.2f} <<<**")
            else:
                lines.append("No interest transactions found in CSV")
                lines.append("\n**All transactions:**")
                for txn in all_transactions[:50]:  # Limit to 50 transactions
                    lines.append(f"  {txn['date']} | {txn['description'][:50]} | ${txn['amount']:.2f}")

            logger.info(f"CSV loan interest extracted: ${total_interest:.2f} from {len(interest_transactions)} transactions")
            return "\n".join(lines)

        except Exception as e:
            logger.error(f"Error reading CSV loan statement {file_path}: {e}")
            return None

    def _read_csv_for_bank_transactions(self, file_path: str, filename: str) -> Optional[str]:
        """
        Read CSV bank statement and extract all transactions.

        Bank statement CSVs contain the complete transaction history which is
        essential for accurate income/expense tracking.
        """
        import csv
        import os

        try:
            if not os.path.exists(file_path):
                logger.warning(f"CSV file not found: {file_path}")
                return None

            lines = [f"### BANK_STATEMENT (CSV - PRIMARY SOURCE): {filename}"]
            lines.append("⚠️ **IMPORTANT: Use this CSV data for transaction analysis - it contains all individual transactions**")
            lines.append("")

            transactions = []
            income_total = 0.0
            expense_total = 0.0

            with open(file_path, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    # Try to get the description/memo field
                    description = row.get('Memo/Description', row.get('Description', row.get('Memo', '')))
                    date = row.get('Date', row.get('date', 'N/A'))
                    other_party = row.get('OP name', row.get('Other Party', ''))

                    # Try to get credit/debit amounts
                    credit_str = row.get('Amount (credit)', row.get('Credit', ''))
                    debit_str = row.get('Amount (debit)', row.get('Debit', ''))
                    amount_str = row.get('Amount', '')

                    try:
                        credit = float(credit_str.replace(',', '').replace('$', '')) if credit_str else 0
                        debit = abs(float(debit_str.replace(',', '').replace('$', ''))) if debit_str else 0
                        if not credit and not debit and amount_str:
                            amt = float(amount_str.replace(',', '').replace('$', ''))
                            if amt > 0:
                                credit = amt
                            else:
                                debit = abs(amt)
                    except (ValueError, AttributeError):
                        credit = 0
                        debit = 0

                    if description or other_party:
                        txn = {
                            'date': date,
                            'description': (description or '').strip()[:60],
                            'other_party': (other_party or '').strip()[:30],
                            'credit': credit,
                            'debit': debit
                        }
                        transactions.append(txn)
                        income_total += credit
                        expense_total += debit

            # Format output
            lines.append(f"**TRANSACTION SUMMARY:**")
            lines.append(f"  - Total Credits (Income): ${income_total:.2f}")
            lines.append(f"  - Total Debits (Expenses): ${expense_total:.2f}")
            lines.append(f"  - Transaction Count: {len(transactions)}")
            lines.append("")

            # Categorize key transaction types
            rent_txns = [t for t in transactions if 'myrent' in t['description'].lower() or 'rent' in t['description'].lower()]
            loan_txns = [t for t in transactions if 'loan' in t['description'].lower()]
            utility_txns = [t for t in transactions if any(u in t['other_party'].lower() for u in ['watercare', 'power', 'electric', 'gas'])]

            if rent_txns:
                rent_total = sum(t['credit'] for t in rent_txns)
                lines.append(f"**RENT INCOME ({len(rent_txns)} transactions): ${rent_total:.2f}**")
                for txn in rent_txns[:10]:
                    lines.append(f"  {txn['date']} | {txn['description']} | ${txn['credit']:.2f}")
                lines.append("")

            if loan_txns:
                loan_total = sum(t['debit'] for t in loan_txns)
                lines.append(f"**LOAN PAYMENTS ({len(loan_txns)} transactions): ${loan_total:.2f}**")
                for txn in loan_txns[:5]:
                    lines.append(f"  {txn['date']} | {txn['description']} | ${txn['debit']:.2f}")
                lines.append("")

            # Show all transactions
            lines.append(f"\n**ALL TRANSACTIONS ({len(transactions)} items):**")
            for txn in transactions:
                if txn['credit'] > 0:
                    lines.append(f"  {txn['date']} | {txn['description']} | +${txn['credit']:.2f} | {txn['other_party']}")
                else:
                    lines.append(f"  {txn['date']} | {txn['description']} | -${txn['debit']:.2f} | {txn['other_party']}")

            logger.info(f"CSV bank statement extracted: {len(transactions)} transactions, income=${income_total:.2f}, expenses=${expense_total:.2f}")
            return "\n".join(lines)

        except Exception as e:
            logger.error(f"Error reading CSV bank statement {file_path}: {e}")
            return None

    def _format_rag_learnings(self, learnings: List) -> str:
        """Format RAG learnings for the prompt."""
        if not learnings:
            return ""

        lines = ["## Historical Patterns and Learnings (from similar returns)"]
        lines.append("Apply these learnings when analyzing transactions:\n")

        for learning in learnings[:15]:  # Increased from 5 to 15
            if isinstance(learning, dict):
                title = learning.get("title", "Pattern")
                content = learning.get("content", "")  # Full content, no truncation
                score = learning.get("score", 0)
                scenario = learning.get("scenario", "")

                # Format with context
                if score >= 0.8:
                    lines.append(f"**HIGH RELEVANCE** - {title}:")
                else:
                    lines.append(f"- {title}:")
                lines.append(f"  {content}")
                if scenario:
                    lines.append(f"  (Scenario: {scenario})")
                lines.append("")

        return "\n".join(lines)

    async def _call_claude(self, prompt: str) -> str:
        """Call Claude API with extended thinking for enhanced accuracy."""
        try:
            model = self.claude_client.model

            # Use extended thinking for supported models (opus-4, sonnet-4)
            supports_extended_thinking = "opus-4" in model or "sonnet-4" in model

            if supports_extended_thinking:
                logger.info("Using extended thinking for enhanced accuracy")
                # Extended thinking needs longer timeout (10 minutes)
                # Create a client with extended timeout for this call
                extended_client = AsyncAnthropic(
                    api_key=settings.ANTHROPIC_API_KEY,
                    timeout=httpx.Timeout(600.0, connect=60.0),  # 10 min timeout
                )
                response = await extended_client.messages.create(
                    model=model,
                    max_tokens=32000,  # Must be > budget_tokens
                    thinking={
                        "type": "enabled",
                        "budget_tokens": 10000  # Allow substantial thinking
                    },
                    messages=[{"role": "user", "content": prompt}]
                )
                # Extract text content from response (skip thinking blocks)
                for block in response.content:
                    if hasattr(block, 'text') and block.type == 'text':
                        return block.text
                # Fallback: return last block's text
                for block in reversed(response.content):
                    if hasattr(block, 'text'):
                        return block.text
                return ""
            else:
                # Standard call for older models
                response = await self.claude_client.client.messages.create(
                    model=model,
                    max_tokens=16384,
                    temperature=0.1,
                    messages=[{"role": "user", "content": prompt}]
                )
                return response.content[0].text

        except Exception as e:
            logger.error(f"Claude API call failed: {e}")
            raise

    def _parse_claude_response(
        self,
        response: str,
        context: Dict[str, Any]
    ) -> TaxReturnWorkingsData:
        """Parse Claude's response into TaxReturnWorkingsData."""

        tax_return = context["tax_return"]

        # Extract JSON from response
        try:
            # Try to find JSON in response
            if "```json" in response:
                json_str = response.split("```json")[1].split("```")[0].strip()
            elif "```" in response:
                json_str = response.split("```")[1].split("```")[0].strip()
            else:
                json_str = response.strip()

            data = json.loads(json_str)
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse Claude response as JSON: {e}")
            logger.error(f"Response was: {response[:500]}...")
            # Return empty workings with error flag
            return self._create_error_workings(tax_return, str(e))

        # Build workings from parsed data
        workings = TaxReturnWorkingsData(
            tax_return_id=UUID(tax_return["id"]),
            property_address=tax_return["property_address"],
            tax_year=tax_return["tax_year"],
            property_type=tax_return["property_type"],
            summary=WorkingsSummary(),
            income=IncomeWorkings(),
            expenses=ExpenseWorkings(),
            flags=[],
            document_requests=[],
            client_questions=[],
            documents_status=DocumentsStatus(),
            processing_notes=data.get("processing_notes", [])
        )

        # Parse income
        income_data = data.get("income", {})
        if income_data.get("rental_income"):
            ri = income_data["rental_income"]
            workings.income.rental_income = LineItem(
                category_code="rental_income",
                display_name="Rental Income",
                pl_row=ri.get("pl_row", 6),
                gross_amount=_safe_decimal(ri.get("amount")),
                deductible_percentage=0,  # Income, not deductible
                deductible_amount=Decimal("0"),
                source=ri.get("source", "Unknown"),
                source_code=ri.get("source_code", "BS"),
                verification_status=self._map_verification_status(ri.get("verification", "unverified")),
                notes=ri.get("notes"),
                calculation_logic=self._parse_calculation_logic(ri.get("calculation_logic"))
            )

        if income_data.get("water_rates_recovered"):
            wr = income_data["water_rates_recovered"]
            workings.income.water_rates_recovered = LineItem(
                category_code="water_rates_recovered",
                display_name="Water Rates Recovered",
                pl_row=wr.get("pl_row", 7),
                gross_amount=_safe_decimal(wr.get("amount")),
                deductible_percentage=0,
                deductible_amount=Decimal("0"),
                source=wr.get("source", "Unknown"),
                source_code=wr.get("source_code", "PM"),
                verification_status=self._map_verification_status(wr.get("verification", "unverified")),
                notes=wr.get("notes"),
                calculation_logic=self._parse_calculation_logic(wr.get("calculation_logic"))
            )

        if income_data.get("bank_contribution"):
            bc = income_data["bank_contribution"]
            workings.income.bank_contribution = LineItem(
                category_code="bank_contribution",
                display_name="Bank Contribution",
                pl_row=bc.get("pl_row", 8),
                gross_amount=_safe_decimal(bc.get("amount")),
                deductible_percentage=0,
                deductible_amount=Decimal("0"),
                source=bc.get("source", "Unknown"),
                source_code=bc.get("source_code", "BS"),
                verification_status=self._map_verification_status(bc.get("verification", "unverified")),
                notes=bc.get("notes"),
                calculation_logic=self._parse_calculation_logic(bc.get("calculation_logic"))
            )

        if income_data.get("other_income"):
            oi = income_data["other_income"]
            workings.income.other_income = LineItem(
                category_code="other_income",
                display_name="Other Income",
                pl_row=oi.get("pl_row"),
                gross_amount=_safe_decimal(oi.get("amount")),
                deductible_percentage=0,
                deductible_amount=Decimal("0"),
                source=oi.get("source", "Unknown"),
                source_code=oi.get("source_code", "BS"),
                verification_status=self._map_verification_status(oi.get("verification", "unverified")),
                notes=oi.get("notes"),
                calculation_logic=self._parse_calculation_logic(oi.get("calculation_logic"))
            )

        # Parse expenses
        expenses_data = data.get("expenses", {})

        # Interest
        if expenses_data.get("interest"):
            int_data = expenses_data["interest"]
            workings.expenses.interest = LineItem(
                category_code="interest",
                display_name="Mortgage Interest",
                pl_row=int_data.get("pl_row", 25),
                gross_amount=_safe_decimal(_safe_abs(int_data.get("gross_amount"))),
                deductible_percentage=float(int_data.get("deductible_percentage") or 100),
                deductible_amount=_safe_decimal(_safe_abs(int_data.get("deductible_amount"))),
                source=int_data.get("source", "Unknown"),
                source_code=int_data.get("source_code", "LS"),
                verification_status=self._map_verification_status(int_data.get("verification", "unverified")),
                notes=int_data.get("notes"),
                monthly_breakdown=_sanitize_monthly_breakdown(int_data.get("monthly_breakdown")),
                calculation_logic=self._parse_calculation_logic(int_data.get("calculation_logic"))
            )

        # Rates
        if expenses_data.get("rates"):
            rates_data = expenses_data["rates"]
            amount = _safe_decimal(_safe_abs(rates_data.get("amount")))
            workings.expenses.rates = LineItem(
                category_code="rates",
                display_name="Council Rates",
                pl_row=rates_data.get("pl_row", 34),
                gross_amount=amount,
                deductible_percentage=100.0,
                deductible_amount=amount,
                source=rates_data.get("source", "Unknown"),
                source_code=rates_data.get("source_code", "BS"),
                verification_status=self._map_verification_status(rates_data.get("verification", "unverified")),
                notes=rates_data.get("notes"),
                calculation_logic=self._parse_calculation_logic(rates_data.get("calculation_logic"))
            )

        # Insurance
        if expenses_data.get("insurance"):
            ins_data = expenses_data["insurance"]
            amount = _safe_decimal(_safe_abs(ins_data.get("amount")))
            workings.expenses.insurance = LineItem(
                category_code="insurance",
                display_name="Landlord Insurance",
                pl_row=ins_data.get("pl_row", 24),
                gross_amount=amount,
                deductible_percentage=100.0,
                deductible_amount=amount,
                source=ins_data.get("source", "Unknown"),
                source_code=ins_data.get("source_code", "BS"),
                verification_status=self._map_verification_status(ins_data.get("verification", "unverified")),
                notes=ins_data.get("notes"),
                calculation_logic=self._parse_calculation_logic(ins_data.get("calculation_logic"))
            )

        # Agent fees
        if expenses_data.get("agent_fees"):
            af_data = expenses_data["agent_fees"]
            amount = _safe_decimal(_safe_abs(af_data.get("amount")))
            workings.expenses.agent_fees = LineItem(
                category_code="agent_fees",
                display_name="Property Management Fees",
                pl_row=af_data.get("pl_row", 13),
                gross_amount=amount,
                deductible_percentage=100.0,
                deductible_amount=amount,
                source=af_data.get("source", "Unknown"),
                source_code=af_data.get("source_code", "PM"),
                verification_status=self._map_verification_status(af_data.get("verification", "unverified")),
                notes=af_data.get("notes"),
                calculation_logic=self._parse_calculation_logic(af_data.get("calculation_logic"))
            )

        # Water Rates
        if expenses_data.get("water_rates"):
            wr_data = expenses_data["water_rates"]
            amount = _safe_decimal(_safe_abs(wr_data.get("amount")))
            workings.expenses.water_rates = LineItem(
                category_code="water_rates",
                display_name="Water Rates",
                pl_row=wr_data.get("pl_row", 41),
                gross_amount=amount,
                deductible_percentage=100.0,
                deductible_amount=amount,
                source=wr_data.get("source", "Unknown"),
                source_code=wr_data.get("source_code", "BS"),
                verification_status=self._map_verification_status(wr_data.get("verification", "unverified")),
                notes=wr_data.get("notes"),
                calculation_logic=self._parse_calculation_logic(wr_data.get("calculation_logic"))
            )

        # Body Corporate
        if expenses_data.get("body_corporate"):
            bc_data = expenses_data["body_corporate"]
            amount = _safe_decimal(_safe_abs(bc_data.get("amount")))
            workings.expenses.body_corporate = LineItem(
                category_code="body_corporate",
                display_name="Body Corporate",
                pl_row=bc_data.get("pl_row", 15),
                gross_amount=amount,
                deductible_percentage=100.0,
                deductible_amount=amount,
                source=bc_data.get("source", "Unknown"),
                source_code=bc_data.get("source_code", "BS"),
                verification_status=self._map_verification_status(bc_data.get("verification", "unverified")),
                notes=bc_data.get("notes"),
                calculation_logic=self._parse_calculation_logic(bc_data.get("calculation_logic"))
            )

        # Legal Fees
        if expenses_data.get("legal_fees"):
            lf_data = expenses_data["legal_fees"]
            amount = _safe_decimal(_safe_abs(lf_data.get("amount")))
            workings.expenses.legal_fees = LineItem(
                category_code="legal_fees",
                display_name="Legal Fees",
                pl_row=lf_data.get("pl_row", 27),
                gross_amount=amount,
                deductible_percentage=100.0,
                deductible_amount=amount,
                source=lf_data.get("source", "Unknown"),
                source_code=lf_data.get("source_code", "SS"),
                verification_status=self._map_verification_status(lf_data.get("verification", "unverified")),
                notes=lf_data.get("notes"),
                calculation_logic=self._parse_calculation_logic(lf_data.get("calculation_logic"))
            )

        # Bank Fees
        if expenses_data.get("bank_fees"):
            bf_data = expenses_data["bank_fees"]
            amount = _safe_decimal(_safe_abs(bf_data.get("amount")))
            workings.expenses.bank_fees = LineItem(
                category_code="bank_fees",
                display_name="Bank Fees",
                pl_row=bf_data.get("pl_row", 14),
                gross_amount=amount,
                deductible_percentage=100.0,
                deductible_amount=amount,
                source=bf_data.get("source", "Unknown"),
                source_code=bf_data.get("source_code", "BS"),
                verification_status=self._map_verification_status(bf_data.get("verification", "unverified")),
                notes=bf_data.get("notes"),
                calculation_logic=self._parse_calculation_logic(bf_data.get("calculation_logic"))
            )

        # Advertising
        if expenses_data.get("advertising"):
            ad_data = expenses_data["advertising"]
            amount = _safe_decimal(_safe_abs(ad_data.get("amount")))
            workings.expenses.advertising = LineItem(
                category_code="advertising",
                display_name="Advertising",
                pl_row=ad_data.get("pl_row", 12),
                gross_amount=amount,
                deductible_percentage=100.0,
                deductible_amount=amount,
                source=ad_data.get("source", "Unknown"),
                source_code=ad_data.get("source_code", "BS"),
                verification_status=self._map_verification_status(ad_data.get("verification", "unverified")),
                notes=ad_data.get("notes"),
                calculation_logic=self._parse_calculation_logic(ad_data.get("calculation_logic"))
            )

        # Resident Society (separate from Body Corporate)
        if expenses_data.get("resident_society"):
            rs_data = expenses_data["resident_society"]
            amount = _safe_decimal(_safe_abs(rs_data.get("amount")))
            workings.expenses.resident_society = LineItem(
                category_code="resident_society",
                display_name="Resident Society",
                pl_row=rs_data.get("pl_row", 36),
                gross_amount=amount,
                deductible_percentage=100.0,
                deductible_amount=amount,
                source=rs_data.get("source", "Unknown"),
                source_code=rs_data.get("source_code", "BS"),
                verification_status=self._map_verification_status(rs_data.get("verification", "unverified")),
                notes=rs_data.get("notes"),
                calculation_logic=self._parse_calculation_logic(rs_data.get("calculation_logic"))
            )

        # Depreciation
        if expenses_data.get("depreciation"):
            dep_data = expenses_data["depreciation"]
            amount = _safe_decimal(_safe_abs(dep_data.get("amount")))
            workings.expenses.depreciation = LineItem(
                category_code="depreciation",
                display_name="Depreciation",
                pl_row=dep_data.get("pl_row", 17),
                gross_amount=amount,
                deductible_percentage=100.0,
                deductible_amount=amount,
                source=dep_data.get("source", "Unknown"),
                source_code=dep_data.get("source_code", "DEP"),
                verification_status=self._map_verification_status(dep_data.get("verification", "unverified")),
                notes=dep_data.get("notes"),
                calculation_logic=self._parse_calculation_logic(dep_data.get("calculation_logic"))
            )

        # Accounting Fees (standard $862.50)
        if expenses_data.get("accounting_fees"):
            af_data = expenses_data["accounting_fees"]
            amount = _safe_decimal(_safe_abs(af_data.get("amount"), 862.50))
            workings.expenses.accounting_fees = LineItem(
                category_code="accounting_fees",
                display_name="Consulting & Accounting",
                pl_row=af_data.get("pl_row", 16),
                gross_amount=amount,
                deductible_percentage=100.0,
                deductible_amount=amount,
                source=af_data.get("source", "Standard Fee"),
                source_code=af_data.get("source_code", "AF"),
                verification_status=self._map_verification_status(af_data.get("verification", "verified")),
                notes=af_data.get("notes", "Standard accounting fee"),
                calculation_logic=self._parse_calculation_logic(af_data.get("calculation_logic"))
            )

        # Due Diligence (LIM, meth test, healthy homes, etc.)
        if expenses_data.get("due_diligence"):
            dd_data = expenses_data["due_diligence"]
            amount = _safe_decimal(_safe_abs(dd_data.get("amount")))
            workings.expenses.due_diligence = LineItem(
                category_code="due_diligence",
                display_name="Due Diligence",
                pl_row=dd_data.get("pl_row", 18),
                gross_amount=amount,
                deductible_percentage=100.0,
                deductible_amount=amount,
                source=dd_data.get("source", "Unknown"),
                source_code=dd_data.get("source_code", "INV"),
                verification_status=self._map_verification_status(dd_data.get("verification", "unverified")),
                notes=dd_data.get("notes"),
                calculation_logic=self._parse_calculation_logic(dd_data.get("calculation_logic"))
            )

        # Other Expenses
        if expenses_data.get("other_expenses"):
            oe_data = expenses_data["other_expenses"]
            amount = _safe_decimal(_safe_abs(oe_data.get("amount")))
            workings.expenses.other_expenses = LineItem(
                category_code="other_expenses",
                display_name="Other Expenses",
                pl_row=oe_data.get("pl_row"),
                gross_amount=amount,
                deductible_percentage=100.0,
                deductible_amount=amount,
                source=oe_data.get("source", "Unknown"),
                source_code=oe_data.get("source_code", "BS"),
                verification_status=self._map_verification_status(oe_data.get("verification", "unverified")),
                notes=oe_data.get("notes"),
                calculation_logic=self._parse_calculation_logic(oe_data.get("calculation_logic"))
            )

        # Home Office (Personal Expenditure Claims - Row 37)
        if expenses_data.get("home_office"):
            ho_data = expenses_data["home_office"]
            amount = _safe_decimal(_safe_abs(ho_data.get("amount")))
            if amount > 0:
                workings.expenses.home_office = LineItem(
                    category_code="home_office",
                    display_name="Home Office",
                    pl_row=ho_data.get("pl_row", 37),
                    gross_amount=amount,
                    deductible_percentage=100.0,
                    deductible_amount=amount,
                    source=ho_data.get("source", "Personal Expenditure Claims"),
                    source_code=ho_data.get("source_code", "CP"),
                    verification_status=self._map_verification_status(ho_data.get("verification", "verified")),
                    notes=ho_data.get("notes"),
                    calculation_logic=self._parse_calculation_logic(ho_data.get("calculation_logic"))
                )

        # Mobile Phone (Personal Expenditure Claims - Row 37)
        if expenses_data.get("mobile_phone"):
            mp_data = expenses_data["mobile_phone"]
            amount = _safe_decimal(_safe_abs(mp_data.get("amount")))
            if amount > 0:
                workings.expenses.mobile_phone = LineItem(
                    category_code="mobile_phone",
                    display_name="Mobile Phone",
                    pl_row=mp_data.get("pl_row", 37),
                    gross_amount=amount,
                    deductible_percentage=100.0,
                    deductible_amount=amount,
                    source=mp_data.get("source", "Personal Expenditure Claims"),
                    source_code=mp_data.get("source_code", "CP"),
                    verification_status=self._map_verification_status(mp_data.get("verification", "verified")),
                    notes=mp_data.get("notes"),
                    calculation_logic=self._parse_calculation_logic(mp_data.get("calculation_logic"))
                )

        # Mileage (Personal Expenditure Claims - Row 37)
        if expenses_data.get("mileage"):
            ml_data = expenses_data["mileage"]
            amount = _safe_decimal(_safe_abs(ml_data.get("amount")))
            if amount > 0:
                workings.expenses.mileage = LineItem(
                    category_code="mileage",
                    display_name="Mileage",
                    pl_row=ml_data.get("pl_row", 37),
                    gross_amount=amount,
                    deductible_percentage=100.0,
                    deductible_amount=amount,
                    source=ml_data.get("source", "Personal Expenditure Claims"),
                    source_code=ml_data.get("source_code", "CP"),
                    verification_status=self._map_verification_status(ml_data.get("verification", "verified")),
                    notes=ml_data.get("notes"),
                    calculation_logic=self._parse_calculation_logic(ml_data.get("calculation_logic"))
                )

        # Repairs
        if expenses_data.get("repairs_maintenance"):
            rep_data = expenses_data["repairs_maintenance"]
            items = []
            for item in rep_data.get("items", []):
                # Parse date string to date object
                date_val = None
                if item.get("date"):
                    try:
                        date_val = datetime.strptime(item["date"], "%Y-%m-%d").date()
                    except (ValueError, TypeError):
                        pass
                items.append(RepairItem(
                    date=date_val,
                    description=item.get("description", ""),
                    amount=_safe_decimal(_safe_abs(item.get("amount"))),
                    payee=item.get("payee"),
                    invoice_status=item.get("invoice_status", "not_required")
                ))

            total = _safe_decimal(_safe_abs(rep_data.get("total_amount")))
            workings.expenses.repairs_maintenance = RepairsLineItem(
                category_code="repairs_maintenance",
                display_name="Repairs & Maintenance",
                pl_row=rep_data.get("pl_row", 35),
                gross_amount=total,
                deductible_percentage=100.0,
                deductible_amount=total,
                source=rep_data.get("source", "Bank Statement / Invoices"),
                source_code=rep_data.get("source_code", "BS"),
                verification_status=VerificationStatus.NEEDS_REVIEW,
                repair_items=items,
                items_requiring_invoice=sum(1 for i in items if i.invoice_status == "missing_required"),
                calculation_logic=self._parse_calculation_logic(rep_data.get("calculation_logic"))
            )

        # Parse flags
        for flag in data.get("flags", []):
            workings.flags.append(WorkingsFlagData(
                severity=FlagSeverityEnum.from_string(flag.get("severity", "medium")),
                category=FlagCategoryEnum(flag.get("category", "review_required")),
                message=flag.get("message", ""),
                action_required=flag.get("action_required", "")
            ))

        # Parse document requests
        for req in data.get("document_requests", []):
            workings.document_requests.append(DocumentRequestData(
                document_type=req.get("document_type", ""),
                reason=req.get("reason", ""),
                priority=req.get("priority", "required")
            ))

        # Parse client questions
        for q in data.get("client_questions", []):
            workings.client_questions.append(ClientQuestionData(
                question=q.get("question", ""),
                context=q.get("context"),
                options=q.get("options", []),
                related_amount=_safe_decimal(q.get("related_amount")) if q.get("related_amount") else None
            ))

        # Parse documents status
        doc_status = data.get("documents_status", {})
        if doc_status:
            for doc_type in ["pm_statement", "bank_statement", "loan_statement", "rates_invoice", "insurance_policy"]:
                if doc_type in doc_status:
                    status_data = doc_status[doc_type]
                    setattr(workings.documents_status, doc_type, DocumentStatusData(
                        status=status_data.get("status", "missing"),
                        notes=status_data.get("notes")
                    ))

        return workings

    def _map_verification_status(self, status: str) -> VerificationStatus:
        """Map string status to VerificationStatus enum."""
        mapping = {
            "verified": VerificationStatus.VERIFIED,
            "needs_review": VerificationStatus.NEEDS_REVIEW,
            "unverified": VerificationStatus.UNVERIFIED,
            "missing_invoice": VerificationStatus.MISSING_INVOICE,
            "missing_loan_statement": VerificationStatus.NEEDS_REVIEW,
            "estimated": VerificationStatus.ESTIMATED
        }
        return mapping.get(status.lower(), VerificationStatus.UNVERIFIED)

    def _parse_calculation_logic(self, calc_data: Optional[Dict]) -> Optional[CalculationLogic]:
        """Parse calculation_logic from Claude response into CalculationLogic model."""
        if not calc_data:
            return None

        try:
            return CalculationLogic(
                primary_source_code=calc_data.get("primary_source_code", "BS"),
                primary_source_name=calc_data.get("primary_source_name", "Unknown"),
                calculation_method=calc_data.get("calculation_method", ""),
                formula=calc_data.get("formula"),
                calculation_steps=calc_data.get("calculation_steps", []),
                cross_validated_with=calc_data.get("cross_validated_with", []),
                validation_status=calc_data.get("validation_status", "not_validated"),
                variance_amount=_safe_decimal(calc_data.get("variance_amount")) if calc_data.get("variance_amount") else None,
                variance_notes=calc_data.get("variance_notes"),
                adjustments=calc_data.get("adjustments", []),
                source_references=[]  # Could parse if provided
            )
        except Exception as e:
            logger.warning(f"Failed to parse calculation_logic: {e}")
            return None

    def _create_error_workings(
        self,
        tax_return: Dict[str, Any],
        error_message: str
    ) -> TaxReturnWorkingsData:
        """Create empty workings with error flag."""
        workings = TaxReturnWorkingsData(
            tax_return_id=UUID(tax_return["id"]),
            property_address=tax_return["property_address"],
            tax_year=tax_return["tax_year"],
            property_type=tax_return["property_type"],
            summary=WorkingsSummary(),
            income=IncomeWorkings(),
            expenses=ExpenseWorkings(),
            flags=[
                WorkingsFlagData(
                    severity=FlagSeverityEnum.HIGH,
                    category=FlagCategoryEnum.REVIEW_REQUIRED,
                    message=f"Failed to process: {error_message}",
                    action_required="Manual processing required"
                )
            ],
            processing_notes=[f"Error during processing: {error_message}"]
        )
        return workings

    async def _get_existing_workings(
        self,
        tax_return_id: UUID,
        db: AsyncSession
    ) -> Optional[TaxReturnWorkings]:
        """Get existing workings if any (latest version)."""
        result = await db.execute(
            select(TaxReturnWorkings).where(
                TaxReturnWorkings.tax_return_id == tax_return_id
            ).order_by(TaxReturnWorkings.version.desc()).limit(1)
        )
        return result.scalar_one_or_none()

    async def _save_workings(
        self,
        workings: TaxReturnWorkingsData,
        tax_return_id: UUID,
        processing_time: float,
        db: AsyncSession
    ) -> TaxReturnWorkings:
        """Save workings to database."""

        # Get next version number
        existing = await self._get_existing_workings(tax_return_id, db)
        version = (existing.version + 1) if existing else 1

        # Create workings record
        db_workings = TaxReturnWorkings(
            tax_return_id=tax_return_id,
            version=version,
            total_income=workings.summary.total_income,
            total_expenses=workings.summary.total_expenses,
            total_deductions=workings.summary.total_deductions,
            net_rental_income=workings.summary.net_rental_income,
            interest_gross=workings.summary.interest_gross,
            interest_deductible_percentage=workings.summary.interest_deductible_percentage,
            interest_deductible_amount=workings.summary.interest_deductible_amount,
            income_workings=workings.income.model_dump(mode="json") if workings.income else None,
            expense_workings=workings.expenses.model_dump(mode="json") if workings.expenses else None,
            document_inventory=workings.documents_status.model_dump(mode="json") if workings.documents_status else None,
            processing_notes=workings.processing_notes,
            audit_trail=workings.audit_trail,
            ai_model_used=self.claude_client.model,
            ai_prompt_version=self.PROMPT_VERSION,
            processing_time_seconds=processing_time,
            status=WorkingsStatus.DRAFT
        )
        db.add(db_workings)
        await db.flush()

        # Create flag records
        for flag in workings.flags:
            db_flag = WorkingsFlag(
                workings_id=db_workings.id,
                severity=FlagSeverity(flag.severity.value),
                category=FlagCategory(flag.category.value),
                message=flag.message,
                action_required=flag.action_required
            )
            db.add(db_flag)

        # Create document request records
        for req in workings.document_requests:
            db_req = DocumentRequest(
                workings_id=db_workings.id,
                tax_return_id=tax_return_id,
                document_type=req.document_type,
                reason=req.reason,
                priority=req.priority,
                details=req.details
            )
            db.add(db_req)

        # Create client question records
        for q in workings.client_questions:
            db_q = ClientQuestion(
                workings_id=db_workings.id,
                tax_return_id=tax_return_id,
                question=q.question,
                context=q.context,
                options=q.options,
                related_amount=q.related_amount,
                affects_category=q.affects_category,
                affects_deductibility=q.affects_deductibility
            )
            db.add(db_q)

        await db.commit()

        return db_workings

    async def _qa_validate_calculations(
        self,
        workings: TaxReturnWorkingsData,
        context: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """
        QA Step: Validate all calculations for accuracy.

        Returns a list of validation issues found.
        """
        issues = []

        # 1. Validate income totals
        if workings.income:
            calculated_income = Decimal("0")
            for field_name in ["rental_income", "water_recovered", "bank_contribution", "other_income"]:
                item = getattr(workings.income, field_name, None)
                if item and item.gross_amount:
                    try:
                        calculated_income += Decimal(str(item.gross_amount))
                    except:
                        pass

            reported_income = Decimal(str(workings.summary.total_income or 0))
            diff = abs(calculated_income - reported_income)
            if diff > Decimal("0.01"):
                issues.append({
                    "type": "income_mismatch",
                    "severity": "high",
                    "message": f"Income total mismatch: sum of items=${calculated_income:.2f}, reported=${reported_income:.2f}",
                    "calculated": float(calculated_income),
                    "reported": float(reported_income),
                    "difference": float(diff)
                })

        # 2. Validate expense totals
        if workings.expenses:
            calculated_expenses = Decimal("0")
            expense_fields = [
                "advertising", "agent_fees", "bank_fees", "body_corporate",
                "accounting_fees", "depreciation", "due_diligence", "insurance",
                "interest", "legal_fees", "rates", "repairs", "resident_society",
                "travel", "water_rates", "other_expenses"
            ]
            for field_name in expense_fields:
                item = getattr(workings.expenses, field_name, None)
                if item:
                    # Use deductible_amount if available, otherwise gross_amount
                    amount = item.deductible_amount if item.deductible_amount else item.gross_amount
                    if amount:
                        try:
                            calculated_expenses += Decimal(str(amount))
                        except:
                            pass

            reported_expenses = Decimal(str(workings.summary.total_deductions or 0))
            diff = abs(calculated_expenses - reported_expenses)
            if diff > Decimal("0.01"):
                issues.append({
                    "type": "expense_mismatch",
                    "severity": "high",
                    "message": f"Expense total mismatch: sum of items=${calculated_expenses:.2f}, reported=${reported_expenses:.2f}",
                    "calculated": float(calculated_expenses),
                    "reported": float(reported_expenses),
                    "difference": float(diff)
                })

        # 3. Validate interest calculation (deductibility applied correctly)
        if workings.expenses and workings.expenses.interest:
            interest = workings.expenses.interest
            if interest.gross_amount and interest.deductible_percentage:
                expected_deductible = Decimal(str(interest.gross_amount)) * Decimal(str(interest.deductible_percentage)) / 100
                reported_deductible = Decimal(str(interest.deductible_amount or 0))
                diff = abs(expected_deductible - reported_deductible)
                if diff > Decimal("0.01"):
                    issues.append({
                        "type": "interest_deductibility_error",
                        "severity": "high",
                        "message": f"Interest deductibility error: {interest.gross_amount} × {interest.deductible_percentage}% = ${expected_deductible:.2f}, but reported ${reported_deductible:.2f}",
                        "expected": float(expected_deductible),
                        "reported": float(reported_deductible)
                    })

        # 4. Validate net rental income calculation
        expected_net = Decimal(str(workings.summary.total_income or 0)) - Decimal(str(workings.summary.total_deductions or 0))
        reported_net = Decimal(str(workings.summary.net_rental_income or 0))
        diff = abs(expected_net - reported_net)
        if diff > Decimal("0.01"):
            issues.append({
                "type": "net_income_error",
                "severity": "high",
                "message": f"Net income calculation error: {workings.summary.total_income} - {workings.summary.total_deductions} = ${expected_net:.2f}, but reported ${reported_net:.2f}",
                "expected": float(expected_net),
                "reported": float(reported_net)
            })

        # 5. Sanity checks
        if workings.summary.total_income and workings.summary.total_income < 0:
            issues.append({
                "type": "negative_income",
                "severity": "medium",
                "message": f"Total income is negative: ${workings.summary.total_income} - this is unusual"
            })

        if workings.summary.total_deductions and workings.summary.total_deductions < 0:
            issues.append({
                "type": "negative_deductions",
                "severity": "medium",
                "message": f"Total deductions is negative: ${workings.summary.total_deductions} - this is unusual"
            })

        # 6. Check for suspiciously round numbers (might indicate guessing)
        for field_name in ["rental_income", "rates", "insurance"]:
            if workings.income and field_name == "rental_income":
                item = workings.income.rental_income
            elif workings.expenses:
                item = getattr(workings.expenses, field_name, None)
            else:
                item = None

            if item and item.gross_amount:
                amount = float(item.gross_amount)
                # Check if it's a suspiciously round number (exactly divisible by 1000 and > 1000)
                if amount > 1000 and amount % 1000 == 0:
                    issues.append({
                        "type": "round_number_warning",
                        "severity": "low",
                        "message": f"{field_name} is a round number (${amount}) - verify this is accurate",
                        "field": field_name,
                        "amount": amount
                    })

        logger.info(f"QA Validation found {len(issues)} issues")
        return issues

    async def _qa_verify_with_claude(
        self,
        workings: TaxReturnWorkingsData,
        context: Dict[str, Any],
        issues: List[Dict[str, Any]]
    ) -> TaxReturnWorkingsData:
        """
        QA Step: Have Claude verify and correct critical calculations.

        Only called if validation issues are found.
        """
        if not issues:
            return workings

        # Only verify high-severity issues
        high_severity = [i for i in issues if i.get("severity") == "high"]
        if not high_severity:
            # Just add warnings to processing notes
            for issue in issues:
                workings.processing_notes.append(f"QA Warning: {issue['message']}")
            return workings

        logger.info(f"QA verification needed for {len(high_severity)} high-severity issues")

        # Build verification prompt
        issues_text = "\n".join([f"- {i['message']}" for i in high_severity])

        # Get the current workings summary
        current_values = {
            "total_income": float(workings.summary.total_income or 0),
            "total_deductions": float(workings.summary.total_deductions or 0),
            "net_rental_income": float(workings.summary.net_rental_income or 0),
            "interest_gross": float(workings.summary.interest_gross or 0) if workings.summary.interest_gross else 0,
            "interest_deductible": float(workings.summary.interest_deductible_amount or 0) if workings.summary.interest_deductible_amount else 0
        }

        verification_prompt = f"""You are performing QA verification on tax return workings.

## CALCULATION ISSUES DETECTED:
{issues_text}

## CURRENT VALUES:
{json.dumps(current_values, indent=2)}

## YOUR TASK:
Review these calculation errors and provide CORRECTED values.

For each calculation:
1. Show your working step-by-step
2. Identify what went wrong
3. Provide the correct value

Respond with ONLY a JSON object:
{{
    "corrections": {{
        "total_income": <correct value or null if no change needed>,
        "total_deductions": <correct value or null if no change needed>,
        "net_rental_income": <correct value or null if no change needed>,
        "interest_deductible_amount": <correct value or null if no change needed>
    }},
    "verification_notes": [
        "Explanation of each correction..."
    ]
}}
"""

        try:
            response = await self.claude_client.extract_with_prompt(prompt=verification_prompt)

            # Parse corrections
            import re
            json_match = re.search(r'\{.*\}', response, re.DOTALL)
            if json_match:
                corrections = json.loads(json_match.group())

                # Apply corrections
                if corrections.get("corrections"):
                    corr = corrections["corrections"]
                    if corr.get("total_income") is not None:
                        workings.summary.total_income = _safe_decimal(corr["total_income"])
                        logger.info(f"QA corrected total_income to {corr['total_income']}")
                    if corr.get("total_deductions") is not None:
                        workings.summary.total_deductions = _safe_decimal(corr["total_deductions"])
                        logger.info(f"QA corrected total_deductions to {corr['total_deductions']}")
                    if corr.get("net_rental_income") is not None:
                        workings.summary.net_rental_income = _safe_decimal(corr["net_rental_income"])
                        logger.info(f"QA corrected net_rental_income to {corr['net_rental_income']}")
                    if corr.get("interest_deductible_amount") is not None and workings.expenses and workings.expenses.interest:
                        workings.expenses.interest.deductible_amount = _safe_decimal(corr["interest_deductible_amount"])
                        workings.summary.interest_deductible_amount = _safe_decimal(corr["interest_deductible_amount"])
                        logger.info(f"QA corrected interest_deductible_amount to {corr['interest_deductible_amount']}")

                # Add verification notes
                if corrections.get("verification_notes"):
                    for note in corrections["verification_notes"]:
                        workings.processing_notes.append(f"QA Verification: {note}")

                workings.processing_notes.append("QA: Calculations verified and corrected by AI")

        except Exception as e:
            logger.error(f"QA verification failed: {e}")
            workings.processing_notes.append(f"QA Warning: Verification failed - {str(e)}")
            # Add original issues as warnings
            for issue in high_severity:
                workings.flags.append(WorkingsFlagData(
                    severity=FlagSeverityEnum.HIGH,
                    category=FlagCategoryEnum.REVIEW_REQUIRED,
                    message=f"QA Issue: {issue['message']}",
                    action_required="Manual verification required"
                ))

        return workings


# Singleton instance
_ai_brain: Optional[AIBrain] = None


def get_ai_brain() -> AIBrain:
    """Get or create singleton AI Brain."""
    global _ai_brain

    if _ai_brain is None:
        _ai_brain = AIBrain()

    return _ai_brain
