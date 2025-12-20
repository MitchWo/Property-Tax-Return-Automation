"""Settlement statement extraction prompts."""

SETTLEMENT_STATEMENT_EXTRACTION_PROMPT = """You are extracting financial data from a New Zealand property settlement statement for a rental property tax return.

## Property Context
- Property Address: {property_address}
- Tax Year: {tax_year}
- This is Year 1 (first year of ownership)

## CRITICAL INFORMATION TO EXTRACT

Settlement statements contain crucial Year 1 adjustments that affect the tax return:

### 1. Basic Settlement Details
- Settlement date (determines ownership period for pro-rating)
- Purchase price
- Deposit paid
- Vendor and purchaser names

### 2. Rates Apportionment (P&L Row 34)
Look for:
- "Rates" or "Council Rates" in the apportionments section
- Usually shows as a credit to purchaser (you're reimbursing vendor)
- May also show "Vendor Credit" which REDUCES your rates expense

### 3. Body Corporate Pro-rata (P&L Row 16)
Look for:
- "Body Corporate" or "BC Levy"
- Operating Fund contribution only
- Capital/Reserve fund is NOT deductible

### 4. Resident Society (P&L Row 36)
- Separate from Body Corporate
- Sometimes called "Residents Association"

### 5. Legal Fees (P&L Row 27)
- Solicitor/lawyer fees for the purchase
- Usually listed at the end of statement

### 6. Interest on Deposit (Special)
- If deposit earned interest while held in trust
- This NETS AGAINST your interest expense

## Output Format
```json
{{
  "settlement_details": {{
    "settlement_date": "YYYY-MM-DD",
    "purchase_price": 850000.00,
    "deposit": 85000.00,
    "vendor_name": "Name",
    "purchaser_name": "Name",
    "solicitor": "Law firm name",
    "property_address": "Full address from document"
  }},
  "apportionments": {{
    "rates": {{
      "amount": 1234.56,
      "description": "Council rates apportionment",
      "period": "01/07/2024 to settlement",
      "pl_row": 34
    }},
    "rates_vendor_credit": {{
      "amount": -500.00,
      "description": "Vendor credit for rates paid in advance",
      "pl_row": 34,
      "note": "Subtract from total rates"
    }},
    "body_corporate": {{
      "operating_fund": 456.78,
      "reserve_fund": 200.00,
      "total": 656.78,
      "deductible": 456.78,
      "pl_row": 16,
      "note": "Only operating fund is deductible"
    }},
    "resident_society": {{
      "amount": 150.00,
      "pl_row": 36
    }},
    "water_rates": {{
      "amount": 234.56,
      "pl_row": 41
    }}
  }},
  "other_items": {{
    "legal_fees": {{
      "amount": 1500.00,
      "pl_row": 27
    }},
    "interest_on_deposit": {{
      "amount": 45.67,
      "note": "Net against interest expense",
      "pl_row": 26
    }}
  }},
  "calculated_totals": {{
    "rates_for_pl": 734.56,
    "calculation": "1234.56 (apportionment) - 500.00 (vendor credit) = 734.56"
  }},
  "verification": {{
    "address_matches_context": true,
    "settlement_within_tax_year": true,
    "months_of_ownership": 8
  }},
  "warnings": []
}}
```

## Common Items to Look For

| Line Item | Category | P&L Row | Notes |
|-----------|----------|---------|-------|
| Council Rates | rates | 34 | Add to bank statement rates |
| Vendor Credit Rates | rates | 34 | SUBTRACT from rates |
| Body Corporate - Operating | body_corporate | 16 | Deductible |
| Body Corporate - Reserve | N/A | N/A | NOT deductible |
| Resident Society | resident_society | 36 | Separate category |
| Water Rates | water_rates | 41 | |
| Legal Fees | legal_fees | 27 | |
| Interest on Deposit | interest | 26 | Reduces interest expense |

## Address Verification

Compare the property address in the settlement statement against: {property_address}

If they don't match, set address_matches_context: false and add a warning.
"""