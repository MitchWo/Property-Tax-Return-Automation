"""Transaction categorizer service with multi-layer categorization and learning."""
import logging
import re
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.db_models import (
    CategoryFeedback,
    PLRowMapping,
    TaxReturn,
    TaxRule,
    Transaction,
    TransactionPattern,
    TransactionSummary,
)
from app.rules.loader import get_pattern_matcher
from app.schemas.transactions import (
    ExtractedTransaction,
    TransactionCreate,
)
from app.services.categorization_trace import CategorizationTrace
from app.services.phase1_document_intake.claude_client import ClaudeClient
from app.services.rag_categorization_integration import get_rag_integration
from app.services.skill_loader import get_skill_loader

logger = logging.getLogger(__name__)


class TransactionCategorizer:
    """
    Multi-layer transaction categorization engine.

    Layers (in order of priority):
    1. Tax Rules - Check deductibility rules
    2. YAML Patterns - Fast regex/payee matching
    3. Learned Patterns (Exact) - Previously confirmed categorizations
    4. Learned Patterns (Fuzzy) - Similar descriptions via pg_trgm
    5. Claude AI - For uncertain items
    6. Human Review - Flag for manual review
    """

    def __init__(self):
        """Initialize categorizer."""
        self.pattern_matcher = get_pattern_matcher()
        self.skill_loader = get_skill_loader()
        self.claude_client = ClaudeClient()

        # Confidence thresholds
        self.AUTO_ACCEPT_THRESHOLD = 0.90  # Auto-accept if above this
        self.REVIEW_THRESHOLD = 0.70       # Flag for review if below this
        self.FUZZY_SIMILARITY_THRESHOLD = 0.6  # pg_trgm similarity threshold

    async def categorize_transaction(
        self,
        db: AsyncSession,
        transaction: ExtractedTransaction,
        tax_return: TaxReturn,
        use_claude: bool = True,
        document_context: Optional[Dict[str, Any]] = None
    ) -> TransactionCreate:
        """
        Categorize a single transaction through all layers.

        Args:
            db: Database session
            transaction: Extracted transaction to categorize
            tax_return: Associated tax return for context
            use_claude: Whether to use Claude for uncertain items
            document_context: Cross-document context (loan accounts, client names)

        Returns:
            TransactionCreate with categorization applied
        """
        # Determine transaction type from amount
        txn_type = "income" if transaction.amount > 0 else "expense"

        # Initialize trace
        trace = CategorizationTrace()

        # Initialize result
        result = TransactionCreate(
            tax_return_id=tax_return.id,
            transaction_date=transaction.transaction_date,
            description=transaction.description,
            other_party=transaction.other_party,
            amount=transaction.amount,
            balance=transaction.balance,
            raw_data=transaction.raw_data,
            confidence=0.0,
            needs_review=True
        )

        # If transaction already has a suggested category with high confidence, use it
        if hasattr(transaction, 'suggested_category') and transaction.suggested_category and transaction.confidence >= self.AUTO_ACCEPT_THRESHOLD:
            result.category_code = transaction.suggested_category
            result.confidence = transaction.confidence
            result.categorization_source = "extraction"
            result.needs_review = getattr(transaction, 'needs_review', False)
            result.review_reason = getattr(transaction, 'review_reason', None)
            return await self._apply_tax_rules(db, result, tax_return)

        # Layer 1: Cross-Document Context Matching (loan account transfers, etc.)
        if document_context:
            context_match = self._match_document_context(
                transaction.description,
                transaction.other_party,
                float(transaction.amount),
                document_context
            )
            if context_match and context_match["confidence"] >= self.AUTO_ACCEPT_THRESHOLD:
                logger.info(f"Document context match: {transaction.description[:50]} -> {context_match['category']}")
                result.category_code = context_match["category"]
                result.confidence = context_match["confidence"]
                result.categorization_source = "document_context"
                result.needs_review = False
                result.review_reason = context_match.get("reason")
                trace.record_context_match(
                    True,
                    context_match["category"],
                    context_match["confidence"],
                    context_match.get("reason", "")
                )
                result.categorization_trace = trace.to_dict()
                return await self._apply_tax_rules(db, result, tax_return)

        # Layer 2: YAML Pattern Matching
        yaml_match = self._match_yaml_patterns(
            transaction.description,
            transaction.other_party,
            float(transaction.amount),
            txn_type
        )

        if yaml_match:
            trace.record_yaml_match(
                True,
                yaml_match["category"],
                yaml_match["confidence"],
                yaml_match.get("pattern_name", "unknown")
            )
            if yaml_match["confidence"] >= self.AUTO_ACCEPT_THRESHOLD:
                result.category_code = yaml_match["category"]
                result.confidence = yaml_match["confidence"]
                result.categorization_source = yaml_match["source"]
                result.needs_review = yaml_match.get("flag_for_review", False)
                result.review_reason = yaml_match.get("review_reason")
                result.categorization_trace = trace.to_dict()
                return await self._apply_tax_rules(db, result, tax_return)
        else:
            trace.record_yaml_match(False)

        # Layer 3: Learned Patterns (Exact Match)
        exact_match = await self._match_learned_exact(
            db, transaction.description, transaction.other_party
        )

        if exact_match:
            trace.record_learned_match(
                True,
                exact_match["category"],
                exact_match["confidence"],
                exact_match.get("times_applied", 0)
            )
            if exact_match["confidence"] >= self.AUTO_ACCEPT_THRESHOLD:
                result.category_code = exact_match["category"]
                result.confidence = exact_match["confidence"]
                result.categorization_source = "learned_exact"
                result.needs_review = False
                result.categorization_trace = trace.to_dict()
                return await self._apply_tax_rules(db, result, tax_return)

        # Layer 4: Learned Patterns (Fuzzy Match via pg_trgm)
        fuzzy_match = await self._match_learned_fuzzy(
            db, transaction.description
        )

        if fuzzy_match:
            trace.record_learned_match(
                True,
                fuzzy_match["category"],
                fuzzy_match["confidence"],
                fuzzy_match.get("times_applied", 0)
            )
            if fuzzy_match["confidence"] >= self.AUTO_ACCEPT_THRESHOLD:
                result.category_code = fuzzy_match["category"]
                result.confidence = fuzzy_match["confidence"] * 0.95  # Slight penalty for fuzzy
                result.categorization_source = "learned_fuzzy"
                result.needs_review = result.confidence < self.AUTO_ACCEPT_THRESHOLD
                if result.needs_review:
                    result.review_reason = f"Fuzzy match to: {fuzzy_match.get('matched_description', '')}"
                result.categorization_trace = trace.to_dict()
                return await self._apply_tax_rules(db, result, tax_return)
        elif not exact_match:
            # Only record no learned match if neither exact nor fuzzy matched
            trace.record_learned_match(False)

        # ===== Layer 5: RAG Learnings =====
        rag_prompt_addition = ""  # Initialize for Claude use later
        try:
            rag_integration = get_rag_integration(db)

            # Get client ID from tax return
            client_id = tax_return.client_id if tax_return else None

            rag_context = await rag_integration.get_categorization_context(
                description=transaction.description,
                other_party=transaction.other_party,
                amount=transaction.amount,
                client_id=client_id
            )

            # Check for high-confidence direct match
            if rag_context.get("direct_match"):
                match = rag_context["direct_match"]
                logger.info(f"RAG direct match for '{transaction.description}': {match['category']} (confidence: {match['confidence']:.2f})")

                # Record in trace
                if hasattr(trace, 'record_rag_match'):
                    trace.record_rag_match(
                        matched=True,
                        category=match["category"],
                        confidence=match["confidence"],
                        learning_title=match.get("title", "")
                    )

                result.category_code = match["category"]
                result.confidence = match["confidence"]
                result.categorization_source = "rag_learning"
                result.needs_review = False
                result.review_reason = f"Matched learning: {match.get('title', 'RAG match')}"
                result.categorization_trace = trace.to_dict()
                return await self._apply_tax_rules(db, result, tax_return)

            # If learnings found but no direct match, prepare context for Claude
            rag_prompt_addition = rag_integration.format_learnings_for_prompt(rag_context)

            # Record that RAG was checked
            if hasattr(trace, 'record_rag_match'):
                trace.record_rag_match(
                    matched=False,
                    learnings_found=len(rag_context.get("context_learnings", []))
                )

        except Exception as e:
            logger.warning(f"RAG lookup failed, continuing without: {e}")
            rag_prompt_addition = ""

        # At this point, use best available match or fall back to Claude/unknown
        best_match = self._get_best_match(yaml_match, exact_match, fuzzy_match)

        if best_match and best_match["confidence"] >= self.REVIEW_THRESHOLD:
            result.category_code = best_match["category"]
            result.confidence = best_match["confidence"]
            result.categorization_source = best_match.get("source", "combined")
            result.needs_review = True
            result.review_reason = best_match.get("review_reason", "Confidence below auto-accept threshold")
            result.categorization_trace = trace.to_dict()
            return await self._apply_tax_rules(db, result, tax_return)

        # Layer 6: Claude AI (for uncertain items)
        if use_claude:
            claude_result = await self._categorize_with_claude(
                transaction, tax_return, rag_prompt_addition
            )

            if claude_result:
                trace.record_claude_result(
                    claude_result["category"],
                    claude_result["confidence"],
                    claude_result.get("reasoning", "")
                )
                result.category_code = claude_result["category"]
                result.confidence = claude_result["confidence"]
                result.categorization_source = "claude"
                result.needs_review = claude_result.get("needs_review", True)
                result.review_reason = claude_result.get("review_reason")
                result.categorization_trace = trace.to_dict()
                return await self._apply_tax_rules(db, result, tax_return)

        # Layer 6: Flag for human review
        result.category_code = "unknown"
        result.confidence = 0.0
        result.categorization_source = "none"
        result.needs_review = True
        result.review_reason = "Could not categorize - requires manual review"
        result.transaction_type = txn_type
        result.categorization_trace = trace.to_dict()

        return result

    async def categorize_batch(
        self,
        db: AsyncSession,
        transactions: List[ExtractedTransaction],
        tax_return: TaxReturn,
        use_claude: bool = True,
        document_context: Optional[Dict[str, Any]] = None
    ) -> List[TransactionCreate]:
        """
        Categorize a batch of transactions efficiently.

        Args:
            db: Database session
            transactions: List of extracted transactions
            tax_return: Associated tax return
            use_claude: Whether to use Claude for uncertain items
            document_context: Cross-document context (loan accounts, client names)

        Returns:
            List of categorized TransactionCreate objects
        """
        results = []

        # First pass: Try to categorize without Claude (using patterns, rules, etc.)
        uncertain = []

        logger.info(f"Starting batch categorization of {len(transactions)} transactions")
        if document_context:
            logger.info(f"Using document context with {len(document_context.get('loan_accounts', []))} loan accounts")

        for i, txn in enumerate(transactions):
            result = await self.categorize_transaction(
                db, txn, tax_return, use_claude=False,  # Don't use Claude yet
                document_context=document_context
            )

            if result.category_code == "unknown" and use_claude:
                uncertain.append((txn, len(results)))
                results.append(result)  # Placeholder
            else:
                results.append(result)

        # Second pass: Batch process uncertain transactions with Claude
        if uncertain and use_claude:
            logger.info(f"Found {len(uncertain)} uncertain transactions, sending to Claude in batches")

            # Process in batches of 20-25 transactions
            BATCH_SIZE = 20
            uncertain_transactions = [txn for txn, _ in uncertain]

            all_claude_results = []
            for batch_start in range(0, len(uncertain_transactions), BATCH_SIZE):
                batch_end = min(batch_start + BATCH_SIZE, len(uncertain_transactions))
                batch = uncertain_transactions[batch_start:batch_end]

                logger.info(f"Processing Claude batch {batch_start//BATCH_SIZE + 1} ({len(batch)} transactions)")
                batch_results = await self._batch_categorize_with_claude(
                    batch, tax_return, document_context=document_context
                )
                all_claude_results.extend(batch_results)

            # Apply Claude results back to the main results list
            for (txn, idx), claude_result in zip(uncertain, all_claude_results):
                if claude_result and isinstance(claude_result, dict):
                    results[idx].category_code = claude_result.get("category", "unknown")
                    results[idx].confidence = claude_result.get("confidence", 0.0)
                    results[idx].categorization_source = "claude"
                    results[idx].needs_review = claude_result.get("needs_review", True)
                    results[idx].review_reason = claude_result.get("review_reason")
                    results[idx] = await self._apply_tax_rules(db, results[idx], tax_return)

        # Log summary statistics
        categorized = sum(1 for r in results if r.category_code != "unknown")
        logger.info(f"Batch categorization complete: {categorized}/{len(transactions)} categorized successfully")

        return results

    def _match_document_context(
        self,
        description: str,
        other_party: str,
        amount: float,
        document_context: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """
        Match transaction against cross-document context.

        This is the FIRST layer of categorization - it uses information from
        other documents (like loan account numbers) to identify transactions.

        Examples:
        - "AP#22200442 TO R R CHAND" + loan account 22200442 = principal_repayment
        - "Transfer to <loan_account>" = principal_repayment
        - "Interest" from loan statement = interest

        Args:
            description: Transaction description
            other_party: Transaction other party
            amount: Transaction amount
            document_context: Cross-document context with loan_accounts, client_names, etc.

        Returns:
            Match result with category and confidence, or None
        """
        if not description:
            return None

        desc_upper = description.upper()
        desc_clean = re.sub(r'[^A-Z0-9]', '', desc_upper)  # Remove non-alphanumeric for matching

        # Check for loan account matches
        loan_accounts = document_context.get("loan_accounts", [])
        for loan in loan_accounts:
            account_num = loan.get("account_number_clean", "")
            holder_name = (loan.get("holder_name") or "").upper()

            if not account_num:
                continue

            # Check if loan account number appears in description
            if account_num in desc_clean:
                # Determine if it's a payment TO the loan (principal) or FROM (drawdown)
                is_outgoing = amount < 0  # Negative = money leaving bank account

                # Check for keywords that indicate direction
                is_transfer_to = any(kw in desc_upper for kw in [" TO ", "AP TO", "AP#", "PAYMENT TO", "TFR TO"])
                is_transfer_from = any(kw in desc_upper for kw in [" FROM ", "TFR FROM", "TRANSFER FROM"])

                if is_outgoing or is_transfer_to:
                    # Payment TO loan account = Principal repayment
                    return {
                        "category": "principal_repayment",
                        "confidence": 0.95,
                        "source": "document_context",
                        "reason": f"Matched loan account {loan.get('account_number')} - payment to loan is principal repayment"
                    }
                elif is_transfer_from:
                    # Transfer FROM loan = Drawdown (excluded)
                    return {
                        "category": "transfer",
                        "confidence": 0.95,
                        "source": "document_context",
                        "reason": f"Matched loan account {loan.get('account_number')} - transfer from loan is drawdown"
                    }

            # Check if holder name appears in description with transfer keywords
            if holder_name and len(holder_name) > 3:
                # Normalize holder name for matching
                holder_parts = holder_name.split()
                if any(part in desc_upper for part in holder_parts if len(part) > 2):
                    # Check for loan-related keywords + holder name
                    loan_keywords = ["AP#", "AP ", "LOAN", "MORTGAGE", "REPAYMENT"]
                    if any(kw in desc_upper for kw in loan_keywords):
                        is_outgoing = amount < 0
                        if is_outgoing:
                            return {
                                "category": "principal_repayment",
                                "confidence": 0.90,
                                "source": "document_context",
                                "reason": f"Matched loan holder '{holder_name}' with loan keywords - principal repayment"
                            }

        # Check for owner contributions/drawings based on client names
        client_names = document_context.get("client_names", [])
        for client_name in client_names:
            if not client_name or len(client_name) < 3:
                continue

            # Check if client name appears in description
            name_parts = client_name.split()
            if any(part in desc_upper for part in name_parts if len(part) > 2):
                # Check for transfer keywords
                is_transfer_to = any(kw in desc_upper for kw in [" TO ", "TFR TO", "TRANSFER TO"])
                is_transfer_from = any(kw in desc_upper for kw in [" FROM ", "TFR FROM", "TRANSFER FROM"])

                # Only categorize if NOT already matched as loan
                # Check if this might be a loan-related transfer (already handled above)
                is_loan_related = any(
                    loan.get("account_number_clean", "") in desc_clean
                    for loan in loan_accounts
                )

                if not is_loan_related:
                    if is_transfer_from and amount > 0:
                        # Transfer FROM owner = Funds Introduced (capital contribution)
                        return {
                            "category": "funds_introduced",
                            "confidence": 0.85,
                            "source": "document_context",
                            "reason": f"Transfer from owner '{client_name}' - funds introduced"
                        }
                    elif is_transfer_to and amount < 0:
                        # Transfer TO owner = Internal transfer (drawings)
                        return {
                            "category": "transfer",
                            "confidence": 0.85,
                            "source": "document_context",
                            "reason": f"Transfer to owner '{client_name}' - owner drawings/transfer"
                        }

        return None

    def _match_yaml_patterns(
        self,
        description: str,
        other_party: str = None,
        amount: float = None,
        transaction_type: str = None
    ) -> Optional[Dict[str, Any]]:
        """Match against YAML-defined patterns."""
        return self.pattern_matcher.match(
            description=description,
            other_party=other_party,
            amount=amount,
            transaction_type=transaction_type
        )

    async def _match_learned_exact(
        self,
        db: AsyncSession,
        description: str,
        other_party: str = None
    ) -> Optional[Dict[str, Any]]:
        """Match against learned patterns (exact match)."""
        # Normalize description
        normalized = self._normalize_description(description)

        # Query for exact match
        query = select(TransactionPattern).where(
            TransactionPattern.description_normalized == normalized,
            TransactionPattern.confidence >= 0.5  # Minimum learned confidence
        ).order_by(TransactionPattern.confidence.desc())

        result = await db.execute(query)
        pattern = result.scalar_one_or_none()

        if pattern:
            # Update usage stats
            pattern.times_applied += 1
            pattern.last_used_at = datetime.now(timezone.utc)
            await db.commit()

            return {
                "category": pattern.category_code,
                "confidence": pattern.confidence,
                "source": "learned_exact",
                "pattern_id": str(pattern.id),
                "matched_description": pattern.description_normalized
            }

        return None

    async def _match_learned_fuzzy(
        self,
        db: AsyncSession,
        description: str
    ) -> Optional[Dict[str, Any]]:
        """Match against learned patterns using pg_trgm fuzzy matching."""
        normalized = self._normalize_description(description)

        # Use PostgreSQL's pg_trgm similarity function
        # Note: pg_trgm extension must be enabled
        try:
            query = text("""
                SELECT
                    id,
                    description_normalized,
                    category_code,
                    confidence,
                    similarity(description_normalized, :description) as sim
                FROM transaction_patterns
                WHERE similarity(description_normalized, :description) > :threshold
                ORDER BY sim DESC
                LIMIT 1
            """)

            result = await db.execute(
                query,
                {
                    "description": normalized,
                    "threshold": self.FUZZY_SIMILARITY_THRESHOLD
                }
            )
            row = result.fetchone()

            if row:
                # Update usage stats
                update_query = text("""
                    UPDATE transaction_patterns
                    SET times_applied = times_applied + 1,
                        last_used_at = NOW()
                    WHERE id = :id
                """)
                await db.execute(update_query, {"id": row.id})
                await db.commit()

                return {
                    "category": row.category_code,
                    "confidence": float(row.confidence) * float(row.sim),
                    "source": "learned_fuzzy",
                    "pattern_id": str(row.id),
                    "matched_description": row.description_normalized,
                    "similarity": float(row.sim)
                }

        except Exception as e:
            # pg_trgm might not be available
            logger.warning(f"Fuzzy matching failed (pg_trgm may not be enabled): {e}")

        return None

    def _get_best_match(self, *matches) -> Optional[Dict[str, Any]]:
        """Get the best match from multiple sources."""
        valid_matches = [m for m in matches if m is not None]

        if not valid_matches:
            return None

        return max(valid_matches, key=lambda m: m.get("confidence", 0))

    async def _categorize_with_claude(
        self,
        transaction: ExtractedTransaction,
        tax_return: TaxReturn,
        rag_context: str = ""
    ) -> Optional[Dict[str, Any]]:
        """Use Claude to categorize a single uncertain transaction."""
        results = await self._batch_categorize_with_claude([transaction], tax_return, [rag_context] if rag_context else [])
        return results[0] if results else None

    async def _batch_categorize_with_claude(
        self,
        transactions: List[ExtractedTransaction],
        tax_return: TaxReturn,
        rag_contexts: Optional[List[str]] = None,
        document_context: Optional[Dict[str, Any]] = None
    ) -> List[Optional[Dict[str, Any]]]:
        """Use Claude to categorize a batch of uncertain transactions."""
        if not transactions:
            return []

        # Build context
        context = {
            "property_address": tax_return.property_address,
            "tax_year": tax_return.tax_year,
            "property_type": tax_return.property_type.value
        }

        # Get domain knowledge
        domain_context = self.skill_loader.get_domain_context()

        # Build document context section for prompt
        doc_context_section = ""
        if document_context:
            loan_accounts = document_context.get("loan_accounts", [])
            client_names = document_context.get("client_names", [])

            if loan_accounts or client_names:
                doc_context_section = "\n## Cross-Document Context\n\n"
                doc_context_section += "Use this information from other documents to help categorize:\n\n"

                if loan_accounts:
                    doc_context_section += "**Loan Accounts (from loan statements):**\n"
                    for loan in loan_accounts:
                        doc_context_section += f"- Account: {loan.get('account_number', 'N/A')}"
                        if loan.get('lender'):
                            doc_context_section += f" (Lender: {loan['lender']})"
                        if loan.get('holder_name'):
                            doc_context_section += f" - Holder: {loan['holder_name']}"
                        doc_context_section += "\n"
                    doc_context_section += "\n**IMPORTANT:** Transfers TO these loan accounts are principal repayments (NOT deductible).\n\n"

                if client_names:
                    doc_context_section += "**Property Owner Names:**\n"
                    for name in client_names:
                        doc_context_section += f"- {name}\n"
                    doc_context_section += "\n**IMPORTANT:** Transfers FROM owner = funds_introduced, TO owner = transfer.\n\n"

        # Build prompt
        prompt = f"""{domain_context}
{doc_context_section}
## Transaction Categorization Task

Categorize the following {len(transactions)} transactions for a New Zealand rental property tax return.

Property: {context['property_address']}
Tax Year: {context['tax_year']}
Property Type: {context['property_type']}

## Available Categories

**Income:**
- rental_income: Rent payments
- water_rates_recovered: Water reimbursements from tenant
- bank_contribution: Cashbacks, rebates
- insurance_payout: Insurance claims
- other_income: Other income

**Expenses:**
- interest: Loan interest (from bank statement)
- rates: Council rates
- water_rates: Water charges
- body_corporate: BC levies (operating fund only)
- insurance: Landlord insurance
- agent_fees: Property management fees
- repairs_maintenance: Repairs and maintenance
- bank_fees: Account fees
- legal_fees: Legal costs
- advertising: Tenant finding
- listing_fees: Letting fees
- gardening: Lawn/garden maintenance
- electricity, gas: Utilities if landlord pays
- depreciation: Chattels depreciation
- consulting_accounting: Accounting fees
- due_diligence: LIM, inspections, reports
- meth_testing, smoke_alarms, healthy_homes: Compliance
- pest_control, security, rubbish_collection: Services

**Excluded (not on P&L):**
- bond: Tenant bond (NOT income)
- transfer: Internal transfers (including owner drawings)
- principal_repayment: Loan principal (payments TO loan account)
- capital_expense: Capital improvements
- personal: Non-property related
- funds_introduced: Money transferred IN from owner (capital contribution)

**Unknown:**
- unknown: Cannot determine

## Transactions to Categorize

"""
        for i, txn in enumerate(transactions, 1):
            txn_type = "CREDIT" if txn.amount > 0 else "DEBIT"
            prompt += f"""
{i}. Date: {txn.transaction_date}
   Description: {txn.description}
   Other Party: {txn.other_party or 'N/A'}
   Amount: ${abs(txn.amount):.2f} ({txn_type})
"""

        # Add RAG learnings to prompt if available
        if rag_contexts and len(rag_contexts) > 0 and rag_contexts[0]:
            # For batch processing, we only use the first context
            # In a more sophisticated implementation, you'd match contexts to transactions
            prompt += "\n" + rag_contexts[0] + "\n"

        prompt += """

## Response Format

Return a JSON array with one object per transaction:
```json
[
  {
    "index": 1,
    "category": "category_code",
    "confidence": 0.85,
    "reasoning": "Brief explanation",
    "needs_review": false,
    "review_reason": null
  }
]
```

IMPORTANT:
- Bond payments are NOT income
- Interest must be from bank statement debits
- If truly uncertain, use "unknown" with needs_review: true
"""

        try:
            # Log API call
            logger.info(f"Sending batch of {len(transactions)} transactions to Claude API")
            start_time = datetime.now(timezone.utc)

            response = await self.claude_client.client.messages.create(
                model=self.claude_client.model,
                max_tokens=4096,
                temperature=0.1,
                messages=[{"role": "user", "content": prompt}]
            )

            # Log response time
            elapsed = (datetime.now(timezone.utc) - start_time).total_seconds()
            logger.info(f"Claude API response received in {elapsed:.2f} seconds")

            response_text = response.content[0].text

            # Parse JSON
            if "```json" in response_text:
                response_text = response_text.split("```json")[1].split("```")[0].strip()
            elif "```" in response_text:
                response_text = response_text.split("```")[1].split("```")[0].strip()

            import json
            results = json.loads(response_text)

            # Map back to transactions
            result_map = {r["index"]: r for r in results}

            return [
                result_map.get(i + 1, {
                    "category": "unknown",
                    "confidence": 0.0,
                    "needs_review": True,
                    "review_reason": "Claude did not return result"
                })
                for i in range(len(transactions))
            ]

        except Exception as e:
            logger.error(f"Claude batch categorization failed: {e}")
            return [None] * len(transactions)

    async def _apply_tax_rules(
        self,
        db: AsyncSession,
        transaction: TransactionCreate,
        tax_return: TaxReturn
    ) -> TransactionCreate:
        """Apply tax rules to determine deductibility."""
        # Get P&L mapping for category
        mapping_result = await db.execute(
            select(PLRowMapping).where(
                PLRowMapping.category_code == transaction.category_code
            )
        )
        mapping = mapping_result.scalar_one_or_none()

        if mapping:
            transaction.transaction_type = mapping.transaction_type
            transaction.is_deductible = mapping.is_deductible
        else:
            # Default based on amount
            transaction.transaction_type = "income" if transaction.amount > 0 else "expense"
            transaction.is_deductible = transaction.transaction_type == "expense"

        # Apply interest deductibility rules
        if transaction.category_code == "interest" and transaction.is_deductible:
            # Handle property_type as either enum or string
            property_type_value = (
                tax_return.property_type.value
                if hasattr(tax_return.property_type, 'value')
                else tax_return.property_type
            )

            rule_result = await db.execute(
                select(TaxRule).where(
                    TaxRule.rule_type == "interest_deductibility",
                    TaxRule.tax_year == tax_return.tax_year,
                    TaxRule.property_type == property_type_value
                )
            )
            rule = rule_result.scalar_one_or_none()

            if rule:
                percentage = rule.value.get("percentage", 100)
                transaction.deductible_percentage = float(percentage)
                # deductible_amount is calculated in the database model, not set here
            else:
                transaction.deductible_percentage = 100.0
                # deductible_amount is calculated in the database model
        elif transaction.is_deductible:
            transaction.deductible_percentage = 100.0
            # deductible_amount is calculated in the database model
        else:
            transaction.deductible_percentage = 0.0
            # deductible_amount is calculated in the database model

        return transaction

    def _normalize_description(self, description: str) -> str:
        """Normalize description for matching."""
        if not description:
            return ""

        # Lowercase
        normalized = description.lower()

        # Remove extra whitespace
        normalized = " ".join(normalized.split())

        # Remove common noise words/patterns
        noise_patterns = [
            r'\b\d{2}/\d{2}/\d{2,4}\b',  # Dates
            r'\bref[:\s]*\w+\b',          # Reference numbers
            r'\b\d{6,}\b',                # Long numbers
            r'[#*]+',                      # Special characters
        ]

        for pattern in noise_patterns:
            normalized = re.sub(pattern, '', normalized)

        # Clean up
        normalized = " ".join(normalized.split())

        return normalized.strip()

    async def learn_from_correction(
        self,
        db: AsyncSession,
        transaction_id: UUID,
        corrected_category: str,
        corrected_by: str = None,
        notes: str = None,
        create_pattern: bool = True
    ) -> Optional[UUID]:
        """
        Learn from a user correction.

        Args:
            db: Database session
            transaction_id: ID of corrected transaction
            corrected_category: The correct category
            corrected_by: User who made correction
            notes: Optional notes
            create_pattern: Whether to create/update a pattern

        Returns:
            Pattern ID if created/updated, None otherwise
        """
        # Get the transaction
        txn_result = await db.execute(
            select(Transaction).where(Transaction.id == transaction_id)
        )
        transaction = txn_result.scalar_one_or_none()

        if not transaction:
            logger.warning(f"Transaction not found: {transaction_id}")
            return None

        original_category = transaction.category_code

        # Record the feedback
        feedback = CategoryFeedback(
            transaction_id=transaction_id,
            original_category=original_category,
            corrected_category=corrected_category,
            corrected_by=corrected_by,
            notes=notes
        )
        db.add(feedback)

        # Update the transaction
        transaction.category_code = corrected_category
        transaction.manually_reviewed = True
        transaction.reviewed_by = corrected_by
        transaction.reviewed_at = datetime.now(timezone.utc)
        transaction.needs_review = False

        pattern_id = None

        # Create or update pattern
        if create_pattern:
            normalized = self._normalize_description(transaction.description)

            # Check if pattern exists
            pattern_result = await db.execute(
                select(TransactionPattern).where(
                    TransactionPattern.description_normalized == normalized
                )
            )
            existing_pattern = pattern_result.scalar_one_or_none()

            if existing_pattern:
                # Update existing pattern
                if existing_pattern.category_code == corrected_category:
                    existing_pattern.times_confirmed += 1
                    # Boost confidence
                    existing_pattern.confidence = min(
                        0.99,
                        existing_pattern.confidence + 0.05
                    )
                else:
                    existing_pattern.times_corrected += 1
                    # If corrected too many times, update category
                    if existing_pattern.times_corrected > existing_pattern.times_confirmed:
                        existing_pattern.category_code = corrected_category
                        existing_pattern.confidence = 0.70  # Reset confidence
                        existing_pattern.times_confirmed = 1
                        existing_pattern.times_corrected = 0

                pattern_id = existing_pattern.id
            else:
                # Create new pattern
                new_pattern = TransactionPattern(
                    description_normalized=normalized,
                    other_party_normalized=self._normalize_description(
                        transaction.other_party
                    ) if transaction.other_party else None,
                    category_code=corrected_category,
                    confidence=0.85,  # Start with good confidence
                    times_applied=0,
                    times_confirmed=1,
                    times_corrected=0,
                    is_global=True,  # Could be client-specific based on logic
                    source="user_correction"
                )
                db.add(new_pattern)
                await db.flush()  # Get the ID
                pattern_id = new_pattern.id

            feedback.pattern_created = True
            feedback.pattern_id = pattern_id

        await db.commit()

        logger.info(
            f"Learned from correction: {original_category} -> {corrected_category} "
            f"for '{transaction.description[:50]}...'"
        )

        return pattern_id

    async def generate_summaries(
        self,
        db: AsyncSession,
        tax_return_id: UUID
    ) -> List[TransactionSummary]:
        """
        Generate category summaries for a tax return.

        Args:
            db: Database session
            tax_return_id: Tax return to summarize

        Returns:
            List of TransactionSummary records
        """
        # Delete existing summaries
        await db.execute(
            text("DELETE FROM transaction_summaries WHERE tax_return_id = :id"),
            {"id": tax_return_id}
        )
        await db.flush()  # Ensure delete is executed before inserts

        # Aggregate by category
        query = text("""
            SELECT
                category_code,
                COUNT(*) as transaction_count,
                SUM(amount) as gross_amount,
                SUM(COALESCE(deductible_amount, 0)) as deductible_amount,
                SUM(COALESCE(gst_amount, 0)) as gst_amount
            FROM transactions
            WHERE tax_return_id = :tax_return_id
            AND category_code IS NOT NULL
            GROUP BY category_code
        """)

        result = await db.execute(query, {"tax_return_id": tax_return_id})
        rows = result.fetchall()

        summaries = []

        for row in rows:
            # Get monthly breakdown for interest
            monthly_breakdown = None
            if row.category_code == "interest":
                monthly_breakdown = await self._get_monthly_breakdown(
                    db, tax_return_id, row.category_code
                )

            summary = TransactionSummary(
                tax_return_id=tax_return_id,
                category_code=row.category_code,
                transaction_count=row.transaction_count,
                gross_amount=row.gross_amount or Decimal("0"),
                deductible_amount=row.deductible_amount or Decimal("0"),
                gst_amount=row.gst_amount,
                monthly_breakdown=monthly_breakdown
            )
            db.add(summary)
            summaries.append(summary)

        await db.commit()

        return summaries

    async def _get_monthly_breakdown(
        self,
        db: AsyncSession,
        tax_return_id: UUID,
        category_code: str
    ) -> Dict[str, float]:
        """Get monthly breakdown for a category (used for interest workings)."""
        query = text("""
            SELECT
                TO_CHAR(transaction_date, 'Mon-YY') as month,
                SUM(ABS(amount)) as total
            FROM transactions
            WHERE tax_return_id = :tax_return_id
            AND category_code = :category_code
            GROUP BY TO_CHAR(transaction_date, 'Mon-YY'),
                     DATE_TRUNC('month', transaction_date)
            ORDER BY DATE_TRUNC('month', transaction_date)
        """)

        result = await db.execute(
            query,
            {"tax_return_id": tax_return_id, "category_code": category_code}
        )

        return {row.month: float(row.total) for row in result.fetchall()}


# Singleton instance
_categorizer: Optional[TransactionCategorizer] = None


def get_transaction_categorizer() -> TransactionCategorizer:
    """Get or create the singleton categorizer."""
    global _categorizer

    if _categorizer is None:
        _categorizer = TransactionCategorizer()

    return _categorizer