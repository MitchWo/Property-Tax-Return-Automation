"""Transaction extraction prompts for bank statements."""

BANK_STATEMENT_EXTRACTION_PROMPT = """You are extracting transactions from a New Zealand bank statement for rental property tax return preparation.

## Property Context
- Property Address: {property_address}
- Tax Year: {tax_year} ({tax_year_start} to {tax_year_end})
- Property Type: {property_type}
- Year of Ownership: {year_of_ownership}

## Your Task

Extract ALL transactions from this bank statement that fall within the tax year period.

## CRITICAL RULES

1. **Interest Charges**:
   - Interest is often charged BI-WEEKLY (every ~14 days)
   - Look for patterns like "DEBIT INTEREST", "LOAN INTEREST", "HOUSING NZ INT"
   - Count each interest debit separately - do NOT combine them
   - There may be 24-26 interest charges per year

2. **Income vs Bond**:
   - Rent payments are INCOME
   - Bond payments are NOT INCOME (exclude or flag)
   - If description contains "bond", flag for review
   - If a payment seems unusually large, it may include bond

3. **Transaction Types**:
   - CREDIT/deposit = income (positive amount)
   - DEBIT/withdrawal = expense (negative amount)

4. **Date Filtering**:
   - Only include transactions dated {tax_year_start} to {tax_year_end}
   - Ignore transactions outside this range

## Output Format

Return a JSON array of transactions:
```json
{{
  "transactions": [
    {{
      "date": "YYYY-MM-DD",
      "description": "Original description from statement",
      "other_party": "Payee/payer name if visible",
      "amount": -123.45,
      "balance": 5678.90,
      "suggested_category": "category_code",
      "confidence": 0.85,
      "needs_review": false,
      "review_reason": null,
      "row_number": 1
    }}
  ],
  "summary": {{
    "total_transactions": 45,
    "date_range": {{
      "earliest": "YYYY-MM-DD",
      "latest": "YYYY-MM-DD"
    }},
    "total_credits": 12345.67,
    "total_debits": -9876.54,
    "interest_charges_count": 26,
    "interest_charges_total": -4567.89
  }},
  "warnings": [
    "Description of any issues found"
  ]
}}
```

## Category Codes to Use

**Income:**
- rental_income: Rent payments from tenants/PM
- water_rates_recovered: Water reimbursements
- bank_contribution: Cashbacks, rebates
- insurance_payout: Insurance claims
- other_income: Other credits

**Expenses:**
- interest: Loan interest charges
- rates: Council rates
- water_rates: Water charges
- body_corporate: BC levies
- insurance: Insurance payments
- agent_fees: PM fees
- repairs_maintenance: Repairs
- bank_fees: Account fees
- gardening: Lawn/garden
- electricity: Power bills
- legal_fees: Solicitor

**Excluded:**
- bond: Bond (NOT income)
- transfer: Internal transfers
- principal_repayment: Loan principal
- personal: Non-property items

**Uncertain:**
- unknown: Cannot determine

## Important Notes

- If you cannot read a value clearly, set needs_review: true
- For amounts, use negative for debits, positive for credits
- Include the running balance if visible
- Preserve original descriptions exactly as shown
"""


LOAN_STATEMENT_EXTRACTION_PROMPT = """You are extracting information from a New Zealand loan/mortgage statement.

## Property Context
- Property Address: {property_address}
- Tax Year: {tax_year}

## IMPORTANT

We do NOT use loan statements for interest expense calculations. The bank statement is the authoritative source for interest.

## Your Task

Extract loan account details for verification purposes only:
```json
{{
  "loan_account_number": "XX-XXXX-XXXXXXXX-XX",
  "lender": "Bank name",
  "loan_type": "Residential mortgage",
  "property_secured": "Address if shown",
  "current_balance": 450000.00,
  "interest_rate": 6.99,
  "statement_period": {{
    "start": "YYYY-MM-DD",
    "end": "YYYY-MM-DD"
  }},
  "interest_shown": 12345.67,
  "note": "Interest figure is for reference only - use bank statement for actual expense"
}}
```

## DO NOT

- Use the interest figure from this statement for tax calculations
- Extract individual transactions (we use bank statement instead)

This information is used only to:
1. Verify the loan exists for this property
2. Confirm the property address matches
3. Cross-reference loan account numbers
"""