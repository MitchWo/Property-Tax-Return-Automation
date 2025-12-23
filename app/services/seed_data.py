"""Seed data for tax rules and P&L row mappings."""
import asyncio
import logging
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import AsyncSessionLocal
from app.models.db_models import PLRowMapping, TaxRule

logger = logging.getLogger(__name__)


# =============================================================================
# TAX RULES DATA
# =============================================================================

TAX_RULES = [
    # Interest Deductibility Rules
    {
        "rule_type": "interest_deductibility",
        "tax_year": "FY24",
        "property_type": "new_build",
        "value": {"percentage": 100},
        "notes": "New builds (CCC after 27 March 2020) - 100% deductible"
    },
    {
        "rule_type": "interest_deductibility",
        "tax_year": "FY24",
        "property_type": "existing",
        "value": {"percentage": 50},
        "notes": "Existing properties FY24 - 50% deductible"
    },
    {
        "rule_type": "interest_deductibility",
        "tax_year": "FY25",
        "property_type": "new_build",
        "value": {"percentage": 100},
        "notes": "New builds (CCC after 27 March 2020) - 100% deductible"
    },
    {
        "rule_type": "interest_deductibility",
        "tax_year": "FY25",
        "property_type": "existing",
        "value": {"percentage": 80},
        "notes": "Existing properties FY25 - 80% deductible"
    },
    {
        "rule_type": "interest_deductibility",
        "tax_year": "FY26",
        "property_type": "new_build",
        "value": {"percentage": 100},
        "notes": "New builds (CCC after 27 March 2020) - 100% deductible"
    },
    {
        "rule_type": "interest_deductibility",
        "tax_year": "FY26",
        "property_type": "existing",
        "value": {"percentage": 100},
        "notes": "Existing properties FY26 - 100% deductible (restored)"
    },

    # Standard Fees
    {
        "rule_type": "accounting_fee",
        "tax_year": "all",
        "property_type": "all",
        "value": {"amount": 862.50, "gst_inclusive": True},
        "notes": "Standard Lighthouse Financial fee per property"
    },

    # IRD Mileage Rates (if needed for property inspections)
    {
        "rule_type": "ird_mileage_rate",
        "tax_year": "FY24",
        "property_type": "all",
        "value": {"cents_per_km": 95},
        "notes": "IRD tier 1 mileage rate FY24"
    },
    {
        "rule_type": "ird_mileage_rate",
        "tax_year": "FY25",
        "property_type": "all",
        "value": {"cents_per_km": 99},
        "notes": "IRD tier 1 mileage rate FY25"
    },

    # GST Rate
    {
        "rule_type": "gst_rate",
        "tax_year": "all",
        "property_type": "all",
        "value": {"percentage": 15},
        "notes": "NZ GST rate"
    },

    # Depreciation - Chattels threshold
    {
        "rule_type": "low_value_asset_threshold",
        "tax_year": "FY24",
        "property_type": "all",
        "value": {"amount": 1000},
        "notes": "Assets under $1000 can be fully expensed"
    },
    {
        "rule_type": "low_value_asset_threshold",
        "tax_year": "FY25",
        "property_type": "all",
        "value": {"amount": 1000},
        "notes": "Assets under $1000 can be fully expensed"
    },
]


# =============================================================================
# P&L ROW MAPPINGS
# Based on IR3R workbook template structure
# Categories are grouped for consistent UI display across the app
# =============================================================================

# Category group ordering for UI display
CATEGORY_GROUP_ORDER = [
    "Income",
    "Interest & Finance",
    "Rates & Levies",
    "Insurance",
    "Repairs & Maintenance",
    "Professional Services",
    "Advertising & Admin",
    "Utilities",
    "Travel",
    "Other",
    "Excluded",
]

PL_ROW_MAPPINGS = [
    # =========================================================================
    # INCOME (rows 6-11)
    # =========================================================================
    {
        "category_code": "rental_income",
        "pl_row": 6,
        "display_name": "Rental Income",
        "category_group": "Income",
        "transaction_type": "income",
        "is_deductible": False,
        "default_source": "BS/PM",
        "sort_order": 1
    },
    {
        "category_code": "water_rates_recovered",
        "pl_row": 7,
        "display_name": "Water Rates Recovered",
        "category_group": "Income",
        "transaction_type": "income",
        "is_deductible": False,
        "default_source": "BS",
        "sort_order": 2
    },
    {
        "category_code": "bank_contribution",
        "pl_row": 8,
        "display_name": "Bank Contribution / Cashback",
        "category_group": "Income",
        "transaction_type": "income",
        "is_deductible": False,
        "default_source": "BS",
        "sort_order": 3
    },
    {
        "category_code": "insurance_payout",
        "pl_row": 9,
        "display_name": "Insurance Payout",
        "category_group": "Income",
        "transaction_type": "income",
        "is_deductible": False,
        "default_source": "BS",
        "sort_order": 4
    },
    {
        "category_code": "other_income",
        "pl_row": 10,
        "display_name": "Other Income",
        "category_group": "Income",
        "transaction_type": "income",
        "is_deductible": False,
        "default_source": "BS",
        "sort_order": 5
    },

    # =========================================================================
    # INTEREST & FINANCE
    # =========================================================================
    {
        "category_code": "interest",
        "pl_row": 26,
        "display_name": "Mortgage Interest",
        "category_group": "Interest & Finance",
        "transaction_type": "expense",
        "is_deductible": True,
        "default_source": "BS",
        "sort_order": 20
    },
    {
        "category_code": "hire_purchase",
        "pl_row": 24,
        "display_name": "Hire Purchase Interest",
        "category_group": "Interest & Finance",
        "transaction_type": "expense",
        "is_deductible": True,
        "default_source": "INV",
        "sort_order": 21
    },
    {
        "category_code": "mortgage_admin",
        "pl_row": 31,
        "display_name": "Mortgage Admin / Break Fee",
        "category_group": "Interest & Finance",
        "transaction_type": "expense",
        "is_deductible": True,
        "default_source": "BS",
        "sort_order": 22
    },
    {
        "category_code": "bank_fees",
        "pl_row": 15,
        "display_name": "Bank Fees",
        "category_group": "Interest & Finance",
        "transaction_type": "expense",
        "is_deductible": True,
        "default_source": "BS",
        "sort_order": 23
    },

    # =========================================================================
    # RATES & LEVIES
    # =========================================================================
    {
        "category_code": "rates",
        "pl_row": 34,
        "display_name": "Council Rates",
        "category_group": "Rates & Levies",
        "transaction_type": "expense",
        "is_deductible": True,
        "default_source": "BS/SS",
        "sort_order": 30
    },
    {
        "category_code": "water_rates",
        "pl_row": 41,
        "display_name": "Water Rates",
        "category_group": "Rates & Levies",
        "transaction_type": "expense",
        "is_deductible": True,
        "default_source": "BS",
        "sort_order": 31
    },
    {
        "category_code": "body_corporate",
        "pl_row": 16,
        "display_name": "Body Corporate Levies",
        "category_group": "Rates & Levies",
        "transaction_type": "expense",
        "is_deductible": True,
        "default_source": "BS/SS",
        "sort_order": 32
    },
    {
        "category_code": "resident_society",
        "pl_row": 36,
        "display_name": "Resident Society Levies",
        "category_group": "Rates & Levies",
        "transaction_type": "expense",
        "is_deductible": True,
        "default_source": "SS/INV",
        "sort_order": 33
    },

    # =========================================================================
    # INSURANCE
    # =========================================================================
    {
        "category_code": "insurance",
        "pl_row": 25,
        "display_name": "Landlord Insurance",
        "category_group": "Insurance",
        "transaction_type": "expense",
        "is_deductible": True,
        "default_source": "INV",
        "sort_order": 40
    },

    # =========================================================================
    # REPAIRS & MAINTENANCE
    # =========================================================================
    {
        "category_code": "repairs_maintenance",
        "pl_row": 35,
        "display_name": "Repairs & Maintenance",
        "category_group": "Repairs & Maintenance",
        "transaction_type": "expense",
        "is_deductible": True,
        "default_source": "BS/PM",
        "sort_order": 50
    },
    {
        "category_code": "gardening",
        "pl_row": 22,
        "display_name": "Gardening / Lawns",
        "category_group": "Repairs & Maintenance",
        "transaction_type": "expense",
        "is_deductible": True,
        "default_source": "BS/PM",
        "sort_order": 51
    },
    {
        "category_code": "pest_control",
        "pl_row": 32,
        "display_name": "Pest Control",
        "category_group": "Repairs & Maintenance",
        "transaction_type": "expense",
        "is_deductible": True,
        "default_source": "BS/PM",
        "sort_order": 52
    },
    {
        "category_code": "healthy_homes",
        "pl_row": 23,
        "display_name": "Healthy Homes Compliance",
        "category_group": "Repairs & Maintenance",
        "transaction_type": "expense",
        "is_deductible": True,
        "default_source": "INV",
        "sort_order": 53
    },
    {
        "category_code": "smoke_alarms",
        "pl_row": 39,
        "display_name": "Smoke Alarms",
        "category_group": "Repairs & Maintenance",
        "transaction_type": "expense",
        "is_deductible": True,
        "default_source": "INV",
        "sort_order": 54
    },
    {
        "category_code": "meth_testing",
        "pl_row": 29,
        "display_name": "Meth Testing",
        "category_group": "Repairs & Maintenance",
        "transaction_type": "expense",
        "is_deductible": True,
        "default_source": "INV",
        "sort_order": 55
    },
    {
        "category_code": "security",
        "pl_row": 38,
        "display_name": "Security",
        "category_group": "Repairs & Maintenance",
        "transaction_type": "expense",
        "is_deductible": True,
        "default_source": "BS",
        "sort_order": 56
    },
    {
        "category_code": "rubbish_collection",
        "pl_row": 37,
        "display_name": "Rubbish Collection",
        "category_group": "Repairs & Maintenance",
        "transaction_type": "expense",
        "is_deductible": True,
        "default_source": "BS",
        "sort_order": 57
    },
    {
        "category_code": "cleaning",
        "pl_row": 16,
        "display_name": "Cleaning",
        "category_group": "Repairs & Maintenance",
        "transaction_type": "expense",
        "is_deductible": True,
        "default_source": "INV",
        "sort_order": 58
    },

    # =========================================================================
    # PROFESSIONAL SERVICES
    # =========================================================================
    {
        "category_code": "agent_fees",
        "pl_row": 13,
        "display_name": "Property Management Fees",
        "category_group": "Professional Services",
        "transaction_type": "expense",
        "is_deductible": True,
        "default_source": "PM",
        "sort_order": 60
    },
    {
        "category_code": "consulting_accounting",
        "pl_row": 17,
        "display_name": "Accounting Fees",
        "category_group": "Professional Services",
        "transaction_type": "expense",
        "is_deductible": True,
        "default_source": "AF",
        "sort_order": 61
    },
    {
        "category_code": "legal_fees",
        "pl_row": 27,
        "display_name": "Legal Fees",
        "category_group": "Professional Services",
        "transaction_type": "expense",
        "is_deductible": True,
        "default_source": "SS/INV",
        "sort_order": 62
    },
    {
        "category_code": "due_diligence",
        "pl_row": 19,
        "display_name": "Valuation / Due Diligence",
        "category_group": "Professional Services",
        "transaction_type": "expense",
        "is_deductible": True,
        "default_source": "INV",
        "sort_order": 63
    },
    # Aliases for categories that Claude may return
    {
        "category_code": "property_management",
        "pl_row": 13,
        "display_name": "Property Management Fees",
        "category_group": "Professional Services",
        "transaction_type": "expense",
        "is_deductible": True,
        "default_source": "PM",
        "sort_order": 64
    },
    {
        "category_code": "legal_accounting",
        "pl_row": 17,
        "display_name": "Accounting / Legal Fees",
        "category_group": "Professional Services",
        "transaction_type": "expense",
        "is_deductible": True,
        "default_source": "AF",
        "sort_order": 65
    },
    {
        "category_code": "accounting_fee",
        "pl_row": 17,
        "display_name": "Accounting Fee",
        "category_group": "Professional Services",
        "transaction_type": "expense",
        "is_deductible": True,
        "default_source": "AF",
        "sort_order": 66
    },
    {
        "category_code": "accounting_fees",
        "pl_row": 17,
        "display_name": "Accounting Fees",
        "category_group": "Professional Services",
        "transaction_type": "expense",
        "is_deductible": True,
        "default_source": "AF",
        "sort_order": 67
    },
    {
        "category_code": "letting_fee",
        "pl_row": 13,
        "display_name": "Letting Fee",
        "category_group": "Professional Services",
        "transaction_type": "expense",
        "is_deductible": True,
        "default_source": "PM",
        "sort_order": 66
    },
    {
        "category_code": "inspection_fee",
        "pl_row": 13,
        "display_name": "Property Inspection Fee",
        "category_group": "Professional Services",
        "transaction_type": "expense",
        "is_deductible": True,
        "default_source": "PM",
        "sort_order": 67
    },

    # =========================================================================
    # ADVERTISING & ADMIN
    # =========================================================================
    {
        "category_code": "advertising",
        "pl_row": 14,
        "display_name": "Advertising for Tenants",
        "category_group": "Advertising & Admin",
        "transaction_type": "expense",
        "is_deductible": True,
        "default_source": "PM/INV",
        "sort_order": 70
    },
    {
        "category_code": "listing_fees",
        "pl_row": 28,
        "display_name": "Letting / Listing Fees",
        "category_group": "Advertising & Admin",
        "transaction_type": "expense",
        "is_deductible": True,
        "default_source": "PM",
        "sort_order": 71
    },
    {
        "category_code": "postage_courier",
        "pl_row": 33,
        "display_name": "Postage / Stationery",
        "category_group": "Advertising & Admin",
        "transaction_type": "expense",
        "is_deductible": True,
        "default_source": "BS",
        "sort_order": 72
    },
    {
        "category_code": "subscriptions",
        "pl_row": 40,
        "display_name": "Subscriptions",
        "category_group": "Advertising & Admin",
        "transaction_type": "expense",
        "is_deductible": True,
        "default_source": "BS",
        "sort_order": 73
    },

    # =========================================================================
    # UTILITIES
    # =========================================================================
    {
        "category_code": "electricity",
        "pl_row": 20,
        "display_name": "Electricity",
        "category_group": "Utilities",
        "transaction_type": "expense",
        "is_deductible": True,
        "default_source": "BS",
        "sort_order": 80
    },
    {
        "category_code": "gas",
        "pl_row": 21,
        "display_name": "Gas",
        "category_group": "Utilities",
        "transaction_type": "expense",
        "is_deductible": True,
        "default_source": "BS",
        "sort_order": 81
    },
    {
        "category_code": "utilities",
        "pl_row": 20,
        "display_name": "Utilities (General)",
        "category_group": "Utilities",
        "transaction_type": "expense",
        "is_deductible": True,
        "default_source": "BS",
        "sort_order": 82
    },

    # =========================================================================
    # TRAVEL
    # =========================================================================
    {
        "category_code": "mileage",
        "pl_row": 30,
        "display_name": "Mileage / Travel",
        "category_group": "Travel",
        "transaction_type": "expense",
        "is_deductible": True,
        "default_source": "CALC",
        "sort_order": 90
    },
    {
        "category_code": "travel",
        "pl_row": 30,
        "display_name": "Travel Expenses",
        "category_group": "Travel",
        "transaction_type": "expense",
        "is_deductible": True,
        "default_source": "BS",
        "sort_order": 91
    },

    # =========================================================================
    # OTHER (deductible)
    # =========================================================================
    {
        "category_code": "depreciation",
        "pl_row": 18,
        "display_name": "Depreciation - Chattels",
        "category_group": "Other",
        "transaction_type": "expense",
        "is_deductible": True,
        "default_source": "CP",
        "sort_order": 100
    },
    {
        "category_code": "other_deductible",
        "pl_row": None,
        "display_name": "Other Deductible Expense",
        "category_group": "Other",
        "transaction_type": "expense",
        "is_deductible": True,
        "default_source": "BS",
        "sort_order": 101
    },
    {
        "category_code": "other_expense",
        "pl_row": None,
        "display_name": "Other Expense",
        "category_group": "Other",
        "transaction_type": "expense",
        "is_deductible": True,
        "default_source": "BS",
        "sort_order": 102
    },

    # =========================================================================
    # EXCLUDED (not on P&L - non-deductible or not income/expense)
    # =========================================================================
    {
        "category_code": "bond",
        "pl_row": None,
        "display_name": "Bond (Not Income)",
        "category_group": "Excluded",
        "transaction_type": "excluded",
        "is_deductible": False,
        "default_source": "BS",
        "sort_order": 200
    },
    {
        "category_code": "principal_repayment",
        "pl_row": None,
        "display_name": "Loan Principal",
        "category_group": "Excluded",
        "transaction_type": "excluded",
        "is_deductible": False,
        "default_source": "BS",
        "sort_order": 201
    },
    {
        "category_code": "transfer",
        "pl_row": None,
        "display_name": "Transfer Between Accounts",
        "category_group": "Excluded",
        "transaction_type": "excluded",
        "is_deductible": False,
        "default_source": "BS",
        "sort_order": 202
    },
    {
        "category_code": "capital_expense",
        "pl_row": None,
        "display_name": "Capital Expense (Not Deductible)",
        "category_group": "Excluded",
        "transaction_type": "excluded",
        "is_deductible": False,
        "default_source": "BS",
        "sort_order": 203
    },
    {
        "category_code": "capital_purchase",
        "pl_row": None,
        "display_name": "Property Purchase (Capital)",
        "category_group": "Excluded",
        "transaction_type": "excluded",
        "is_deductible": False,
        "default_source": "SS",
        "sort_order": 203
    },
    {
        "category_code": "personal",
        "pl_row": None,
        "display_name": "Personal (Not Property Related)",
        "category_group": "Excluded",
        "transaction_type": "excluded",
        "is_deductible": False,
        "default_source": "BS",
        "sort_order": 204
    },
    {
        "category_code": "funds_introduced",
        "pl_row": None,
        "display_name": "Funds Introduced",
        "category_group": "Excluded",
        "transaction_type": "excluded",
        "is_deductible": False,
        "default_source": "BS",
        "sort_order": 205
    },
    {
        "category_code": "unknown",
        "pl_row": None,
        "display_name": "Unknown - Needs Review",
        "category_group": "Excluded",
        "transaction_type": "flagged",
        "is_deductible": False,
        "default_source": None,
        "sort_order": 999
    },
]


# =============================================================================
# SEED FUNCTIONS
# =============================================================================

async def seed_tax_rules(db: AsyncSession) -> int:
    """Seed tax rules into database."""
    count = 0

    for rule_data in TAX_RULES:
        # Check if rule already exists
        result = await db.execute(
            select(TaxRule).where(
                TaxRule.rule_type == rule_data["rule_type"],
                TaxRule.tax_year == rule_data["tax_year"],
                TaxRule.property_type == rule_data["property_type"]
            )
        )
        existing = result.scalar_one_or_none()

        if not existing:
            rule = TaxRule(
                id=uuid4(),
                rule_type=rule_data["rule_type"],
                tax_year=rule_data["tax_year"],
                property_type=rule_data["property_type"],
                value=rule_data["value"],
                notes=rule_data.get("notes")
            )
            db.add(rule)
            count += 1
            logger.info(f"Added tax rule: {rule_data['rule_type']} - {rule_data['tax_year']} - {rule_data['property_type']}")
        else:
            logger.debug(f"Tax rule already exists: {rule_data['rule_type']} - {rule_data['tax_year']}")

    await db.commit()
    return count


async def seed_pl_row_mappings(db: AsyncSession) -> int:
    """Seed P&L row mappings into database."""
    count = 0

    for mapping_data in PL_ROW_MAPPINGS:
        # Check if mapping already exists
        result = await db.execute(
            select(PLRowMapping).where(
                PLRowMapping.category_code == mapping_data["category_code"]
            )
        )
        existing = result.scalar_one_or_none()

        if not existing:
            mapping = PLRowMapping(
                id=uuid4(),
                category_code=mapping_data["category_code"],
                pl_row=mapping_data["pl_row"],
                display_name=mapping_data["display_name"],
                category_group=mapping_data.get("category_group"),
                transaction_type=mapping_data["transaction_type"],
                is_deductible=mapping_data["is_deductible"],
                default_source=mapping_data.get("default_source"),
                sort_order=mapping_data["sort_order"]
            )
            db.add(mapping)
            count += 1
            logger.info(f"Added P&L mapping: {mapping_data['category_code']} -> Row {mapping_data['pl_row']} ({mapping_data.get('category_group')})")
        else:
            # Update existing mapping with category_group if it doesn't have one
            if existing.category_group is None and mapping_data.get("category_group"):
                existing.category_group = mapping_data["category_group"]
                existing.display_name = mapping_data["display_name"]  # Also update display name
                existing.sort_order = mapping_data["sort_order"]  # Update sort order
                logger.info(f"Updated P&L mapping: {mapping_data['category_code']} with group {mapping_data['category_group']}")

    await db.commit()
    return count


async def seed_all(db: AsyncSession = None) -> dict:
    """Seed all initial data."""
    close_session = False

    if db is None:
        db = AsyncSessionLocal()
        close_session = True

    try:
        results = {
            "tax_rules": await seed_tax_rules(db),
            "pl_row_mappings": await seed_pl_row_mappings(db)
        }

        logger.info(f"Seeding complete: {results}")
        return results

    finally:
        if close_session:
            await db.close()


# =============================================================================
# CLI ENTRY POINT
# =============================================================================

async def main():
    """Run seeding from command line."""
    logging.basicConfig(level=logging.INFO)

    logger.info("Seeding database with initial data...")

    results = await seed_all()

    logger.info(f"Tax rules added: {results['tax_rules']}")
    logger.info(f"P&L row mappings added: {results['pl_row_mappings']}")
    logger.info("Seeding complete!")


if __name__ == "__main__":
    asyncio.run(main())