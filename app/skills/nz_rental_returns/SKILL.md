# NZ Rental Property Tax Returns (IR3R) - Domain Knowledge

## Overview

This skill contains domain expertise for preparing New Zealand rental property tax returns (IR3R). It covers document processing, transaction categorization, and workbook generation for Lighthouse Financial accounting firm.

## System Architecture

The system operates in two phases:

### Phase 1: Document Intake & Classification
- Document upload and Claude Vision classification
- **Full transaction extraction** from financial documents
- Preliminary categorization with confidence scores
- Completeness analysis and blocking issue detection

### Phase 2: Transaction Processing & Learning
- Reads pre-extracted transactions from Phase 1
- Multi-layer categorization (YAML → Learned → RAG → Claude)
- RAG integration with Pinecone for pattern learning
- Workbook generation (Lighthouse Financial template)

## Tax Year Reference

| Tax Year | Period | Interest Deductibility (Existing) | Interest Deductibility (New Build) |
|----------|--------|-----------------------------------|-----------------------------------|
| FY24 | 1 Apr 2023 - 31 Mar 2024 | 50% | 100% |
| FY25 | 1 Apr 2024 - 31 Mar 2025 | 80% | 100% |
| FY26 | 1 Apr 2025 - 31 Mar 2026 | 100% | 100% |

**New Build Definition**: Property with Code Compliance Certificate (CCC) issued AFTER 27 March 2020.

## Critical Business Rules

### 1. Interest Source - ALWAYS Use Bank Statement

**CRITICAL**: Interest expense MUST come from the bank statement, NOT loan statements.

- Bank statements show actual interest debited from the account
- Loan statements may show different figures (projected, rounded)
- Interest is often charged BI-WEEKLY (~26 charges per year)
- Sum ALL interest debits - never subtract credits or adjustments

### 2. Year 1 (First Year of Ownership) - Settlement Statement Required

For Year 1 properties, extract these from the Settlement Statement:

| Item | P&L Row | Notes |
|------|---------|-------|
| Rates Apportionment | 34 | Add to rates from bank statement |
| Vendor Credit for Rates | 34 | SUBTRACT from total rates |
| Body Corporate Pro-rata | 16 | Operating fund only |
| Resident Society Pro-rata | 36 | Separate from Body Corp |
| Legal Fees | 27 | Conveyancing costs |
| Interest on Deposit | 26 | NET against interest expense |

**Rates Calculation (Year 1)**:
```
Total Rates = Settlement Apportionment + Bank Statement Instalments - Vendor Credit
```

### 3. Bond vs Rent - ALWAYS Confirm

**CRITICAL**: Bond is NOT income. When a tenant payment arrives:

- If description mentions "bond" → Flag for review
- If amount is unusually large → Could be bond + rent combined
- ALWAYS ask client to confirm the split

### 4. Depreciation Pro-Rating

For partial year ownership:
```
Deductible Depreciation = Full Year Amount × (Months Owned ÷ 12)
```

### 5. GST Handling

- Most landlords are NOT GST registered
- If not registered, all amounts are GST-inclusive
- Water rates row (41) is always GST-inclusive for non-registered

## Category Groups

Categories are organized into groups for UI display:

| Group | Description |
|-------|-------------|
| Income | Rental income, bank contributions, rates recovered |
| Interest & Finance | Mortgage interest, bank fees, principal repayments |
| Rates & Levies | Council rates, water rates, body corporate |
| Insurance | Landlord insurance |
| Property Management | Agent fees, letting fees, advertising |
| Repairs & Maintenance | Repairs, maintenance, cleaning, gardening |
| Utilities | Electricity, gas (if landlord pays) |
| Professional Services | Legal, accounting, due diligence |
| Other Expenses | Subscriptions, postage, mileage |
| Excluded | Non-deductible items (personal, capital, bonds) |

## P&L Row Mappings (IR3R Workbook)

### Income Section (Rows 6-11)

| Row | Category Code | Display Name | Source |
|-----|--------------|--------------|--------|
| 6 | rental_income | Rental Income | BS/PM |
| 7 | water_rates_recovered | Water Rates Recovered | BS |
| 8 | bank_contribution | Bank Contribution / Cashback | BS |
| 9 | insurance_payout | Insurance Payout | BS |
| 10 | other_income | Other Income | BS |

### Expense Section (Rows 13-41)

| Row | Category Code | Display Name | Source | Notes |
|-----|--------------|--------------|--------|--------|
| 13 | agent_fees | Agent Fees / Commission | PM | Property management fees |
| 14 | advertising | Advertising | PM/INV | Tenant finding |
| 15 | bank_fees | Bank Fees | BS | Account fees only |
| 16 | body_corporate | Body Corporate Levies | BS/SS | Operating fund ONLY |
| 17 | consulting_accounting | Consulting & Accounting | AF | Always $862.50 |
| 18 | depreciation | Depreciation - Chattels | CP | Pro-rate if partial year |
| 19 | due_diligence | Due Diligence / Reports | INV | LIM, valuations, inspections |
| 20 | electricity | Electricity | BS | If landlord pays |
| 21 | gas | Gas | BS | If landlord pays |
| 22 | gardening | Gardening / Lawns | BS/PM |  |
| 23 | healthy_homes | Healthy Homes Compliance | INV |  |
| 24 | hire_purchase | Hire Purchase Interest | INV |  |
| 25 | insurance | Insurance - Landlord | INV | Must be LANDLORD insurance |
| 26 | interest | Interest Expense | BS | From bank statement ONLY |
| 27 | legal_fees | Legal Fees | SS/INV |  |
| 28 | listing_fees | Listing Fees | PM |  |
| 29 | meth_testing | Meth Testing | INV |  |
| 30 | mileage | Mileage | CALC | IRD rates |
| 31 | mortgage_admin | Mortgage Admin/Break Fee | BS |  |
| 32 | pest_control | Pest Control | BS/PM |  |
| 33 | postage_courier | Postage / Courier | BS |  |
| 34 | rates | Rates - Council | BS/SS | See Year 1 calculation |
| 35 | repairs_maintenance | Repairs & Maintenance | BS/PM |  |
| 36 | resident_society | Resident Society Levies | SS/INV | Separate from BC |
| 37 | rubbish_collection | Rubbish Collection | BS |  |
| 38 | security | Security | BS |  |
| 39 | smoke_alarms | Smoke Alarms | INV |  |
| 40 | subscriptions | Subscriptions | BS |  |
| 41 | water_rates | Water Rates | BS | GST-inclusive |

### Excluded Categories (Non-deductible)

| Category Code | Display Name | Notes |
|--------------|--------------|-------|
| principal_repayment | Loan Principal | NOT deductible |
| funds_introduced | Funds Introduced | Capital contribution |
| personal | Personal Expense | Non-rental |
| bond | Bond/Deposit | Not income |
| transfer | Account Transfer | Internal movement |
| capital_expense | Capital Improvement | Not current expense |

### Source Codes
- **BS**: Bank Statement
- **PM**: Property Manager Statement
- **SS**: Settlement Statement
- **INV**: Invoice/Document
- **AF**: Accounting Fee (standard)
- **CP**: Chattel Pack/Depreciation Schedule
- **CALC**: Calculated

## Transaction Extraction (Phase 1)

Financial documents have ALL transactions extracted during Phase 1:

```json
{
  "transactions": [
    {
      "date": "2024-01-15",
      "description": "LOAN INTEREST CHARGED",
      "amount": -523.45,
      "other_party": "ANZ Bank",
      "suggested_category": "interest",
      "confidence": 0.95,
      "needs_review": false,
      "review_reasons": []
    }
  ],
  "transaction_summary": {
    "total_count": 45,
    "flagged_count": 5,
    "total_income": 15600.00,
    "total_expenses": -8234.50
  }
}
```

## Multi-layer Categorization (Phase 2)

```
1. YAML Patterns (95% confidence)
        │ No match
        ▼
2. Learned Patterns (80-90%)
        │ No match
        ▼
3. RAG Semantic Search (70-80%)
        │ Low confidence
        ▼
4. Claude AI Fallback (60-90%)
```

## Document Processing Guidelines

### Bank Statements
- Extract ALL transactions for the tax year period
- Identify the rental account (not personal accounts)
- Look for:
  - Rent deposits (income)
  - Interest charges (bi-weekly pattern)
  - Council rates payments
  - Insurance payments
  - Repairs/maintenance
  - Property manager disbursements

### Loan Statements
- Do NOT use for interest figures
- Use only to verify:
  - Loan exists for the property
  - Loan account number
  - Property security address matches

### Property Manager Statements
- Cross-reference with bank statement
- Extract:
  - Gross rent collected
  - Management fee percentage and amount
  - Maintenance/repairs paid
  - Advertising costs
  - Letting fees
- The NET amount should match bank deposits

### Settlement Statements (Year 1)
- Required for first year of ownership
- Extract all apportionments (see table above)
- Note the settlement date for pro-rating

## Common Mistakes to Avoid

1. **Using loan statement interest** - Always use bank statement
2. **Including bond as income** - Bond is NOT taxable
3. **Wrong insurance type** - Must be landlord/rental insurance
4. **Missing settlement apportionments** - Critical for Year 1
5. **Not pro-rating depreciation** - Must adjust for partial year
6. **Mixing personal and rental** - Ensure correct account
7. **Forgetting vendor credits** - Reduces rates expense

## RAG Learning System

The system learns from user corrections:

1. User reviews/corrects transaction category
2. Transaction marked as `manually_reviewed = True`
3. On "Save & Commit Learnings":
   - Only reviewed transactions saved
   - Duplicate check (0.95 threshold)
   - Stored in Pinecone `transaction-coding` namespace

This enables continuous improvement in categorization accuracy.
