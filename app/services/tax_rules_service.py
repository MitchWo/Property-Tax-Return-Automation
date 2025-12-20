"""Tax rules service for retrieving and applying tax rules.

This service combines:
1. Database TaxRule records (deterministic rules)
2. RAG tax-rules namespace (learned/contextual rules from Pinecone)

The RAG tax-rules namespace provides additional context for tax treatment
decisions, especially for edge cases or client-specific interpretations.
"""
import logging
from decimal import Decimal
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.db_models import PLRowMapping, TaxRule
from app.services.phase2_feedback_learning.knowledge_store import knowledge_store

logger = logging.getLogger(__name__)


class TaxRulesService:
    """Service for managing and applying tax rules."""

    def __init__(self):
        # Cache for RAG tax context to avoid repeated API calls for same category
        self._rag_cache: Dict[str, Dict[str, Any]] = {}

    def _get_cache_key(self, category_code: str, property_type: str, tax_year: str) -> str:
        return f"{category_code}:{property_type}:{tax_year}"

    async def get_interest_deductibility(
        self,
        db: AsyncSession,
        tax_year: str,
        property_type: str
    ) -> float:
        """
        Get interest deductibility percentage for a property.

        Args:
            db: Database session
            tax_year: Tax year (e.g., "FY25")
            property_type: "new_build" or "existing"

        Returns:
            Deductibility percentage (0-100)
        """
        result = await db.execute(
            select(TaxRule).where(
                TaxRule.rule_type == "interest_deductibility",
                TaxRule.tax_year == tax_year,
                TaxRule.property_type == property_type
            )
        )
        rule = result.scalar_one_or_none()

        if rule:
            return rule.value.get("percentage", 100)

        # Default to 100% if no rule found
        logger.warning(
            f"No interest deductibility rule found for {tax_year}/{property_type}, "
            "defaulting to 100%"
        )
        return 100.0

    async def get_accounting_fee(
        self,
        db: AsyncSession,
        tax_year: str = "all"
    ) -> Decimal:
        """
        Get standard accounting fee.

        Args:
            db: Database session
            tax_year: Tax year or "all" for default

        Returns:
            Accounting fee amount
        """
        result = await db.execute(
            select(TaxRule).where(
                TaxRule.rule_type == "accounting_fee",
                TaxRule.tax_year.in_([tax_year, "all"])
            )
        )
        rule = result.scalar_one_or_none()

        if rule:
            return Decimal(str(rule.value.get("amount", 862.50)))

        return Decimal("862.50")  # Default

    async def get_gst_rate(self, db: AsyncSession) -> float:
        """Get current GST rate."""
        result = await db.execute(
            select(TaxRule).where(
                TaxRule.rule_type == "gst_rate",
                TaxRule.tax_year == "all"
            )
        )
        rule = result.scalar_one_or_none()

        if rule:
            return rule.value.get("percentage", 15)

        return 15.0  # NZ GST rate

    async def get_pl_row_mapping(
        self,
        db: AsyncSession,
        category_code: str
    ) -> Optional[PLRowMapping]:
        """
        Get P&L row mapping for a category.

        Args:
            db: Database session
            category_code: Category code

        Returns:
            PLRowMapping or None
        """
        result = await db.execute(
            select(PLRowMapping).where(
                PLRowMapping.category_code == category_code
            )
        )
        return result.scalar_one_or_none()

    async def get_all_pl_mappings(
        self,
        db: AsyncSession
    ) -> List[PLRowMapping]:
        """Get all P&L row mappings ordered by sort_order."""
        result = await db.execute(
            select(PLRowMapping).order_by(PLRowMapping.sort_order)
        )
        return list(result.scalars().all())

    async def get_category_by_pl_row(
        self,
        db: AsyncSession,
        pl_row: int
    ) -> Optional[PLRowMapping]:
        """Get category mapping by P&L row number."""
        result = await db.execute(
            select(PLRowMapping).where(PLRowMapping.pl_row == pl_row)
        )
        return result.scalar_one_or_none()

    async def calculate_depreciation_prorate(
        self,
        full_year_amount: Decimal,
        months_owned: int
    ) -> Decimal:
        """
        Calculate pro-rated depreciation for partial year.

        Args:
            full_year_amount: Full year depreciation amount
            months_owned: Number of months property owned in tax year

        Returns:
            Pro-rated depreciation amount
        """
        if months_owned >= 12:
            return full_year_amount

        return full_year_amount * Decimal(str(months_owned)) / Decimal("12")

    async def get_rules_for_return(
        self,
        tax_year: str,
        property_type: str,
        db: AsyncSession
    ) -> Dict[str, Any]:
        """
        Get all applicable rules for a tax return.

        Args:
            tax_year: Tax year
            property_type: Property type
            db: Database session

        Returns:
            Dictionary of all applicable rules
        """
        return await self.get_all_rules_for_return(db, tax_year, property_type)

    async def get_all_rules_for_return(
        self,
        db: AsyncSession,
        tax_year: str,
        property_type: str
    ) -> Dict[str, Any]:
        """
        Get all applicable rules for a tax return.

        Args:
            db: Database session
            tax_year: Tax year
            property_type: Property type

        Returns:
            Dictionary of all applicable rules
        """
        # Get all rules that apply
        result = await db.execute(
            select(TaxRule).where(
                TaxRule.tax_year.in_([tax_year, "all"]),
                TaxRule.property_type.in_([property_type, "all"])
            )
        )
        rules = result.scalars().all()

        # Organize by rule type
        rules_dict = {}
        for rule in rules:
            # More specific rules override general ones
            key = rule.rule_type
            if key not in rules_dict:
                rules_dict[key] = rule.value
            elif rule.tax_year != "all" or rule.property_type != "all":
                # More specific rule, override
                rules_dict[key] = rule.value

        return rules_dict

    async def get_rag_tax_context(
        self,
        category_code: str,
        property_type: str,
        tax_year: str,
        description: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Query RAG tax-rules and gst-rules namespaces for tax treatment context.

        Searches across:
        - tax-rules: Tax deductibility and treatment
        - gst-rules: GST treatment and rules
        - skill_learnings: Domain knowledge

        Args:
            category_code: Transaction category code
            property_type: Property type (new_build, existing)
            tax_year: Tax year (e.g., "FY25")
            description: Optional transaction description for context

        Returns:
            Dictionary with RAG-sourced tax guidance
        """
        if not knowledge_store.enabled:
            return {}

        # Check cache first to avoid repeated API calls
        cache_key = self._get_cache_key(category_code, property_type, tax_year)
        if cache_key in self._rag_cache:
            return self._rag_cache[cache_key]

        try:
            # Search tax-rules namespace for relevant guidance (includes gst-rules)
            results = await knowledge_store.search_tax_rules(
                query=f"{category_code} deductibility treatment {property_type}",
                property_type=property_type,
                tax_year=tax_year,
                category=category_code,
                top_k=5
            )

            # Also search gst-rules specifically for GST treatment
            gst_results = await knowledge_store.search_gst_rules(
                query=f"{category_code} GST treatment",
                category=category_code,
                top_k=3
            )
            results.extend(gst_results)

            if not results:
                return {}

            # Parse results for applicable rules
            rag_context = {
                "has_rag_guidance": True,
                "rules_found": len(results),
                "guidance": []
            }

            for result in results:
                content = result.get("content", "")
                score = result.get("score", 0)
                source = result.get("source_namespace", "tax-rules")

                # Look for specific percentage mentions
                import re
                percentage_match = re.search(r'(\d+(?:\.\d+)?)\s*%', content)

                guidance_entry = {
                    "content": content[:300],
                    "score": score,
                    "source": source
                }

                if percentage_match:
                    guidance_entry["suggested_percentage"] = float(percentage_match.group(1))

                rag_context["guidance"].append(guidance_entry)

            logger.info(f"RAG tax context for {category_code}: {len(results)} rules found")

            # Cache the result for future lookups
            self._rag_cache[cache_key] = rag_context
            return rag_context

        except Exception as e:
            logger.warning(f"Failed to get RAG tax context: {e}")
            return {}

    async def apply_tax_rules(
        self,
        db: AsyncSession,
        transaction: Any,
        tax_return: Any
    ) -> Dict[str, Any]:
        """
        Apply tax rules to a transaction.

        Combines:
        1. Database TaxRule records (deterministic, authoritative)
        2. RAG tax-rules namespace (contextual, learned rules)

        Args:
            db: Database session
            transaction: Transaction to apply rules to
            tax_return: Associated tax return

        Returns:
            Dictionary with tax rule results
        """
        result = {}
        property_type = tax_return.property_type.value if hasattr(tax_return.property_type, 'value') else tax_return.property_type
        category_code = getattr(transaction, 'category_code', None)
        rag_context = {}

        # Get RAG context for this transaction category
        if category_code:
            rag_context = await self.get_rag_tax_context(
                category_code=category_code,
                property_type=property_type,
                tax_year=tax_return.tax_year,
                description=getattr(transaction, 'description', None)
            )

            if rag_context.get("has_rag_guidance"):
                result["rag_tax_context"] = rag_context
                logger.debug(f"Applied RAG tax context for {category_code}")

        # Apply interest deductibility if applicable
        if category_code == 'interest':
            deductibility = await self.get_interest_deductibility(
                db,
                tax_return.tax_year,
                property_type
            )
            result['deductible_percentage'] = deductibility

            # Check if RAG has different guidance (for logging/auditing)
            for guidance in rag_context.get("guidance", []):
                if "suggested_percentage" in guidance:
                    if guidance["suggested_percentage"] != deductibility:
                        logger.info(
                            f"RAG suggests {guidance['suggested_percentage']}% vs DB rule {deductibility}% "
                            f"for interest deductibility - using DB rule"
                        )

        # Apply GST rules if needed
        if hasattr(transaction, 'gst_inclusive'):
            result['gst_inclusive'] = transaction.gst_inclusive

        return result


# Singleton instance
_tax_rules_service: Optional[TaxRulesService] = None


def get_tax_rules_service() -> TaxRulesService:
    """Get or create the singleton tax rules service."""
    global _tax_rules_service

    if _tax_rules_service is None:
        _tax_rules_service = TaxRulesService()

    return _tax_rules_service