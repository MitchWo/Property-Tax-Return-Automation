"""Tax rules service for retrieving and applying tax rules."""
import logging
from decimal import Decimal
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.db_models import PLRowMapping, TaxRule

logger = logging.getLogger(__name__)


class TaxRulesService:
    """Service for managing and applying tax rules."""

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

    async def apply_tax_rules(
        self,
        db: AsyncSession,
        transaction: Any,
        tax_return: Any
    ) -> Dict[str, Any]:
        """
        Apply tax rules to a transaction.

        Args:
            db: Database session
            transaction: Transaction to apply rules to
            tax_return: Associated tax return

        Returns:
            Dictionary with tax rule results
        """
        result = {}

        # Apply interest deductibility if applicable
        if hasattr(transaction, 'category_code') and transaction.category_code == 'interest':
            deductibility = await self.get_interest_deductibility(
                db,
                tax_return.tax_year,
                tax_return.property_type.value if hasattr(tax_return.property_type, 'value') else tax_return.property_type
            )
            result['deductible_percentage'] = deductibility

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