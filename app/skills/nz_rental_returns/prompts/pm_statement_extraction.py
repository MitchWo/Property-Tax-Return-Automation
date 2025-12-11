"""Property manager statement extraction prompts."""

PM_STATEMENT_EXTRACTION_PROMPT = """You are extracting financial data from a New Zealand property manager statement for a rental property tax return.

## Property Context
- Property Address: {property_address}
- Tax Year: {tax_year} ({tax_year_start} to {tax_year_end})

## Understanding PM Statements

Property managers collect rent, deduct their fees and expenses, then remit the NET amount to the landlord. The bank statement shows only the net deposit, so we need the PM statement to see the breakdown.

## Key Items to Extract

### 1. Gross Rent Collected (P&L Row 6 - Income)
- Total rent received from tenant
- This is the INCOME figure (not the net payment)

### 2. Management Fee (P&L Row 13 - Expense)
- Usually 7-10% of gross rent
- Deductible expense

### 3. Letting Fee (P&L Row 28 - Expense)
- One-time fee for finding tenant
- Usually 1 week rent + GST

### 4. Advertising (P&L Row 14 - Expense)
- TradeMe listings, signboards, etc.

### 5. Repairs/Maintenance (P&L Row 35 - Expense)
- Work arranged by PM
- Should have separate invoices

### 6. Inspection Fees (P&L Row 19 - Expense)
- Routine inspections
- Healthy homes assessments

### 7. Other Deductions
- Various property expenses paid by PM

## Output Format
```json
{{
  "pm_company": "Property Manager Name",
  "statement_period": {{
    "start": "YYYY-MM-DD",
    "end": "YYYY-MM-DD"
  }},
  "property_address": "Address from statement",

  "income": {{
    "gross_rent": {{
      "amount": 2400.00,
      "description": "Rent collected",
      "pl_row": 6
    }},
    "other_income": []
  }},

  "deductions": {{
    "management_fee": {{
      "amount": 192.00,
      "percentage": 8.0,
      "pl_row": 13
    }},
    "letting_fee": {{
      "amount": 650.00,
      "pl_row": 28,
      "note": "One week rent + GST"
    }},
    "advertising": {{
      "amount": 150.00,
      "pl_row": 14
    }},
    "repairs": [
      {{
        "date": "YYYY-MM-DD",
        "description": "Plumber - blocked drain",
        "amount": 250.00,
        "pl_row": 35
      }}
    ],
    "inspections": {{
      "amount": 50.00,
      "pl_row": 19
    }},
    "other": []
  }},

  "net_payment": {{
    "amount": 1108.00,
    "payment_date": "YYYY-MM-DD",
    "note": "This should match bank statement deposit"
  }},

  "reconciliation": {{
    "gross_income": 2400.00,
    "total_deductions": 1292.00,
    "calculated_net": 1108.00,
    "matches_stated_net": true
  }},

  "transactions_for_import": [
    {{
      "date": "YYYY-MM-DD",
      "description": "PM Statement - Gross Rent",
      "amount": 2400.00,
      "category": "rental_income",
      "pl_row": 6,
      "source": "pm_statement"
    }},
    {{
      "date": "YYYY-MM-DD",
      "description": "PM Statement - Management Fee",
      "amount": -192.00,
      "category": "agent_fees",
      "pl_row": 13,
      "source": "pm_statement"
    }}
  ],

  "warnings": []
}}
```

## Important Notes

1. **Cross-Reference with Bank Statement**
   - The NET payment from PM should appear as a deposit in bank statement
   - Don't double-count: if bank statement shows gross rent deposits, PM statement provides breakdown
   - If bank statement shows PM net deposits, use PM statement for gross rent figure

2. **GST Treatment**
   - Most landlords are NOT GST registered
   - PM fees usually include GST
   - Keep amounts GST-inclusive unless client is GST registered

3. **Multiple Statements**
   - PM may issue monthly statements
   - Combine all statements for the tax year period
   - Watch for year-end splits

4. **Repairs Through PM**
   - These should have supporting invoices
   - Flag any large repairs (>$500) for review
   - Distinguish repairs from capital improvements
"""