# Test Sample Data

This directory contains sample data files for testing the Property Tax Agent system.

## Directory Structure

```
sample_data/
├── bank_statements/
│   ├── anz_sample.csv          # ANZ bank statement format
│   ├── asb_sample.csv          # ASB bank statement format
│   ├── bnz_sample.csv          # BNZ bank statement format
│   └── westpac_sample.csv      # Westpac bank statement format
├── documents/
│   ├── pm_statement.pdf        # Sample property management statement
│   ├── invoice.pdf             # Sample invoice
│   └── receipt.pdf             # Sample receipt
├── expected_outputs/
│   ├── categorized_transactions.json
│   ├── tax_summary.json
│   └── workbook_structure.json
└── test_configs/
    ├── property_residential.json
    ├── property_mixed_use.json
    └── property_new_build.json
```

## Bank Statement Formats

### ANZ Format
```csv
Date,Description,Amount
01/04/2024,"MORTGAGE PAYMENT - 123 MAIN ST",-2500.00
05/04/2024,"RENTAL INCOME - TENANT SMITH",850.00
```

### ASB Format
```csv
TransactionDate,PayeeName,DebitAmount,CreditAmount,Balance
2024-04-01,MORTGAGE PAYMENT,2500.00,,15000.00
2024-04-05,RENT RECEIVED,,850.00,15850.00
```

### BNZ Format
```csv
Date,Transaction,Debit,Credit,Balance
01/04/2024,Loan Payment,2500.00,,10000.00
05/04/2024,Rent Deposit,,850.00,10850.00
```

### Westpac Format
```csv
Date,Details,Debit,Credit,Balance
01-Apr-24,HOME LOAN PAYMENT,2500.00,,20000.00
05-Apr-24,RENT INCOME,,850.00,20850.00
```

## Test Categories

### Income Transactions
- Rental income
- Bond refunds
- Insurance settlements
- Property sale proceeds

### Expense Transactions
- Mortgage interest
- Property management fees
- Insurance premiums
- Rates
- Repairs & maintenance
- Utilities
- Legal fees
- Accounting fees

### Edge Cases
- Negative amounts (refunds)
- Zero amounts
- Foreign currency
- Special characters in descriptions
- Missing data fields
- Duplicate transactions

## Property Types for Testing

### Residential Rental (Pre-2021)
- 100% interest deductibility
- Standard expense categories

### Mixed Use Property
- FY24: 50% interest deductibility
- FY25: 75% interest deductibility
- FY26+: 100% interest deductibility

### New Build (Post-2020)
- 100% interest deductibility
- Eligible for 10-year bright-line test

### Short-Term Rental
- Business expense rules apply
- GST considerations

## Expected Outputs

### Categorized Transactions
```json
{
  "transactions": [
    {
      "date": "2024-04-01",
      "description": "MORTGAGE PAYMENT - 123 MAIN ST",
      "amount": -2500.00,
      "category": "Interest",
      "subcategory": "Mortgage Interest",
      "pl_row": "Interest on money borrowed for the rental",
      "deductible_amount": -2500.00,
      "deductibility_percentage": 100,
      "gst_inclusive": false
    }
  ]
}
```

### Tax Summary
```json
{
  "fiscal_year": "FY24",
  "total_income": 10200.00,
  "total_expenses": 8500.00,
  "net_profit": 1700.00,
  "deductible_interest": 6000.00,
  "non_deductible_interest": 0.00
}
```

## Usage

### Running Tests with Sample Data
```bash
# Run all tests with sample data
pytest tests/ --sample-data

# Run specific test with sample data
pytest tests/test_transaction_extractor.py::test_parse_anz_statement

# Generate test report
pytest tests/ --html=reports/test_report.html
```

### Creating New Sample Data
1. Export real bank statements (remove sensitive data)
2. Create minimal examples (5-10 transactions)
3. Include variety of transaction types
4. Add edge cases
5. Document expected categorizations

## Data Privacy
- All sample data is synthetic
- No real personal or financial information
- Payee names are generic
- Amounts are rounded
- Dates are standardized

## Validation
Sample data should be validated against:
1. Bank parser configurations in `app/rules/bank_parsers.yaml`
2. Categorization rules in `app/rules/categorization.yaml`
3. Tax rules in database seed data
4. P&L row mappings in database

## Contributing Test Data
When adding new sample data:
1. Follow existing format conventions
2. Include both valid and invalid examples
3. Document expected behavior
4. Add corresponding test cases
5. Update this README