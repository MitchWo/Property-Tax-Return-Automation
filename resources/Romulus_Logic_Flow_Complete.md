d
# Romulus Logic Flow: NZ Rental Property Tax Return

## Complete Decision Tree & Processing Logic

**Version:** 1.0  
**Date:** December 2025  
**Purpose:** Documents the reasoning architecture and decision trees used when processing NZ rental property tax returns (IR3R)

---

# TABLE OF CONTENTS

1. [Initial Triage](#1-initial-triage)
2. [Client Classification](#2-client-classification)
3. [Phase 1: Document Review](#3-phase-1-document-review)
4. [Phase 2: Interest Calculation (Detailed)](#4-phase-2-interest-calculation)
5. [Phase 3: Expense Coding (Detailed)](#5-phase-3-expense-coding)
6. [Phase 4: P&L Completion](#6-phase-4-pl-completion)
7. [Phase 5: QA Review (Detailed)](#7-phase-5-qa-review)
8. [Decision Tree Summary](#8-decision-tree-summary)

---

# 1. INITIAL TRIAGE

```
START
  │
  ▼
┌─────────────────────────────────────────┐
│  What phase has been requested?          │
└─────────────────────────────────────────┘
  │
  ├─► Explicit phase trigger → Execute that phase only
  │
  ├─► "FULL PROCESSING" → Execute Phases 1-4 sequentially
  │
  └─► No phase specified → ASK user which phase
```

---

# 2. CLIENT CLASSIFICATION

Before any processing, classify the client to determine which rules apply:

## 2.1 Year 1 Determination

```
┌─────────────────────────────────────────┐
│  QUESTION 1: Is this Year 1?             │
│  (Property purchased during this FY)     │
└─────────────────────────────────────────┘
  │
  ├─► YES → Settlement statement is MANDATORY
  │         Rates calculation includes apportionment
  │         Check for all one-off compliance costs
  │         Depreciation may need pro-rating
  │
  └─► NO  → Settlement statement not needed
            Rates = instalments paid only
            Skip Year 1 compliance cost search
```

## 2.2 Property Type

```
┌─────────────────────────────────────────┐
│  QUESTION 2: Property type?              │
└─────────────────────────────────────────┘
  │
  ├─► NEW BUILD (CCC ≥ 27/03/2020)
  │     → Interest = 100% deductible
  │     → Need CCC as evidence
  │
  └─► EXISTING (CCC < 27/03/2020)
        → Interest = 80% deductible (FY25)
        → 100% from 1 April 2025
```

## 2.3 GST Registration Status

```
┌─────────────────────────────────────────┐
│  QUESTION 3: GST registered?             │
└─────────────────────────────────────────┘
  │
  ├─► YES → Use GST-EXCLUSIVE amounts
  │         GST workings are authoritative
  │         Check for no-GST income (travel agents)
  │         FFE contributions = capital (exclude)
  │
  └─► NO  → Use GST-INCLUSIVE amounts
            Do NOT divide by 1.15
            GST is a cost to the taxpayer
```

## 2.4 Property Management Type

```
┌─────────────────────────────────────────┐
│  QUESTION 4: Property management type?   │
└─────────────────────────────────────────┘
  │
  ├─► LONG-TERM (traditional PM)
  │     → Standard rental income treatment
  │     → Agent fees = PM + letting
  │
  ├─► SHORT-TERM (hotel/Airbnb managed)
  │     → Check GST workings for income classification
  │     → May have "no GST" income streams
  │     → Check for Daily Service expenses
  │     → Travel agent commission = no GST
  │
  └─► SELF-MANAGED
        → All income via bank deposits
        → Check for personal expense claims
```

---

# 3. PHASE 1: DOCUMENT REVIEW

## 3.1 Scope Boundaries

- ✓ Inventory documents provided
- ✓ Identify missing documents
- ✓ Confirm client details
- ✓ Flag special items
- ✗ Do NOT process any transactions
- ✗ Do NOT create workbook
- ✗ Do NOT calculate anything

## 3.2 Document Inventory Logic

```
┌─────────────────────────────────────────┐
│  DOCUMENT INVENTORY                      │
└─────────────────────────────────────────┘
  │
  ▼
FOR EACH document provided:
  │
  ├─► Identify document type by CONTENT, not filename
  │     • Bank statement → look for transaction format
  │     • Loan statement → look for interest charges
  │     • Settlement statement → look for apportionments
  │     • PM statement → look for management fees
  │     • Invoice → look for supplier details
  │
  └─► Note what period it covers
      Note any gaps or missing months
```

## 3.3 Completeness Check

```
┌─────────────────────────────────────────┐
│  COMPLETENESS CHECK                      │
└─────────────────────────────────────────┘
  │
  ▼
ALWAYS REQUIRED:
  ├─► Bank statement? ─────► Missing = STOP
  └─► Loan statements? ────► Missing = STOP

IF Year 1:
  ├─► Settlement statement? ────► Missing = CRITICAL GAP
  │     (Cannot calculate rates correctly without it)
  │
  ├─► Depreciation schedule? ───► Missing = flag, optional
  │
  └─► Compliance docs? ──────────► Actively search for:
        □ LIM          □ Property valuation (Valocity)
        □ Meth test    □ Depreciation schedule (Valuit)
        □ Healthy homes □ Smoke alarm

IF Property Manager:
  └─► PM year-end statement? ───► Missing = income uncertainty

IF Body Corporate:
  └─► BC invoice? ─────────────► Missing = cannot verify operating vs reserve
```

## 3.4 Special Items Flag

```
┌─────────────────────────────────────────┐
│  SPECIAL ITEMS FLAG                      │
└─────────────────────────────────────────┘
  │
  ▼
SCAN for:
  ├─► Personal expenditure claims document
  │     (Home office, mileage, mobile)
  │
  ├─► Bank contribution/cashback letters
  │     (Taxable income)
  │
  ├─► Insurance invoices
  │     (Only LANDLORD insurance is deductible)
  │
  └─► "Bond + Rent" transactions
        (Must confirm split with client)

OUTPUT: Checklist with status indicators
        Flag any blockers before proceeding
```

---

# 4. PHASE 2: INTEREST CALCULATION

## 4.1 Entry Conditions

```
┌─────────────────────────────────────────┐
│  PRE-REQUISITES CHECK                    │
└─────────────────────────────────────────┘
  │
  ├─► Do I have bank statement(s)?
  │     NO  → STOP. Cannot proceed without bank statement.
  │     YES → Continue
  │
  ├─► Do I have loan account statement(s)?
  │     NO  → Flag as secondary source missing, but can proceed
  │           (Bank statement is primary anyway)
  │     YES → Will use for cross-reference only
  │
  ├─► How many loan accounts exist?
  │     → Count distinct loan account numbers
  │     → Each needs separate tracking
  │
  └─► Do I know the property type?
        NEW BUILD → 100% deductible
        EXISTING  → 80% deductible (FY25)
        UNKNOWN   → ASK before proceeding
```

## 4.2 Source Document Hierarchy

```
┌─────────────────────────────────────────┐
│  WHY BANK STATEMENT IS PRIMARY           │
└─────────────────────────────────────────┘

BANK STATEMENT shows:
  ├─► Every interest debit as it happens
  ├─► Interest redirected FROM loan accounts TO everyday account
  ├─► Actual dates of each charge
  └─► Complete transaction-level detail

LOAN STATEMENT shows:
  ├─► Summary/closing balance view
  ├─► May aggregate multiple charges
  ├─► "Interest Charged" line may be a period total
  └─► Can mislead if read as individual transactions

DECISION RULE:
  │
  └─► ALWAYS extract interest from BANK STATEMENT
      Use loan statement ONLY to:
        • Cross-reference totals
        • Identify loan account numbers
        • Understand loan structure (offset, revolving, etc.)
```

## 4.3 Interest Transaction Identification

```
┌─────────────────────────────────────────┐
│  FINDING INTEREST TRANSACTIONS           │
└─────────────────────────────────────────┘

STEP 1: Filter/search bank statement for interest-related terms
  │
  Search terms (in order of reliability):
  ├─► "Debit Interest"
  ├─► "Loan Interest"
  ├─► "Interest Charged"
  ├─► "Interest"
  └─► Loan account number (as reference)

STEP 2: For each potential interest transaction, verify:
  │
  ├─► Is it a DEBIT (money going out)?
  │     YES → Likely genuine interest charge
  │     NO (credit) → This is interest RECEIVED or adjustment
  │                   → Do NOT include in expense calculation
  │
  ├─► Does description reference a loan account?
  │     YES → Assign to that loan account
  │     NO  → Check if it's the only loan (assign to Loan 1)
  │           Or flag for clarification if multiple loans
  │
  └─► Is the amount reasonable?
        Compare to expected range based on loan size
        Unusually large → May be capitalised interest or error
        Unusually small → May be offset account effect
```

## 4.4 Exclusion Rules

```
┌─────────────────────────────────────────┐
│  EXCLUSION RULES                         │
└─────────────────────────────────────────┘

DO NOT include in interest expense:
  │
  ├─► Interest CREDITS (refunds, adjustments)
  │     These reduce what the bank owes, not what taxpayer pays
  │
  ├─► "Interest Adjustment" entries
  │     Often backdated corrections — already reflected in charges
  │
  ├─► "OFFSET Benefit" entries
  │     These show interest SAVED, not interest CHARGED
  │
  ├─► Interest on savings/deposit accounts
  │     This is INCOME, not expense
  │     (Unless it's interest on deposit from settlement — see Year 1 rules)
  │
  └─► Capitalised interest (added to loan principal)
        This is captured in future interest charges
        Including it would double-count
```

## 4.5 Frequency Analysis

```
┌─────────────────────────────────────────┐
│  DETERMINING INTEREST FREQUENCY          │
└─────────────────────────────────────────┘

STEP 1: Count total interest transactions for the year
  │
  ├─► ~24-26 transactions → BI-WEEKLY charging
  │     (Every ~14 days, some months get 3 charges)
  │
  ├─► ~12-13 transactions → MONTHLY charging
  │     (Once per month, verify dates ~30 days apart)
  │
  ├─► ~52 transactions → WEEKLY charging (rare)
  │
  └─► Irregular count → Mixed frequency or partial year
        Investigate further

STEP 2: Verify pattern by checking dates
  │
  FOR bi-weekly pattern:
  │   ├─► Charges should be ~14 days apart
  │   ├─► October, January, April, July often have 3 charges
  │   └─► Other months typically have 2 charges
  │
  FOR monthly pattern:
  │   ├─► Charges should be ~30 days apart
  │   ├─► Usually on same day each month (1st, 15th, 20th, etc.)
  │   └─► Each month has exactly 1 charge

STEP 3: Document the frequency finding
  │
  └─► This informs the monthly breakdown accuracy
      Bi-weekly: Some months will have higher totals (3 charges)
      Monthly: Each month should be relatively consistent
```

## 4.6 Monthly Breakdown Construction

```
┌─────────────────────────────────────────┐
│  ASSIGNING CHARGES TO MONTHS             │
└─────────────────────────────────────────┘

FOR EACH interest transaction:
  │
  ├─► Extract the transaction DATE
  │
  ├─► Assign to calendar month based on date:
  │     Apr 2024: 1 Apr - 30 Apr
  │     May 2024: 1 May - 31 May
  │     ... etc ...
  │     Mar 2025: 1 Mar - 31 Mar
  │
  └─► Add amount to that month's total for that loan

EDGE CASE: Transaction on month boundary
  │
  └─► Use the actual transaction date
      If charged on 1 May, it goes to May (not April)
      Banks are consistent — trust the date

┌─────────────────────────────────────────┐
│  BUILDING THE MONTHLY SUMMARY            │
└─────────────────────────────────────────┘

CREATE structure:
  │
  │  Month    │ Loan 1  │ Loan 2  │ Loan 3  │ Total   │
  │───────────│─────────│─────────│─────────│─────────│
  │  Apr-24   │ $XXX.XX │ $XXX.XX │    -    │ $XXX.XX │
  │  May-24   │ $XXX.XX │ $XXX.XX │    -    │ $XXX.XX │
  │  ...      │         │         │         │         │
  │  Mar-25   │ $XXX.XX │ $XXX.XX │    -    │ $XXX.XX │
  │───────────│─────────│─────────│─────────│─────────│
  │  TOTAL    │ $X,XXX  │ $X,XXX  │    -    │ $X,XXX  │

VALIDATION CHECK:
  │
  ├─► Sum of monthly totals = Sum of all transactions?
  │     YES → Proceed
  │     NO  → Something was missed or double-counted — investigate
  │
  └─► Any month showing $0 unexpectedly?
        → Check if loan was drawn mid-year
        → Check if offset fully covered interest
        → Flag for verification if unclear
```

## 4.7 Offset Account Handling

```
┌─────────────────────────────────────────┐
│  OFFSET ACCOUNT DETECTION                │
└─────────────────────────────────────────┘

INDICATORS of offset account:
  │
  ├─► Loan statement shows "OFFSET" entries
  │
  ├─► Interest charges are much lower than expected
  │     (e.g., $500k loan but only $2k interest/year)
  │
  ├─► Bank statement shows "OFFSET Benefit" credits
  │
  └─► Loan product name includes "Offset" or "100%"

┌─────────────────────────────────────────┐
│  OFFSET ACCOUNT LOGIC                    │
└─────────────────────────────────────────┘

HOW OFFSET WORKS:
  │
  Loan Balance: $500,000
  Offset Account Balance: $480,000
  ─────────────────────────────────────
  Interest Charged On: $20,000 (difference only)
  │
  └─► This is CORRECT — taxpayer genuinely pays less interest

DECISION RULE:
  │
  IF interest appears unusually low:
  │
  ├─► Step 1: Check for offset account indicators
  │
  ├─► Step 2: If offset exists:
  │     → The low interest is CORRECT
  │     → DO NOT flag as an error
  │     → DO NOT try to "calculate" what interest "should" be
  │     → Sum only actual interest CHARGED
  │
  └─► Step 3: Document in notes:
        "Offset account in use — interest reduced accordingly"

WHAT TO SUM:
  │
  ├─► INCLUDE: "LOAN INTEREST" debits (actual interest charged)
  │
  └─► EXCLUDE: "OFFSET Benefit" entries (these show savings, not charges)
```

## 4.8 Year 1: Interest on Deposit

```
┌─────────────────────────────────────────┐
│  INTEREST ON DEPOSIT (Settlement)        │
└─────────────────────────────────────────┘

WHAT IS IT?
  │
  └─► When property is purchased, buyer pays deposit to solicitor
      Solicitor holds deposit in trust account pending settlement
      Interest earned on deposit belongs to the buyer
      This interest is shown on the settlement statement

TAX TREATMENT:
  │
  ├─► This is technically INCOME
  │
  └─► BUT: Correct treatment is to NET against Interest Expense
           NOT to show as separate "Other Income"
           
           Why? IRD accepts netting for simplicity
           and the amounts are usually small

LOGIC FLOW:
  │
  IF Year 1 property:
  │
  ├─► Step 1: Extract settlement statement
  │
  ├─► Step 2: Search for "Interest on Deposit" or similar
  │     Usually in the income/credits section
  │     May be labelled "Interest earned" or "Deposit interest"
  │
  ├─► Step 3: Note the amount
  │
  └─► Step 4: In Phase 4, SUBTRACT from Interest Expense
        
        Interest Expense = Gross Interest Charged − Interest on Deposit

DOCUMENTATION:
  │
  └─► Create clear calculation note:
        "Gross interest: $12,500
         Less: Interest on deposit (SS): ($156)
         Net interest expense: $12,344"
```

## 4.9 Deductibility Calculation

```
┌─────────────────────────────────────────┐
│  APPLYING DEDUCTIBILITY PERCENTAGE       │
└─────────────────────────────────────────┘

DETERMINE property type:
  │
  ├─► NEW BUILD (CCC issued on/after 27 March 2020)
  │     Deductibility = 100%
  │     Evidence required: Code Compliance Certificate
  │
  └─► EXISTING (CCC issued before 27 March 2020)
        FY25 (1 Apr 2024 - 31 Mar 2025): Deductibility = 80%
        From 1 Apr 2025: Deductibility = 100%

CALCULATION:
  │
  Gross Interest Charged (from bank statement)
  × Deductibility Percentage
  = Deductible Interest Expense
  │
  └─► Example (Existing property, FY25):
        Gross interest: $15,000
        × 80%
        = $12,000 deductible

DOCUMENTATION for IR3R:
  │
  ├─► Box 7A: Interest incurred = GROSS amount
  │
  ├─► Box 7B: Interest claimed = DEDUCTIBLE amount
  │
  └─► Box 7C: Tick reason = "New build" or applicable category
```

## 4.10 Phase 2 Output Requirements

```
┌─────────────────────────────────────────┐
│  DELIVERABLES                            │
└─────────────────────────────────────────┘

OUTPUT 1: Interest Summary Table
  │
  │ Loan Account │ Gross Interest │ Deductible % │ Deductible Amt │
  │──────────────│────────────────│──────────────│────────────────│
  │ Loan 1 (XXX) │    $X,XXX.XX   │    100%      │   $X,XXX.XX    │
  │ Loan 2 (XXX) │    $X,XXX.XX   │    100%      │   $X,XXX.XX    │
  │──────────────│────────────────│──────────────│────────────────│
  │ TOTAL        │    $X,XXX.XX   │              │   $X,XXX.XX    │

OUTPUT 2: Monthly Breakdown (for P&L rows 32-48)
  │
  │ Month  │ Loan 1  │ Loan 2  │ Total   │
  │────────│─────────│─────────│─────────│
  │ Apr-24 │ $XXX.XX │ $XXX.XX │ $XXX.XX │
  │ May-24 │ $XXX.XX │ $XXX.XX │ $XXX.XX │
  │ ...    │         │         │         │
  │ Mar-25 │ $XXX.XX │ $XXX.XX │ $XXX.XX │
  │────────│─────────│─────────│─────────│
  │ TOTAL  │ $X,XXX  │ $X,XXX  │ $X,XXX  │

OUTPUT 3: Notes
  │
  ├─► Interest frequency: [Bi-weekly / Monthly]
  ├─► Number of transactions: [X]
  ├─► Offset account: [Yes / No]
  ├─► Interest on deposit (Year 1): [$X.XX]
  └─► Any anomalies noted

OUTPUT 4: Ready status
  │
  └─► "Ready for Phase 3" or "Blocked: [reason]"
```

---

# 5. PHASE 3: EXPENSE CODING

## 5.1 Entry Conditions

```
┌─────────────────────────────────────────┐
│  PRE-REQUISITES CHECK                    │
└─────────────────────────────────────────┘
  │
  ├─► Phase 2 complete?
  │     NO  → Interest calculation needed first
  │     YES → I have the interest figures to use
  │
  ├─► Bank statement available?
  │     NO  → STOP. Cannot code transactions without source.
  │     YES → Continue
  │
  ├─► Is this Year 1?
  │     YES → Settlement statement is MANDATORY
  │           Do I have it?
  │             NO  → STOP. Cannot proceed without settlement statement.
  │             YES → Continue
  │     NO  → Settlement statement not required
  │
  └─► GST registration status known?
        YES → I know whether to use inclusive/exclusive amounts
        NO  → ASK before proceeding
```

## 5.2 Workbook Creation

```
┌─────────────────────────────────────────┐
│  TAB STRUCTURE SETUP                     │
└─────────────────────────────────────────┘

CREATE these tabs in workbook:
  │
  ├─► P&L
  │     → Copy template structure
  │     → Leave ALL VALUES BLANK
  │     → Only structure/headers present
  │
  ├─► Rental BS
  │     → Will contain all bank statement transactions
  │     → Columns: Date | Description | Other Party | Credit | Debit | Balance | Category | P&L Row
  │     → Add SUMIF formulas at bottom for each category
  │
  ├─► Loan Ac 1 (and 2, 3, 4 as needed)
  │     → Will contain interest transactions for that loan
  │     → Columns: Date | Description | Interest Amount | Month
  │     → Add monthly summary section at bottom
  │
  ├─► Settlement (Year 1 only)
  │     → Will contain key figures from settlement statement
  │     → Rates calculation
  │     → Legal fees
  │     → Interest on deposit
  │
  ├─► Depreciation (Year 1 only)
  │     → Will contain chattels schedule
  │     → Pro-rata calculation
  │
  └─► PM Statements (if property manager)
        → Will contain invoice line items
        → Category assignment
        → SUMIF totals by category

┌─────────────────────────────────────────┐
│  DATA IMPORT                             │
└─────────────────────────────────────────┘

FOR Bank Statement → Rental BS tab:
  │
  ├─► Import ALL transactions for the full year
  │     (Not samples — complete transaction history)
  │
  ├─► Preserve original data in columns A-F
  │
  └─► Add coding columns G (Category) and H (P&L Row)

FOR Loan Statements → Loan Ac tabs:
  │
  ├─► Import ALL transactions
  │
  └─► Add Month column for grouping

FOR Settlement Statement → Settlement tab (Year 1):
  │
  └─► Extract key figures (see section 5.4 below)
```

## 5.3 Transaction Coding — Master Decision Tree

```
┌─────────────────────────────────────────┐
│  FOR EACH TRANSACTION IN BANK STATEMENT  │
└─────────────────────────────────────────┘

START
  │
  ▼
┌─────────────────────────────────────────┐
│  Is this a CREDIT or DEBIT?              │
└─────────────────────────────────────────┘
  │
  ├─► CREDIT (money IN) ────────────────────────────────────┐
  │                                                          │
  │   ┌──────────────────────────────────────────────────┐  │
  │   │  INCOME CLASSIFICATION                            │  │
  │   └──────────────────────────────────────────────────┘  │
  │     │                                                    │
  │     ├─► Description contains "RENT" or regular pattern?  │
  │     │     │                                              │
  │     │     ├─► Amount matches expected rent?              │
  │     │     │     YES → Rental Income (Row 6)              │
  │     │     │                                              │
  │     │     └─► Amount different from expected?            │
  │     │           Check for "BOND" in description          │
  │     │           │                                        │
  │     │           ├─► "BOND + RENT" or similar?            │
  │     │           │     → FLAG: Confirm split with client  │
  │     │           │     → Bond portion = NOT income        │
  │     │           │     → Rent portion = Row 6             │
  │     │           │                                        │
  │     │           └─► Pure bond payment?                   │
  │     │                 → Category: Bond Received          │
  │     │                 → P&L Row: "Exclude"               │
  │     │                 → Note: Not income                 │
  │     │                                                    │
  │     ├─► Description contains "WATER"?                    │
  │     │     → Water Recovered (Row 7)                      │
  │     │                                                    │
  │     ├─► Description contains "CASHBACK" / "CONTRIBUTION"?│
  │     │     → Bank Contribution (Row 8)                    │
  │     │     → Note: This IS taxable income                 │
  │     │                                                    │
  │     ├─► Transfer from personal account?                  │
  │     │     → Category: Funds Introduced                   │
  │     │     → P&L Row: "Exclude"                           │
  │     │     → Note: Not income, just funding               │
  │     │                                                    │
  │     ├─► Property Manager payment?                        │
  │     │     → Rental Income (Row 6)                        │
  │     │     → May need to gross up if PM fees deducted     │
  │     │                                                    │
  │     └─► Unknown credit?                                  │
  │           → FLAG for clarification                       │
  │           → Do not assume it's income                    │
  │                                                          │
  └─► DEBIT (money OUT) ────────────────────────────────────┐
                                                             │
      ┌──────────────────────────────────────────────────┐  │
      │  EXPENSE/OTHER CLASSIFICATION                     │  │
      └──────────────────────────────────────────────────┘  │
        │                                                    │
        ▼                                                    │
      [See detailed expense decision tree below]             │
```

## 5.3.1 Expense Classification Decision Tree

```
┌─────────────────────────────────────────┐
│  EXPENSE CLASSIFICATION                  │
└─────────────────────────────────────────┘

FOR EACH debit transaction:
  │
  ▼
┌─────────────────────────────────────────┐
│  STEP 1: Check for NON-DEDUCTIBLE first │
└─────────────────────────────────────────┘
  │
  ├─► Is this a LOAN REPAYMENT?
  │     Keywords: "Loan", "Principal", "Repayment", "Mortgage payment"
  │     BUT NOT: "Interest"
  │     │
  │     └─► YES → Category: Loan Repayment
  │               P&L Row: "Exclude"
  │               Reason: Principal is capital, not deductible
  │
  ├─► Is this INTEREST?
  │     Keywords: "Interest", "Debit Interest", "Loan Interest"
  │     │
  │     └─► YES → Category: Already processed (Phase 2)
  │               P&L Row: Row 25 (via Phase 2 output)
  │               DO NOT re-code here
  │
  ├─► Is this a DRAWING / personal transfer out?
  │     Keywords: "Transfer to [personal account]", "Drawing"
  │     │
  │     └─► YES → Category: Drawing
  │               P&L Row: "Exclude"
  │               Reason: Not an expense
  │
  └─► Is this PERSONAL insurance?
        Keywords: "Life insurance", "Health insurance", "Income protection"
        │
        └─► YES → Category: Personal Insurance
                  P&L Row: "Exclude"
                  Reason: Only LANDLORD insurance is deductible

┌─────────────────────────────────────────┐
│  STEP 2: Identify DEDUCTIBLE expenses   │
└─────────────────────────────────────────┘
  │
  ├─► RATES-RELATED?
  │     │
  │     ├─► "Council" / "[City] Council" / "Rates"?
  │     │     │
  │     │     └─► IF Year 1:
  │     │           → Category: Council Rates
  │     │           → P&L Row: "Pending SS calc"
  │     │           → Will calculate in Settlement tab
  │     │         ELSE:
  │     │           → Category: Council Rates
  │     │           → P&L Row: Row 34
  │     │
  │     └─► "Water" / "Watercare" / "Wellington Water"?
  │           → Category: Water Rates
  │           → P&L Row: Row 41
  │           → Use GST-inclusive amount (non-registered)
  │
  ├─► BODY CORPORATE / RESIDENT SOCIETY?
  │     │
  │     ├─► "Body Corporate" / "BC Levy" / "Unit title"?
  │     │     │
  │     │     └─► CHECK INVOICE for operating vs reserve split
  │     │         │
  │     │         ├─► Operating fund only?
  │     │         │     → Category: Body Corporate
  │     │         │     → P&L Row: Row 15
  │     │         │
  │     │         ├─► Reserve/sinking fund?
  │     │         │     → Category: BC Reserve Fund
  │     │         │     → P&L Row: "Exclude"
  │     │         │     → Reason: Capital contribution
  │     │         │
  │     │         └─► Mixed (both operating and reserve)?
  │     │               → SPLIT the transaction
  │     │               → Operating portion → Row 15
  │     │               → Reserve portion → "Exclude"
  │     │
  │     └─► "RSI" / "Resident Society" / "Laneway" / "Community"?
  │           → Category: Resident Society
  │           → P&L Row: Row 36
  │           → Note: SEPARATE from Body Corporate
  │
  ├─► PROPERTY MANAGEMENT?
  │     │
  │     ├─► Property manager company name?
  │     │     → Category: Agent Fees
  │     │     → P&L Row: Row 13
  │     │     → Includes: Management fees + Letting fees + GST
  │     │     → Does NOT include: Advertising (that's Row 12)
  │     │
  │     └─► "Advertising" / "Trade Me" / "Tenant find ad"?
  │           → Category: Advertising
  │           → P&L Row: Row 12
  │           → Note: SEPARATE from Agent Fees
  │
  ├─► BANK-RELATED?
  │     │
  │     ├─► "Bank fee" / "Account fee" / "Monthly fee"?
  │     │     → Category: Bank Fees
  │     │     → P&L Row: Row 14
  │     │
  │     └─► "Restructure fee" / "Variation fee"?
  │           → Category: Bank Fees
  │           → P&L Row: Row 14
  │           → Note: Deductible, not capital
  │
  ├─► REPAIRS / MAINTENANCE / COMPLIANCE?
  │     │
  │     ├─► Is it a COMPLIANCE cost? (Year 1 especially)
  │     │     │
  │     │     └─► "LIM" / "Land Information Memorandum"?
  │     │         "Meth test" / "Methamphetamine"?
  │     │         "Healthy homes" / "HH compliance"?
  │     │         "Smoke alarm" / "Fire safety"?
  │     │         "Valocity" / "Property valuation"?
  │     │         "Valuit" / "FordBaker" / "Depreciation schedule"?
  │     │           │
  │     │           └─► YES to any → Category: Due Diligence
  │     │                            P&L Row: Row 18
  │     │                            Note: Consolidate all compliance costs here
  │     │
  │     └─► Regular repair/maintenance?
  │           "Plumber" / "Electrician" / "Handyman"?
  │           "Repair" / "Fix" / "Replace [item]"?
  │             │
  │             └─► Is the amount > $1,000?
  │                   │
  │                   ├─► YES → Could be capital
  │                   │         What was done?
  │                   │         │
  │                   │         ├─► Repair existing item → Row 35
  │                   │         └─► New asset / improvement → "Exclude" (capital)
  │                   │
  │                   └─► NO → Category: Repairs & Maintenance
  │                            P&L Row: Row 35
  │
  ├─► INSURANCE?
  │     │
  │     └─► "Landlord insurance" / "Property insurance" / "Contents insurance"?
  │           → Category: Insurance
  │           → P&L Row: Row 24
  │           → Note: Must be PROPERTY insurance, not personal
  │
  ├─► LEGAL?
  │     │
  │     └─► Solicitor name / "Legal fees"?
  │           │
  │           └─► IF Year 1:
  │                 → Will extract from Settlement Statement
  │                 → Category: Legal
  │                 → P&L Row: Row 27
  │               ELSE:
  │                 → Category: Legal
  │                 → P&L Row: Row 27
  │                 → Note: Deductible if property always investment
  │
  └─► UNKNOWN?
        │
        └─► FLAG for clarification
            → Category: "Query"
            → P&L Row: "TBC"
            → Add to clarification list
```

## 5.4 Year 1: Settlement Statement Extraction

```
┌─────────────────────────────────────────┐
│  SETTLEMENT STATEMENT PROCESSING         │
└─────────────────────────────────────────┘

THIS IS MANDATORY FOR YEAR 1 — NEVER SKIP

STEP 1: Locate and extract settlement statement PDF
  │
  └─► Usually in "Settlement" or "Legal" folder
      May be named "Settlement Statement", "Statement of Settlement", 
      "Vendor's Statement", or similar

STEP 2: Read the document carefully — identify sections:
  │
  ├─► PURCHASE PRICE section
  │     Note: Purchase price is CAPITAL — not deductible
  │     Note: Deposit amount for reference
  │
  ├─► APPORTIONMENTS section ← CRITICAL
  │     │
  │     ├─► RATES
  │     │     Look for: "Rates", "Council rates"
  │     │     Find: "Purchaser's share" or "Apportionment"
  │     │     Note: There will ALWAYS be an apportionment (even if small)
  │     │     Note: May show "Vendor credit" to subtract
  │     │
  │     ├─► BODY CORPORATE
  │     │     Look for: "Body corporate levy", "BC operating"
  │     │     This is PRO-RATA from settlement to next levy date
  │     │     SEPARATE from annual invoice
  │     │
  │     └─► RESIDENT SOCIETY
  │           Look for: "RSI", "Resident society", "Laneway levy"
  │           Also pro-rata if applicable
  │
  ├─► LEGAL FEES section
  │     Look for: Solicitor fees breakdown
  │     Deductible if property always intended as investment
  │
  └─► INCOME/CREDITS section
        Look for: "Interest on deposit" / "Interest earned"
        This gets NETTED against Interest Expense

STEP 3: Populate Settlement tab with extracted figures
```

## 5.5 Rates Calculation (Year 1)

```
┌─────────────────────────────────────────┐
│  YEAR 1 RATES CALCULATION                │
└─────────────────────────────────────────┘

FORMULA:
  Rates Deduction = Settlement Apportionment
                  + Instalments Paid During Year
                  − Vendor Credit Received

EXAMPLE:
  │
  Settlement statement shows:
    Rates apportionment (purchaser's share): $412.50
    Vendor credit received: $0
  │
  Bank statement shows:
    Rates instalment 1 (Aug): $850.00
    Rates instalment 2 (Nov): $850.00
    Rates instalment 3 (Feb): $850.00
  │
  CALCULATION:
    $412.50 + $850.00 + $850.00 + $850.00 − $0 = $2,962.50

DOCUMENT in Settlement tab:
  │
  │ Item                        │ Amount    │ Source │
  │─────────────────────────────│───────────│────────│
  │ Rates - Settlement Apport.  │ $412.50   │ SS     │
  │ Rates - Instalment 1        │ $850.00   │ BS     │
  │ Rates - Instalment 2        │ $850.00   │ BS     │
  │ Rates - Instalment 3        │ $850.00   │ BS     │
  │ Rates - Vendor Credit       │ ($0.00)   │ SS     │
  │─────────────────────────────│───────────│────────│
  │ RATES - TOTAL DEDUCTIBLE    │ $2,962.50 │ Calc   │

COMMON MISTAKE TO AVOID:
  │
  └─► Do NOT just sum bank statement instalments
      The settlement apportionment is a valid deduction
      even if it's only a few dollars
```

## 5.6 Depreciation (Year 1)

```
┌─────────────────────────────────────────┐
│  DEPRECIATION CALCULATION                │
└─────────────────────────────────────────┘

IF depreciation schedule provided:
  │
  STEP 1: Extract chattels and rates from schedule
    │
    │ Asset               │ Book Value │ DV Rate │
    │─────────────────────│────────────│─────────│
    │ Carpet              │ $5,000     │ 40%     │
    │ Drapes/Blinds       │ $2,000     │ 20%     │
    │ Dishwasher          │ $800       │ 20%     │
    │ Heat pump           │ $3,500     │ 20%     │
    │ ...                 │            │         │
  │
  STEP 2: Calculate full year depreciation
    │
    │ Asset               │ Book Value │ Rate │ Full Year Dep │
    │─────────────────────│────────────│──────│───────────────│
    │ Carpet              │ $5,000     │ 40%  │ $2,000        │
    │ Drapes/Blinds       │ $2,000     │ 20%  │ $400          │
    │ ...                 │            │      │               │
    │─────────────────────│────────────│──────│───────────────│
    │ TOTAL               │            │      │ $5,104        │
  │
  STEP 3: PRO-RATE if partial year
    │
    └─► How many months was property rented?
        │
        Example: Purchased 1 May, rented from 15 May
        → 11 months rented (May-Mar)
        → Pro-rata factor = 11/12
        │
        Full year: $5,104
        Pro-rated: $5,104 × (11/12) = $4,679

DOCUMENT in Depreciation tab:
  │
  │ Full Year Depreciation │ $5,104     │
  │ Months Rented          │ 11         │
  │ Pro-rata Factor        │ 11/12      │
  │ DEDUCTIBLE AMOUNT      │ $4,679     │ ← Links to P&L Row 17
```

## 5.7 GST Treatment

```
┌─────────────────────────────────────────┐
│  GST DECISION TREE                       │
└─────────────────────────────────────────┘

FIRST: Confirm GST registration status
  │
  ├─► NON-GST REGISTERED (most landlords)
  │     │
  │     └─► Use GST-INCLUSIVE amounts everywhere
  │         DO NOT divide by 1.15
  │         The GST is a COST to the taxpayer
  │         │
  │         Example: Water rates invoice $115.00 (incl GST)
  │         → Use $115.00 in P&L
  │         → Do NOT use $100.00
  │
  └─► GST-REGISTERED
        │
        └─► Use GST-EXCLUSIVE amounts
            The taxpayer claims GST back via GST returns
            │
            Example: Water rates invoice $115.00 (incl GST)
            → Use $100.00 in P&L (ex-GST)
            → The $15.00 GST is claimed separately

┌─────────────────────────────────────────┐
│  SPECIAL CASES (GST-Registered)          │
└─────────────────────────────────────────┘

IF GST-registered AND short-term/hotel managed:
  │
  ├─► INCOME may be largely "no GST"
  │     │
  │     └─► Check GST workings
  │         Travel agent bookings often have no GST
  │         DO NOT divide all income by 1.15
  │         Use GST workings as authoritative source
  │
  ├─► TRAVEL AGENT COMMISSION
  │     │
  │     └─► This has NO GST
  │         Use full amount — don't divide by 1.15
  │
  └─► FFE CONTRIBUTIONS
        │
        └─► These are CAPITAL
            NOT deductible
            Exclude from expenses entirely
```

## 5.8 Phase 3 Output Requirements

```
┌─────────────────────────────────────────┐
│  DELIVERABLES                            │
└─────────────────────────────────────────┘

OUTPUT 1: Workbook with populated source tabs
  │
  ├─► P&L tab: Template structure only — NO VALUES
  │
  ├─► Rental BS tab:
  │     • ALL transactions imported
  │     • Category column completed
  │     • P&L Row column completed
  │     • SUMIF formulas at bottom for each category
  │
  ├─► Loan Ac tabs:
  │     • All interest transactions
  │     • Monthly summaries
  │
  ├─► Settlement tab (Year 1):
  │     • Rates calculation with all components
  │     • Legal fees
  │     • Interest on deposit
  │     • BC/RS pro-rata if applicable
  │
  ├─► Depreciation tab (Year 1):
  │     • Chattels schedule
  │     • Pro-rata calculation
  │
  └─► PM Statements tab (if applicable):
        • Invoice line items
        • Category assignments

OUTPUT 2: Coded transactions summary
  │
  │ Category          │ Amount    │ P&L Row │ Source │
  │───────────────────│───────────│─────────│────────│
  │ Rental Income     │ $XX,XXX   │ 6       │ BS     │
  │ Water Recovered   │ $XXX      │ 7       │ BS     │
  │ Agent Fees        │ $X,XXX    │ 13      │ PM     │
  │ Rates             │ $X,XXX    │ 34      │ SS/BS  │
  │ ...               │           │         │        │

OUTPUT 3: Items requiring clarification
  │
  └─► List of transactions that need client input
      • Bond/Rent splits
      • Unknown transactions
      • Potential capital items

OUTPUT 4: Excluded items summary
  │
  └─► List of non-deductible items with reasons
      • Loan repayments
      • Drawings
      • BC reserve fund
      • Personal insurance

OUTPUT 5: Ready status
  │
  └─► "Ready for Phase 4" or "Blocked: [awaiting clarification on X]"
```

---

# 6. PHASE 4: P&L COMPLETION

## 6.1 Scope Boundaries

- ✓ Use template `Rental_Property_Workbook_Template.xlsx`
- ✓ **Populate P&L with formulas linking to source tabs**
- ✓ Complete Interest Deductibility workings (rows 32-48, cols I-P)
- ✓ Complete PM Statements workings (rows 49-63, cols I-P)
- ✓ Add source references (Column D)
- ✓ Calculate totals
- ✗ Do NOT skip template
- ✗ Do NOT recreate P&L structure
- ✗ Do NOT use unexplained hardcoded numbers

## 6.2 Template Enforcement

```
┌─────────────────────────────────────────┐
│  TEMPLATE ENFORCEMENT                    │
└─────────────────────────────────────────┘
  │
  ▼
MUST use Rental_Property_Workbook_Template.xlsx
  │
  NEVER:
  ├─► Create new P&L structure from scratch
  ├─► Reorganise rows
  └─► Skip workings sections
```

## 6.3 Formula Linking Rule

```
┌─────────────────────────────────────────┐
│  FORMULA LINKING RULE                    │
└─────────────────────────────────────────┘

EVERY P&L value must be ONE of:
  │
  ├─► 1. FORMULA linking to source tab
  │       Example: ='Settlement'!B21
  │       Example: ='Rental BS'!D57
  │
  ├─► 2. CALCULATION formula
  │       Example: =SUM(B6:B8)
  │
  └─► 3. HARDCODED with SOURCE REFERENCE
          Example: $862.50 with "AF" in Column D
          (Accounting fees — known standard amount)

NEVER: Unexplained hardcoded numbers
```

## 6.4 P&L Population Sequence

```
┌─────────────────────────────────────────┐
│  P&L POPULATION SEQUENCE                 │
└─────────────────────────────────────────┘

STEP 1: INCOME SECTION (Rows 6-11)
  │
  ├─► Row 6 (Rental Income)
  │     = Link to Rental BS summary or PM total
  │
  ├─► Row 7 (Water Recovered)
  │     = Link to Rental BS summary
  │
  ├─► Row 8 (Bank Contribution)
  │     = Link to Rental BS or hardcode with source
  │
  └─► Row 11 (Total Income)
        = Formula: =SUM(B6:B10)

STEP 2: EXPENSES SECTION (Rows 12-42)
  │
  FOR EACH expense row:
    │
    ├─► Link to appropriate source tab
    │     • BS transactions → Rental BS SUMIF
    │     • Settlement items → Settlement tab cells
    │     • PM items → PM Statements tab
    │     • Depreciation → Depreciation tab pro-rata total
    │
    └─► Add source reference in Column D

  KEY ROWS:
  │
  ├─► Row 16 (Accounting)
  │     = $862.50 (hardcode OK with "AF" reference)
  │
  ├─► Row 17 (Depreciation)
  │     = Link to Depreciation tab pro-rata total
  │     MUST be pro-rated if partial year
  │
  ├─► Row 25 (Interest)
  │     = Phase 2 total × deductibility %
  │     MINUS interest on deposit (if Year 1)
  │
  └─► Row 34 (Rates)
        Year 1: = Settlement apportionment + Instalments − Vendor credit
        Year 2+: = Sum of instalments from BS

STEP 3: INTEREST DEDUCTIBILITY WORKINGS (Rows 32-48, Cols I-P)
  │
  ├─► Column I: Month (Apr-24 to Mar-25)
  ├─► Column J: Source (BS)
  ├─► Columns K-N: Interest per loan per month
  │     = Link to Loan Ac tab monthly summaries
  └─► Row 45-46: Totals

STEP 4: PM STATEMENTS WORKINGS (Rows 49-63, Cols I-P)
  │
  ├─► Column I: Month
  ├─► Column J: Source (BS/PM)
  ├─► Column K: Rental Income per month
  ├─► Column L: Agent Fees per month
  └─► Row 63: Totals

STEP 5: TOTALS AND NET RESULT
  │
  ├─► Total Expenses = SUM of expense rows
  └─► Net Profit/(Loss) = Total Income − Total Expenses
```

## 6.5 Source Reference Codes

| Code | Source |
|:-----|:-------|
| BS | Bank Statement |
| SS | Settlement Statement |
| PM | Property Manager |
| INV | Invoice |
| CP | Client Provided |
| AF | Accounting Fees |

## 6.6 Phase 4 Output

```
OUTPUT: Completed P&L with all formulas
        All workings sections populated
        All figures traceable to source tabs
```

---

# 7. PHASE 5: QA REVIEW

## 7.1 Entry Conditions

```
┌─────────────────────────────────────────┐
│  PRE-REQUISITES CHECK                    │
└─────────────────────────────────────────┘
  │
  ├─► Phase 4 complete?
  │     NO  → Cannot QA without completed workbook
  │     YES → Continue
  │
  ├─► Do I have access to RAW SOURCE DATA?
  │     │
  │     ├─► Bank statement CSV/PDF?
  │     │     NO  → Cannot verify properly — note limitation
  │     │     YES → Continue
  │     │
  │     ├─► Loan statement(s)?
  │     │     NO  → Secondary source — can proceed with bank
  │     │     YES → Good for cross-reference
  │     │
  │     └─► Settlement statement (Year 1)?
  │           NO  → CRITICAL — cannot verify rates calculation
  │           YES → Continue
  │
  └─► Do I have the Phase 2 and Phase 3 outputs?
        These document the workings I need to verify

┌─────────────────────────────────────────┐
│  FUNDAMENTAL QA PRINCIPLE                │
└─────────────────────────────────────────┘

CRITICAL RULE:
  │
  └─► I must VERIFY, not RECALCULATE
      │
      └─► I check that workbook figures match source data
          I do NOT modify the workbook
          I FLAG discrepancies for review
          I DO NOT assert errors without source verification
```

## 7.2 Structure Verification

```
┌─────────────────────────────────────────┐
│  WORKBOOK STRUCTURE CHECKS               │
└─────────────────────────────────────────┘

CHECK 1: Template used correctly?
  │
  ├─► P&L tab structure matches template?
  │     • Row numbers are standard?
  │     • Column layout correct?
  │     → If recreated from scratch: FLAG as structural issue
  │
  └─► All required tabs present?
        □ P&L
        □ Rental BS
        □ Loan Ac 1 (and 2, 3, 4 as needed)
        □ Settlement (Year 1)
        □ Depreciation (Year 1)
        □ PM Statements (if applicable)

CHECK 2: Source tabs complete?
  │
  ├─► Rental BS tab:
  │     • Contains ALL bank transactions?
  │     • Full year coverage (Apr-Mar)?
  │     • Category column completed for all rows?
  │     • SUMIF formulas present at bottom?
  │
  ├─► Loan Ac tabs:
  │     • Contains all interest transactions?
  │     • Monthly summary section present?
  │     • Totals match Phase 2 output?
  │
  └─► Settlement tab (Year 1):
        • Rates calculation documented?
        • All components present (apportionment, instalments, credit)?
        • Legal fees extracted?
        • Interest on deposit noted?

CHECK 3: P&L formulas correct?
  │
  FOR EACH P&L value:
  │
  ├─► Is it a FORMULA linking to source tab?
  │     YES → Verify link is correct
  │     NO  → Is it a hardcoded value with source reference?
  │           YES → Verify source reference in Column D
  │           NO  → FLAG: Unexplained hardcoded value
  │
  └─► Formula calculates correctly?
        Click into cell, verify result matches displayed value

CHECK 4: Workings sections complete?
  │
  ├─► Interest Deductibility (Rows 32-48, Cols I-P):
  │     • All 12 months populated?
  │     • Monthly figures link to Loan Ac tabs?
  │     • Totals correct?
  │
  └─► PM Statements (Rows 49-63, Cols I-P):
        • All months populated (if applicable)?
        • Figures link to source data?
```

## 7.3 Year 1 Settlement Verification

```
┌─────────────────────────────────────────┐
│  YEAR 1 SETTLEMENT CHECKS                │
└─────────────────────────────────────────┘

CHECK 1: Settlement statement was extracted and read?
  │
  └─► Evidence in Settlement tab of extraction?
      If no Settlement tab or sparse content:
      → CRITICAL FAILURE
      → Cannot verify Year 1 figures without this

CHECK 2: Rates calculation includes apportionment?
  │
  ├─► Step 1: Look at Rates figure in P&L (Row 34)
  │
  ├─► Step 2: Trace back to Settlement tab calculation
  │
  ├─► Step 3: Verify calculation:
  │     │
  │     │ Component                 │ In Calc? │ Matches Source? │
  │     │───────────────────────────│──────────│─────────────────│
  │     │ Settlement Apportionment  │ □ Yes/No │ □ Yes/No        │
  │     │ Instalment 1              │ □ Yes/No │ □ Yes/No        │
  │     │ Instalment 2              │ □ Yes/No │ □ Yes/No        │
  │     │ Instalment 3              │ □ Yes/No │ □ Yes/No        │
  │     │ Vendor Credit (subtracted)│ □ Yes/No │ □ Yes/No        │
  │
  └─► Step 4: If apportionment is MISSING or ZERO without verification:
        → FLAG: "Rates may be understated — settlement apportionment not included"
        → This is a common error

CHECK 3: Interest on deposit netted correctly?
  │
  ├─► Is there interest on deposit in Settlement tab?
  │     NO  → Check settlement statement to confirm none exists
  │     YES → Verify it's SUBTRACTED from Interest Expense (Row 25)
  │           NOT shown as separate Other Income

CHECK 4: Body corporate / Resident Society pro-rata captured?
  │
  ├─► Settlement statement shows BC pro-rata?
  │     YES → Is this included in BC total (Row 15)?
  │           May be in ADDITION to annual invoice
  │
  └─► Settlement statement shows RS pro-rata?
        YES → Is this included in RS total (Row 36)?
```

## 7.4 Interest Verification

```
┌─────────────────────────────────────────┐
│  INTEREST EXPENSE VERIFICATION           │
└─────────────────────────────────────────┘

CHECK 1: Source is bank statement?
  │
  └─► Were interest charges extracted from BANK STATEMENT?
      NOT loan statements (which can mislead)
      Evidence: Interest workings reference "BS" as source

CHECK 2: Transaction count matches expectation?
  │
  ├─► Count interest transactions in source data
  │
  ├─► Expected count based on frequency:
  │     • Bi-weekly: ~24-26 transactions
  │     • Monthly: ~12-13 transactions
  │
  └─► Significant deviation?
        → Investigate: partial year? offset account? data gap?

CHECK 3: Gross charges only (no subtractions)?
  │
  ├─► Review interest calculation
  │
  ├─► Were any credits/adjustments SUBTRACTED?
  │     YES → FLAG: "Interest may be understated — adjustments should not be subtracted"
  │     NO  → Correct approach
  │
  └─► Check for "Interest Adjustment" or credit entries
        These should NOT reduce the total

CHECK 4: Monthly breakdown accurate?
  │
  ├─► Pull source bank data
  │
  ├─► For each month:
  │     │
  │     ├─► Sum interest transactions for that month
  │     ├─► Compare to workings section
  │     └─► Match? □ Yes / □ No
  │
  └─► Any discrepancies?
        → Investigate: mis-assigned month? missed transaction?

CHECK 5: Deductibility % correct?
  │
  ├─► Property type?
  │     • New Build → Should be 100%
  │     • Existing → Should be 80% (FY25)
  │
  └─► Calculation correct?
        Gross Interest × Deductibility % = Row 25 amount?

CHECK 6: Offset account handling?
  │
  ├─► Is there an offset account?
  │
  ├─► If YES:
  │     │
  │     ├─► Interest may be legitimately low
  │     │
  │     └─► Verify: Only "LOAN INTEREST" debits included
  │                  "OFFSET Benefit" entries EXCLUDED
  │
  └─► If interest seems unusually low but NO offset:
        → FLAG for investigation
```

## 7.5 Expense Verification

```
┌─────────────────────────────────────────┐
│  EXPENSE-BY-EXPENSE VERIFICATION         │
└─────────────────────────────────────────┘

FOR EACH expense row in P&L:
  │
  STEP 1: Trace to source
    │
    ├─► What source tab does this link to?
    ├─► Is the link correct?
    └─► Does the amount match the source?

  STEP 2: Check specific rules applied
```

### Row 12: ADVERTISING
```
  │
  ├─► Is this SEPARATE from Agent Fees?
  │     Should NOT be combined with PM fees
  │
  └─► Correct items included?
        Tenant-finding ads only
```

### Row 13: AGENT FEES
```
  │
  ├─► Includes PM fees + letting fees + GST?
  │
  └─► Does NOT include advertising?
        (That's Row 12)
```

### Row 15: BODY CORPORATE
```
  │
  ├─► OPERATING FUND only?
  │     Reserve/sinking fund should be EXCLUDED
  │
  ├─► Invoice checked for split?
  │
  └─► Year 1: Both settlement pro-rata AND annual invoice?
        May correctly have two amounts
```

### Row 16: CONSULTING & ACCOUNTING
```
  │
  ├─► Is this INCLUDED?
  │     Should never be blank
  │
  └─► Standard amount $862.50?
        Or specific invoiced amount?
```

### Row 17: DEPRECIATION
```
  │
  ├─► If partial year: Is it PRO-RATED?
  │     │
  │     └─► Check: Full year × (months/12)?
  │         Example: 11 months = Full year × (11/12)
  │
  └─► Year 1: Links to Depreciation tab calculation?
```

### Row 18: DUE DILIGENCE
```
  │
  ├─► ALL compliance costs consolidated here?
  │     □ LIM
  │     □ Property valuation (Valocity)
  │     □ Depreciation schedule (Valuit)
  │     □ Meth test
  │     □ Healthy homes
  │     □ Smoke alarm
  │
  └─► If Year 1: Were these actively searched for?
```

### Row 25: INTEREST EXPENSE
```
  │
  ├─► Matches Phase 2 output?
  │
  ├─► Deductibility % applied correctly?
  │
  └─► Year 1: Interest on deposit NETTED (subtracted)?
```

### Row 34: RATES
```
  │
  ├─► Year 1: Does it include settlement apportionment?
  │     │
  │     └─► Formula: Apportionment + Instalments − Vendor Credit?
  │
  └─► Year 2+: Sum of instalments from bank statement?
```

### Row 36: RESIDENT SOCIETY
```
  │
  ├─► Is this SEPARATE from Body Corporate?
  │     Should NOT be combined
  │
  └─► Year 1: Includes settlement pro-rata if applicable?
```

### Row 41: WATER RATES
```
  │
  └─► Non-registered: GST-INCLUSIVE amount used?
        (NOT divided by 1.15)
```

## 7.6 False Positive Prevention

```
┌─────────────────────────────────────────┐
│  BEFORE FLAGGING AS ERROR                │
└─────────────────────────────────────────┘

CRITICAL: Many "errors" have legitimate explanations
```

### Interest lower than expected?
```
  │
  CHECK FIRST:
  │
  ├─► Is there an offset account?
  │     YES → Low interest is likely CORRECT
  │           Offset reduces payable interest
  │           DO NOT flag as error
  │
  ├─► Was the loan drawn partway through year?
  │     YES → Fewer months of interest is correct
  │
  └─► Is interest bi-weekly vs monthly?
        Ensure correct transaction count expectation
```

### Depreciation different from schedule?
```
  │
  CHECK FIRST:
  │
  └─► Was it pro-rated for partial year?
        YES → Different from schedule total is CORRECT
        Calculate: Schedule total × (months/12)
```

### Body corporate higher than invoice?
```
  │
  CHECK FIRST:
  │
  ├─► Year 1: Does it include settlement pro-rata?
  │     YES → Higher amount may be CORRECT
  │           (Pro-rata + Annual invoice)
  │
  └─► Does invoice have both operating AND reserve?
        Reserve should be excluded
        Operating portion only → may be lower
```

### Rates higher than bank instalments?
```
  │
  CHECK FIRST:
  │
  └─► Year 1: Does it include settlement apportionment?
        YES → Higher than instalments alone is CORRECT
        This is proper calculation
```

### Fewer interest charges than expected?
```
  │
  CHECK FIRST:
  │
  ├─► Was loan recently drawn (mid-year)?
  │     YES → Fewer charges is correct
  │
  └─► Is there an offset fully covering the loan?
        YES → Zero/minimal interest may be correct
```

### Verification Approach
```
┌─────────────────────────────────────────┐
│  VERIFICATION APPROACH                   │
└─────────────────────────────────────────┘

BEFORE asserting ANY error:
  │
  ├─► Step 1: Identify the apparent discrepancy
  │
  ├─► Step 2: Pull RAW SOURCE DATA
  │     • Bank statement transactions
  │     • Invoice details
  │     • Settlement statement figures
  │
  ├─► Step 3: Count/calculate at transaction level
  │
  ├─► Step 4: Consider legitimate explanations
  │     • Partial year?
  │     • Offset account?
  │     • Pro-rating?
  │     • Multiple components?
  │
  └─► Step 5: ONLY flag if discrepancy persists after verification
```

## 7.7 Reconciliation Checks

```
┌─────────────────────────────────────────┐
│  MATHEMATICAL RECONCILIATION             │
└─────────────────────────────────────────┘

CHECK 1: Income totals
  │
  ├─► Total Income (Row 11) = SUM of Rows 6-10?
  │
  └─► Each income row traces to source correctly?

CHECK 2: Expense totals
  │
  ├─► Total Expenses (Row 43) = SUM of Rows 12-42?
  │
  └─► Each expense row traces to source correctly?

CHECK 3: Net result
  │
  └─► Net Profit/(Loss) = Total Income − Total Expenses?

CHECK 4: Formula integrity
  │
  └─► Click into total cells
      Verify formulas reference correct ranges
      No hardcoded overrides

┌─────────────────────────────────────────┐
│  GST-REGISTERED RECONCILIATION           │
└─────────────────────────────────────────┘

IF GST-registered:
  │
  ├─► Obtain GST workings (filed returns)
  │
  ├─► Reconcile income:
  │     P&L Rental Income ↔ GST workings total
  │     Note: 1-month timing shift may exist
  │
  ├─► Reconcile key expenses:
  │     P&L figures should be GST-exclusive
  │     Match to GST workings categories
  │
  └─► Note: GST workings are AUTHORITATIVE
        If discrepancy, GST workings are more likely correct
        (Already filed with IRD)
```

## 7.8 QA Output Requirements

```
┌─────────────────────────────────────────┐
│  QA REPORT STRUCTURE                     │
└─────────────────────────────────────────┘

SECTION 1: Overall Status
  │
  └─► PASS / ISSUES FOUND / CRITICAL ERRORS

SECTION 2: Structure Verification
  │
  │ Check                              │ Status │
  │────────────────────────────────────│────────│
  │ Template P&L used                  │ ✓ / ✗  │
  │ All source tabs present            │ ✓ / ✗  │
  │ Rental BS complete                 │ ✓ / ✗  │
  │ Loan Ac tabs complete              │ ✓ / ✗  │
  │ Interest workings complete         │ ✓ / ✗  │
  │ PM workings complete               │ ✓ / ✗  │
  │ All P&L values formula-linked      │ ✓ / ✗  │

SECTION 3: Year 1 Verification (if applicable)
  │
  │ Check                              │ Status │
  │────────────────────────────────────│────────│
  │ Settlement statement extracted     │ ✓ / ✗  │
  │ Rates includes apportionment       │ ✓ / ✗  │
  │ Interest on deposit netted         │ ✓ / ✗  │
  │ BC/RS pro-rata captured            │ ✓ / ✗  │
  │ Depreciation pro-rated             │ ✓ / ✗  │

SECTION 4: Interest Verification
  │
  │ Check                              │ Status │
  │────────────────────────────────────│────────│
  │ Bank statement used as source      │ ✓ / ✗  │
  │ Transaction count matches          │ ✓ / ✗  │
  │ Gross charges only (no deductions) │ ✓ / ✗  │
  │ Monthly breakdown accurate         │ ✓ / ✗  │
  │ Correct deductibility % applied    │ ✓ / ✗  │

SECTION 5: Expense Verification
  │
  │ Check                              │ Status │
  │────────────────────────────────────│────────│
  │ BC = operating fund only           │ ✓ / ✗  │
  │ Depreciation pro-rated (if needed) │ ✓ / ✗  │
  │ Accounting fees included           │ ✓ / ✗  │
  │ Due Diligence has all compliance   │ ✓ / ✗  │
  │ Resident Society separate from BC  │ ✓ / ✗  │
  │ Advertising separate from Agent    │ ✓ / ✗  │

SECTION 6: Issues Found
  │
  │ Issue       │ Cell │ Expected │ Found  │ Action     │
  │─────────────│──────│──────────│────────│────────────│
  │ [Description]│ [Ref]│ $XXX     │ $XXX   │ [Fix needed]│

SECTION 7: Items for Client Clarification
  │
  └─► List any items requiring client input

SECTION 8: Final Status
  │
  └─► "Ready to file" / "Requires corrections: [list]"
```

---

# 8. DECISION TREE SUMMARY

```
┌────────────────────────────────────────────────────────────────┐
│                     ROMULUS DECISION TREE                       │
└────────────────────────────────────────────────────────────────┘

1. CLASSIFY CLIENT
   └─► Year 1? → Settlement statement MANDATORY
   └─► New Build? → Interest 100%
   └─► GST registered? → GST-exclusive amounts

2. FOR EACH PHASE:
   └─► Stay within scope boundaries
   └─► Do NOT perform out-of-scope actions
   └─► Output required deliverables

3. YEAR 1 CRITICAL PATH:
   └─► ALWAYS extract settlement statement PDF
   └─► ALWAYS include rates apportionment
   └─► Check for pro-rata BC/RS levies
   └─► Net interest on deposit against expense

4. INTEREST CRITICAL PATH:
   └─► Use BANK STATEMENT (not loan statements)
   └─► Count actual transactions (often bi-weekly)
   └─► Sum GROSS only (no subtractions)
   └─► Verify before flagging low amounts

5. P&L CRITICAL PATH:
   └─► Use template ONLY
   └─► All values = formulas or sourced hardcodes
   └─► Complete workings sections
   └─► Document all sources

6. VERIFICATION CRITICAL PATH:
   └─► Never assert error without raw data check
   └─► Consider legitimate explanations first
   └─► Pull transaction-level data to confirm
```

---

# KEY GUARDRAILS

1. **Phase boundaries** — don't bleed between phases
2. **Year 1 settlement statement** — non-negotiable extraction
3. **Bank statement primacy** — for interest verification
4. **Formula traceability** — every P&L figure must be verifiable
5. **Verification before assertion** — never flag errors without source data confirmation

---

# COMMON ERRORS TO PREVENT

| Error | Correct Approach |
|:------|:-----------------|
| **Assuming no rates apportionment** | **Year 1: ALWAYS extract and read settlement statement** |
| **Using only bank instalments for Year 1 rates** | **Rates = Settlement apportionment + Instalments − Vendor credit** |
| **Not reading settlement statement PDF** | **Year 1: Extract and read it** |
| Recreating P&L structure | Use template — fill cells only |
| Using loan statements for interest | Bank statement is PRIMARY |
| Assuming monthly interest | Count actual transactions (often bi-weekly) |
| Subtracting interest adjustments | Sum gross charges only |
| Including BC reserve fund | Operating fund only — check invoice |
| Full year depreciation for partial year | Pro-rate: Full × (months/12) |
| Missing accounting fees | Always include Row 16 ($862.50) |
| Flagging low offset interest as error | Verify against statements first |
| Hardcoding P&L figures | Use formulas linking to source tabs |
| Combining advertising with agent fees | Advertising (Row 12) SEPARATE from Agent Fees (Row 13) |
| Missing property valuations in Due Diligence | Include BOTH Valocity AND Valuit |
| Treating interest on deposit as income | NET against Interest Expense |

---

*Document Version: 1.0*  
*Last Updated: December 2025*  
*Review annually for legislative changes*
