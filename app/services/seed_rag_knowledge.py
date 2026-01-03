"""
RAG Knowledge Seeding Script

Seeds the Pinecone vector database with domain knowledge for NZ rental property tax returns.
This includes common errors, tax rules, skill learnings, and P&L mapping knowledge.

Usage:
    poetry run python -m app.services.seed_rag_knowledge
"""

import asyncio
import logging
from datetime import datetime

from app.services.phase2_feedback_learning.knowledge_store import knowledge_store

logger = logging.getLogger(__name__)

# ============================================================================
# COMMON-ERRORS NAMESPACE (20 records)
# Error patterns to avoid during processing
# ============================================================================
COMMON_ERRORS = [
    {
        "content": "ERROR: Including loan principal repayments as deductible expenses. Principal repayments are NOT tax deductible - only the interest portion of loan payments is deductible. Always separate interest from principal.",
        "scenario": "principal_vs_interest",
        "category": "loan_statement_errors"
    },
    {
        "content": "ERROR: Using gross interest instead of net interest when settlement statement shows 'interest on deposit' credit. The vendor credit for interest on deposit must be subtracted from gross interest to get the correct net deductible interest.",
        "scenario": "settlement_interest_adjustment",
        "category": "interest_calculation_errors"
    },
    {
        "content": "ERROR: Including body corporate RESERVE/SINKING fund levies as deductible expenses. Only OPERATING fund levies are deductible. Reserve fund contributions are capital in nature.",
        "scenario": "body_corporate_fund_type",
        "category": "body_corporate_errors"
    },
    {
        "content": "ERROR: Treating personal expenses from bank statements as rental deductions. Common personal items: supermarket purchases, retail shopping, personal insurance, personal subscriptions. These must be excluded.",
        "scenario": "personal_expense_inclusion",
        "category": "categorization_errors"
    },
    {
        "content": "ERROR: Double-counting rent income from both PM statements and bank deposits. PM statement is the primary source for rent. Bank deposits should only add 'bank contribution' income not captured in PM statements.",
        "scenario": "rent_double_counting",
        "category": "income_errors"
    },
    {
        "content": "ERROR: Including HOME & CONTENTS insurance as deductible. Only LANDLORD INSURANCE is deductible for rental properties. Home & contents is a personal policy type.",
        "scenario": "wrong_insurance_type",
        "category": "insurance_errors"
    },
    {
        "content": "ERROR: Failing to pro-rate depreciation for partial year ownership. If property was owned less than 12 months, depreciation must be calculated as: (annual amount × months owned ÷ 12).",
        "scenario": "depreciation_proration",
        "category": "depreciation_errors"
    },
    {
        "content": "ERROR: Using 80% interest deductibility for new builds. Properties with CCC issued after 27 March 2020 qualify as 'new builds' and get 100% interest deductibility, not 80%.",
        "scenario": "new_build_deductibility",
        "category": "interest_deductibility_errors"
    },
    {
        "content": "ERROR: Missing PM fees GST component. Property management fees should be recorded GST-inclusive (base fee + GST). The GST portion is part of the deductible expense.",
        "scenario": "pm_fees_gst",
        "category": "gst_errors"
    },
    {
        "content": "ERROR: Treating legal fees over $10,000 as fully deductible. Legal fees under $10k are generally deductible. Over $10k requires analysis of whether they are capital (purchase/sale) or revenue (tenancy disputes).",
        "scenario": "legal_fees_threshold",
        "category": "legal_fees_errors"
    },
    {
        "content": "ERROR: Including interest credits/refunds as additional deductions. Interest credits from bank must be SUBTRACTED from interest expense, not added. Net interest = debits - credits.",
        "scenario": "interest_credit_handling",
        "category": "interest_calculation_errors"
    },
    {
        "content": "ERROR: Categorizing chattels depreciation incorrectly. Chattels (appliances, fixtures) go to Row 17 Depreciation, not Row 35 Repairs & Maintenance, even if they are replacements.",
        "scenario": "chattels_categorization",
        "category": "depreciation_errors"
    },
    {
        "content": "ERROR: Missing vendor rates credit in Year 1 calculations. For purchase year, rates = Bank Paid + (Vendor Instalment - Vendor Credit from settlement). The settlement adjustment is often missed.",
        "scenario": "year1_rates_calculation",
        "category": "settlement_errors"
    },
    {
        "content": "ERROR: Including bank fees from personal accounts. Only bank fees from accounts specifically used for the rental property are deductible. Joint/personal account fees must be excluded.",
        "scenario": "personal_bank_fees",
        "category": "bank_statement_errors"
    },
    {
        "content": "ERROR: Treating all maintenance invoices as immediately deductible. Repairs over $800 without supporting invoice require verification. Capital improvements (adding value) may not be immediately deductible.",
        "scenario": "capital_vs_revenue_maintenance",
        "category": "repairs_errors"
    },
    {
        "content": "ERROR: Excluding accounting fees from deductions. Standard accounting/tax preparation fees ($862.50 for Lighthouse) are always deductible at Row 16 Consulting & Accounting.",
        "scenario": "accounting_fees_missing",
        "category": "professional_fees_errors"
    },
    {
        "content": "ERROR: Including mortgage drawdown as income. Loan drawdowns/advances are not income - they are capital transactions and must be excluded from the P&L.",
        "scenario": "loan_drawdown_as_income",
        "category": "income_errors"
    },
    {
        "content": "ERROR: Mixing up letting fees and ongoing management fees. Letting fees (tenant placement) and ongoing management fees are both deductible but may appear separately on PM statements. Both go to Row 13.",
        "scenario": "letting_vs_management_fees",
        "category": "pm_statement_errors"
    },
    {
        "content": "ERROR: Forgetting to check for interest offset accounts. If property has an offset account, the 'interest saved' may need to be accounted for in net interest calculations.",
        "scenario": "offset_account_interest",
        "category": "interest_calculation_errors"
    },
    {
        "content": "ERROR: Including resident society levies incorrectly. Resident society operating levies are deductible (Row 36), but capital/reserve contributions are not deductible.",
        "scenario": "resident_society_capital",
        "category": "body_corporate_errors"
    }
]

# ============================================================================
# TAX-RULES NAMESPACE (12 records)
# Tax treatment and deductibility rules
# ============================================================================
TAX_RULES = [
    {
        "content": "INTEREST DEDUCTIBILITY - New Builds: Properties with Code Compliance Certificate (CCC) issued on or after 27 March 2020 are classified as 'new builds' and qualify for 100% interest deductibility regardless of purchase date or tax year.",
        "scenario": "new_build_interest_deductibility",
        "category": "interest_rules"
    },
    {
        "content": "INTEREST DEDUCTIBILITY - Existing Properties: For properties NOT classified as new builds, interest deductibility phases in: FY24=50%, FY25=80%, FY26 onwards=100%.",
        "scenario": "existing_property_interest_deductibility",
        "category": "interest_rules"
    },
    {
        "content": "LEGAL FEES RULE: Legal fees under $10,000 are generally treated as immediately deductible revenue expenses. Legal fees $10,000+ require analysis: purchase/sale legal fees are capital, tenancy/dispute legal fees are revenue.",
        "scenario": "legal_fees_deductibility",
        "category": "professional_fees_rules"
    },
    {
        "content": "BODY CORPORATE LEVIES: Only OPERATING fund levies are deductible expenses. RESERVE fund (sinking fund) contributions are capital in nature and not immediately deductible. Check levy breakdown carefully.",
        "scenario": "body_corporate_treatment",
        "category": "levy_rules"
    },
    {
        "content": "REPAIRS VS CAPITAL: Repairs that restore an asset to its original condition are deductible. Improvements that add value or extend useful life beyond original are capital. The $800 threshold triggers invoice verification requirement.",
        "scenario": "repairs_capital_distinction",
        "category": "repairs_rules"
    },
    {
        "content": "DEPRECIATION PRO-RATA: For partial year ownership, depreciation must be pro-rated. Formula: Annual depreciation × (months owned ÷ 12). First year of ownership almost always requires pro-rating.",
        "scenario": "depreciation_proration_rule",
        "category": "depreciation_rules"
    },
    {
        "content": "GST ON PM FEES: Property management fees are always GST-inclusive for deduction purposes. Record base fee + GST as the full deductible amount. PM statements typically show both components.",
        "scenario": "pm_fees_gst_treatment",
        "category": "gst_rules"
    },
    {
        "content": "INSURANCE DEDUCTIBILITY: Only LANDLORD INSURANCE policies are deductible. Home & Contents insurance is personal and not deductible even if it covers a rental property. Check policy type carefully.",
        "scenario": "insurance_type_rule",
        "category": "insurance_rules"
    },
    {
        "content": "RATES YEAR 1 RULE: For purchase year, total rates = Bank Paid Rates + (Vendor Instalment from settlement - Vendor Credit from settlement). Settlement adjustments are critical for accurate Year 1 rates.",
        "scenario": "year1_rates_rule",
        "category": "rates_rules"
    },
    {
        "content": "DUE DILIGENCE COSTS: Pre-purchase due diligence costs (LIM reports, building inspections, meth tests, valuations) are deductible at Row 18 Due Diligence, not capital costs.",
        "scenario": "due_diligence_deductibility",
        "category": "professional_fees_rules"
    },
    {
        "content": "HEALTHY HOMES COMPLIANCE: Installation costs for heating, ventilation, insulation, moisture barriers, and drainage to meet Healthy Homes Standards are deductible as repairs (Row 35), not capital improvements.",
        "scenario": "healthy_homes_treatment",
        "category": "repairs_rules"
    },
    {
        "content": "ACCOUNTING FEES: Standard tax preparation and accounting fees are always deductible. Lighthouse Financial standard fee is $862.50 and should be included at Row 16 even if not yet invoiced.",
        "scenario": "accounting_fees_rule",
        "category": "professional_fees_rules"
    }
]

# ============================================================================
# SKILL_LEARNINGS NAMESPACE (15 records)
# Domain knowledge and calculation patterns
# ============================================================================
SKILL_LEARNINGS = [
    {
        "content": "INTEREST CALCULATION: Net mortgage interest = Sum of all INTEREST DEBIT transactions - Sum of any INTEREST CREDIT transactions. Do NOT include principal repayments, fees, or other charges in interest calculations.",
        "scenario": "net_interest_calculation",
        "category": "calculation_patterns"
    },
    {
        "content": "SETTLEMENT STATEMENT PROCESSING: Key items from settlement statement for Year 1: (1) Purchase price, (2) Settlement date, (3) Rates apportionment (vendor credit), (4) Interest on deposit (vendor credit), (5) Legal fees. Process line by line in order.",
        "scenario": "settlement_processing",
        "category": "document_processing"
    },
    {
        "content": "PM STATEMENT HIERARCHY: Property Manager statement is PRIMARY source for rental income. Gross Rent = Rent collected by PM. Bank deposits of rent (from PM) are transfers, not additional income. Only direct tenant payments to bank are additional income.",
        "scenario": "pm_income_hierarchy",
        "category": "income_processing"
    },
    {
        "content": "BANK CONTRIBUTION INCOME: When a landlord deposits personal funds to cover shortfalls (mortgage payments, repairs), this is 'bank contribution' income at Row 8. It increases assessable income but is often overlooked.",
        "scenario": "bank_contribution_recognition",
        "category": "income_patterns"
    },
    {
        "content": "LOAN STATEMENT INTEREST EXTRACTION: From loan statements, ONLY extract interest charges. Ignore: principal repayments, fees, insurance premiums, offset adjustments. Look for 'INTEREST' in description.",
        "scenario": "loan_interest_extraction",
        "category": "document_processing"
    },
    {
        "content": "INVOICE MATCHING: For expenses >$800, match bank payment to invoice. Invoice date may differ from payment date. Look for: amount match, vendor match, description correlation. Flag if no matching invoice found.",
        "scenario": "invoice_matching_process",
        "category": "validation_patterns"
    },
    {
        "content": "CROSS-DOCUMENT VALIDATION: Interest from bank statement should approximately match interest from loan statement (within 5%). Large variances indicate: missing months, wrong account, or extraction errors.",
        "scenario": "interest_cross_validation",
        "category": "validation_patterns"
    },
    {
        "content": "PROPERTY TYPE DETERMINATION: Check for CCC in documents. If CCC date >= 27 March 2020, property is 'new_build' with 100% interest. If user unsure, use Phase 1 AI suggestion from CCC analysis.",
        "scenario": "property_type_determination",
        "category": "classification_patterns"
    },
    {
        "content": "EXCLUDED TRANSACTIONS: Always exclude from P&L: loan principal, personal expenses, transfers between own accounts, loan drawdowns, mortgage redraw, savings transfers, credit card payments.",
        "scenario": "excluded_transaction_patterns",
        "category": "categorization_patterns"
    },
    {
        "content": "WATER RATES VS WATER RECOVERED: Water rates paid by landlord go to Row 41 as expense. Water charges recovered from tenant (shown on PM statement or rates invoice) go to Row 7 as income.",
        "scenario": "water_income_expense_split",
        "category": "income_expense_patterns"
    },
    {
        "content": "MULTI-PROPERTY RETURNS: When processing returns with multiple properties, keep transactions strictly separated by property. Each property should have its own P&L section. Never mix property transactions.",
        "scenario": "multi_property_separation",
        "category": "processing_patterns"
    },
    {
        "content": "QA VALIDATION PRINCIPLE: When QA finds variance, check if adjustments exist (like 'interest on deposit' credit). If legitimate adjustments explain the variance, accept the adjusted amount - don't override with gross.",
        "scenario": "qa_adjustment_handling",
        "category": "validation_patterns"
    },
    {
        "content": "SOURCE CODE ASSIGNMENT: Always track transaction source. BS=Bank Statement, PM=Property Manager, LS=Loan Statement, SS=Settlement Statement, INV=Invoice, DEP=Depreciation Schedule.",
        "scenario": "source_code_tracking",
        "category": "audit_trail_patterns"
    },
    {
        "content": "FLAGGING THRESHOLDS: Flag for review when: repairs >$800 without invoice, interest variance >5%, categorization confidence <70%, transaction description unclear or ambiguous.",
        "scenario": "flagging_criteria",
        "category": "validation_patterns"
    },
    {
        "content": "VERIFICATION STATUS: Mark as 'verified' when cross-document validation passes. Mark as 'needs_review' when flagged or variance detected. Mark as 'estimated' when pro-rating or assuming values.",
        "scenario": "verification_status_assignment",
        "category": "audit_trail_patterns"
    }
]

# ============================================================================
# PNL-MAPPING NAMESPACE (17 records)
# P&L row assignments for Lighthouse template
# ============================================================================
PNL_MAPPING = [
    {
        "content": "ROW 6 - RENTAL INCOME: All rental income from property management statements and direct tenant payments. This is gross rent before any expenses or management fees.",
        "scenario": "row6_rental_income",
        "category": "income_mapping"
    },
    {
        "content": "ROW 7 - WATER RECOVERED: Water charges recovered from tenants, typically shown on property manager statements or as tenant reimbursements. This is income, not expense reduction.",
        "scenario": "row7_water_recovered",
        "category": "income_mapping"
    },
    {
        "content": "ROW 8 - BANK CONTRIBUTION: Cash injections by owner to cover rental property expenses. When landlord deposits personal funds to pay mortgage or expenses, this is assessable income.",
        "scenario": "row8_bank_contribution",
        "category": "income_mapping"
    },
    {
        "content": "ROW 12 - ADVERTISING: Costs for advertising the property for rent, including Trade Me listings, newspaper ads, signage. Usually appears on PM statements as letting/advertising fees.",
        "scenario": "row12_advertising",
        "category": "expense_mapping"
    },
    {
        "content": "ROW 13 - AGENT FEES: All property management fees including base management fee (GST-inclusive), letting fees for new tenants, and lease renewal fees. Sum all PM fee types.",
        "scenario": "row13_agent_fees",
        "category": "expense_mapping"
    },
    {
        "content": "ROW 15 - BODY CORPORATE: Operating fund levies only. Do NOT include reserve/sinking fund levies here. Usually appears as quarterly or monthly levies from body corporate administrator.",
        "scenario": "row15_body_corporate",
        "category": "expense_mapping"
    },
    {
        "content": "ROW 16 - CONSULTING & ACCOUNTING: Tax preparation fees (standard $862.50 for Lighthouse), accountant fees, tax advice. Always include even if not yet invoiced at return time.",
        "scenario": "row16_accounting",
        "category": "expense_mapping"
    },
    {
        "content": "ROW 17 - DEPRECIATION: Building depreciation (if pre-2011), chattels depreciation (appliances, fixtures, carpets). Calculated from depreciation schedule, pro-rated for partial year.",
        "scenario": "row17_depreciation",
        "category": "expense_mapping"
    },
    {
        "content": "ROW 18 - DUE DILIGENCE: Pre-purchase investigation costs including LIM reports, building inspections, meth testing, valuations. These are deductible even though incurred before purchase.",
        "scenario": "row18_due_diligence",
        "category": "expense_mapping"
    },
    {
        "content": "ROW 24 - INSURANCE: Landlord insurance premiums only. Must be specific landlord/rental property insurance policy. Home & contents insurance is NOT deductible here.",
        "scenario": "row24_insurance",
        "category": "expense_mapping"
    },
    {
        "content": "ROW 25 - INTEREST EXPENSE: Net mortgage interest after applying deductibility percentage. For new builds=100%, for existing FY25=80%. Calculate: gross interest × deductibility %.",
        "scenario": "row25_interest",
        "category": "expense_mapping"
    },
    {
        "content": "ROW 27 - LEGAL FEES: Legal fees for property-related matters. Under $10k generally deductible. Purchase/sale conveyancing typically capital but small amounts may be included.",
        "scenario": "row27_legal_fees",
        "category": "expense_mapping"
    },
    {
        "content": "ROW 34 - RATES: Council rates paid for the property. For Year 1, includes settlement adjustment: Bank Paid + (Vendor Instalment - Vendor Credit).",
        "scenario": "row34_rates",
        "category": "expense_mapping"
    },
    {
        "content": "ROW 35 - REPAIRS & MAINTENANCE: Repairs, maintenance, cleaning, gardening, pest control, healthy homes compliance work. Does NOT include capital improvements that add value.",
        "scenario": "row35_repairs",
        "category": "expense_mapping"
    },
    {
        "content": "ROW 36 - RESIDENT SOCIETY: Operating levies from resident societies (distinct from body corporates). Only operating portion is deductible, not capital contributions.",
        "scenario": "row36_resident_society",
        "category": "expense_mapping"
    },
    {
        "content": "ROW 41 - WATER RATES: Water rates and usage charges paid by landlord. If tenant pays water directly, do not include here. Recovered water goes to Row 7 income.",
        "scenario": "row41_water_rates",
        "category": "expense_mapping"
    },
    {
        "content": "EXCLUDED FROM P&L: Loan principal repayments, personal expenses, inter-account transfers, loan drawdowns, credit card payments, mortgage redraw, savings deposits. These NEVER appear in P&L rows.",
        "scenario": "excluded_items",
        "category": "exclusion_mapping"
    }
]


async def seed_namespace(records: list, namespace: str) -> int:
    """
    Seed a single namespace with records.

    Args:
        records: List of record dicts with content, scenario, category
        namespace: Pinecone namespace to seed

    Returns:
        Number of records successfully stored
    """
    success_count = 0

    for i, record in enumerate(records):
        try:
            result = await knowledge_store.store(
                content=record["content"],
                scenario=record["scenario"],
                category=record["category"],
                source="seed_script",
                namespace=namespace
            )
            if result:
                success_count += 1
                logger.info(f"  [{i+1}/{len(records)}] Stored: {record['scenario']}")
            else:
                logger.warning(f"  [{i+1}/{len(records)}] Failed to store: {record['scenario']}")

            # Small delay to avoid rate limiting
            await asyncio.sleep(0.1)

        except Exception as e:
            logger.error(f"  [{i+1}/{len(records)}] Error storing {record['scenario']}: {e}")

    return success_count


async def seed_all_namespaces():
    """Seed all namespaces with domain knowledge."""

    print("\n" + "="*60)
    print("RAG KNOWLEDGE SEEDING SCRIPT")
    print("="*60)

    if not knowledge_store.enabled:
        print("\nERROR: Pinecone is not configured. Please set PINECONE_API_KEY and PINECONE_INDEX_HOST.")
        return

    total_records = len(COMMON_ERRORS) + len(TAX_RULES) + len(SKILL_LEARNINGS) + len(PNL_MAPPING)
    print(f"\nTotal records to seed: {total_records}")
    print(f"  - common-errors: {len(COMMON_ERRORS)} records")
    print(f"  - tax-rules: {len(TAX_RULES)} records")
    print(f"  - skill_learnings: {len(SKILL_LEARNINGS)} records")
    print(f"  - pnl-mapping: {len(PNL_MAPPING)} records")

    results = {}

    # Seed common-errors namespace
    print(f"\n[1/4] Seeding common-errors namespace ({len(COMMON_ERRORS)} records)...")
    results["common-errors"] = await seed_namespace(COMMON_ERRORS, "common-errors")

    # Seed tax-rules namespace
    print(f"\n[2/4] Seeding tax-rules namespace ({len(TAX_RULES)} records)...")
    results["tax-rules"] = await seed_namespace(TAX_RULES, "tax-rules")

    # Seed skill_learnings namespace
    print(f"\n[3/4] Seeding skill_learnings namespace ({len(SKILL_LEARNINGS)} records)...")
    results["skill_learnings"] = await seed_namespace(SKILL_LEARNINGS, "skill_learnings")

    # Seed pnl-mapping namespace
    print(f"\n[4/4] Seeding pnl-mapping namespace ({len(PNL_MAPPING)} records)...")
    results["pnl-mapping"] = await seed_namespace(PNL_MAPPING, "pnl-mapping")

    # Summary
    print("\n" + "="*60)
    print("SEEDING COMPLETE")
    print("="*60)

    total_success = sum(results.values())
    print(f"\nResults:")
    for namespace, count in results.items():
        expected = {
            "common-errors": len(COMMON_ERRORS),
            "tax-rules": len(TAX_RULES),
            "skill_learnings": len(SKILL_LEARNINGS),
            "pnl-mapping": len(PNL_MAPPING)
        }[namespace]
        status = "OK" if count == expected else "PARTIAL"
        print(f"  {namespace}: {count}/{expected} [{status}]")

    print(f"\nTotal: {total_success}/{total_records} records seeded successfully")

    return results


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format='%(message)s')
    asyncio.run(seed_all_namespaces())
