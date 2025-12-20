# Phase 1: Complete Implementation Guide

## Executive Summary

Phase 1 handles **Document Intake, Classification, and Full Data Extraction**. The goal is to ensure **ALL document data is extracted ALL the time** with guaranteed schema compliance using Claude's Tool Use feature.

**Key Improvements:**
- Remove 5-page limit → Process ALL pages via batch processing
- Enhanced retry logic with proper rate limiting for 50+ documents
- Tool Use for guaranteed JSON schema compliance
- Multi-pass extraction for complex documents
- Comprehensive NZ tax rule enforcement in schemas

---

## Table of Contents

1. [Current State Analysis](#current-state-analysis)
2. [Current Limitations](#current-limitations)
3. [Proposed Architecture](#proposed-architecture)
4. [Comprehensive Tool Use Schemas](#comprehensive-tool-use-schemas)
5. [Implementation Code Changes](#implementation-code-changes)
6. [NZ Tax Validation Rules](#nz-tax-validation-rules)
7. [Configuration Settings](#configuration-settings)
8. [Files to Create/Modify](#files-to-createmodify)
9. [Implementation Priority](#implementation-priority)
10. [Success Metrics](#success-metrics)

---

## Current State Analysis

### Current Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                    CURRENT PHASE 1 FLOW                         │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  Upload Files → Save to Disk → Process Each Document            │
│                                      ↓                          │
│                              ┌──────────────────┐               │
│                              │  File Handler    │               │
│                              │  - PDF → Images  │               │
│                              │  - CSV → Text    │               │
│                              │  - Excel → Text  │               │
│                              └──────────────────┘               │
│                                      ↓                          │
│                              ┌──────────────────┐               │
│                              │  Claude Client   │               │
│                              │  Single API Call │               │
│                              │  Free-form JSON  │               │
│                              └──────────────────┘               │
│                                      ↓                          │
│                              ┌──────────────────┐               │
│                              │  Save to DB      │               │
│                              └──────────────────┘               │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

### Current Files & Responsibilities

| File | Current Responsibility |
|------|------------------------|
| `document_processor.py` | Orchestrates processing, handles duplicates, collects flagged transactions |
| `claude_client.py` | Calls Claude API with retry, prepares images, parses JSON |
| `file_handler.py` | Saves files, processes PDF/Excel/CSV, converts PDFs to images |
| `prompts.py` | Classification and extraction prompts (~558 lines) |
| `config.py` | Settings (max file size, allowed types, API keys) |

---

## Current Limitations

### 1. 5-Page Limit for PDFs

**Location:** `document_processor.py` line 522-523
```python
image_data = await self.claude_client.prepare_image_data(
    processed.image_paths[:5]  # Limit to 5 pages
)
```

**Impact:**
- Bank statements with 20+ pages lose 75% of transactions
- Loan statements with monthly detail lose most data
- PM statements spanning full year lose months 6-12

### 2. Insufficient Retry Logic

**Location:** `claude_client.py` lines 42-77
```python
self.max_retries = 3
wait_time = 2**attempt  # 1, 2, 4 seconds
```

**Impact:**
- 3 retries with 1-4 second waits insufficient for 50+ documents
- Rate limits hit around document 15-20
- No semaphore/concurrency control

### 3. Free-Form JSON Output

**Location:** `claude_client.py` lines 134-144
```python
response_text = response.content[0].text
if "```json" in response_text:
    response_text = response_text.split("```json")[1].split("```")[0].strip()
classification_data = json.loads(response_text)
```

**Impact:**
- JSON parsing failures cause document processing to fail
- No schema enforcement - missing fields go undetected
- Inconsistent field names between runs

### 4. Single-Pass Extraction

**Impact:**
- No verification that all data was captured
- Settlement statements may miss key fields
- Complex documents may have extraction errors

### 5. No Structured Output Enforcement

**Impact:**
- Different runs may return different field structures
- Numeric values sometimes returned as strings
- Date formats inconsistent
- Missing required fields not caught until Phase 2 fails

### 6. Missing Configuration for High Volume

**Location:** `config.py` - No settings for:
- Rate limiting (requests per minute)
- Concurrent document processing
- Retry backoff configuration
- Page limits per document type

---

## Proposed Architecture

```
┌─────────────────────────────────────────────────────────────────────────────────────┐
│                              PHASE 1: DOCUMENT INTAKE                                │
│                         "Extract Everything, Miss Nothing"                           │
├─────────────────────────────────────────────────────────────────────────────────────┤
│                                                                                     │
│  ┌─────────────────────────────────────────────────────────────────────────────┐   │
│  │                         STEP 1: FILE RECEPTION                               │   │
│  │                                                                              │   │
│  │  User Upload (Web/API) → Validate File Type → Generate Content Hash          │   │
│  │                                    ↓                                         │   │
│  │  Check Duplicates (hash + filename) → Save to Disk → Create DB Record        │   │
│  │                                                                              │   │
│  │  Supported: PDF, PNG, JPG, XLSX, XLS, CSV                                   │   │
│  │  Max Size: 50MB per file                                                     │   │
│  └─────────────────────────────────────────────────────────────────────────────┘   │
│                                         ↓                                           │
│  ┌─────────────────────────────────────────────────────────────────────────────┐   │
│  │                      STEP 2: FILE PROCESSING                                 │   │
│  │                                                                              │   │
│  │  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐        │   │
│  │  │    PDF      │  │   Image     │  │    CSV      │  │   Excel     │        │   │
│  │  │             │  │             │  │             │  │             │        │   │
│  │  │ Digital?    │  │ Direct use  │  │ Parse to    │  │ Parse all   │        │   │
│  │  │ → Extract   │  │ for vision  │  │ structured  │  │ sheets to   │        │   │
│  │  │   text      │  │             │  │ text        │  │ text        │        │   │
│  │  │             │  │             │  │             │  │             │        │   │
│  │  │ Scanned?    │  │             │  │             │  │             │        │   │
│  │  │ → Convert   │  │             │  │             │  │             │        │   │
│  │  │   to images │  │             │  │             │  │             │        │   │
│  │  └─────────────┘  └─────────────┘  └─────────────┘  └─────────────┘        │   │
│  │                                                                              │   │
│  │  Output: ProcessedFile { text_content, image_paths[], page_count }          │   │
│  └─────────────────────────────────────────────────────────────────────────────┘   │
│                                         ↓                                           │
│  ┌─────────────────────────────────────────────────────────────────────────────┐   │
│  │                    STEP 3: RATE LIMITER & QUEUE                              │   │
│  │                                                                              │   │
│  │  ┌─────────────────────────────────────────────────────────────────────┐    │   │
│  │  │  Semaphore: Max 3 concurrent Claude API calls                        │    │   │
│  │  │  Min Interval: 0.5s between requests                                 │    │   │
│  │  │  Max Retries: 5 with exponential backoff (2s → 4s → 8s → 16s → 32s) │    │   │
│  │  │  Max Delay: 60s cap                                                  │    │   │
│  │  │  Jitter: Random 0-1s added to prevent thundering herd                │    │   │
│  │  └─────────────────────────────────────────────────────────────────────┘    │   │
│  └─────────────────────────────────────────────────────────────────────────────┘   │
│                                         ↓                                           │
│  ┌─────────────────────────────────────────────────────────────────────────────┐   │
│  │                 STEP 4: DOCUMENT CLASSIFICATION (Pass 1)                     │   │
│  │                                                                              │   │
│  │  Input: First 5 pages (for classification only)                             │   │
│  │  Tool Use: DOCUMENT_CLASSIFICATION_TOOL (schema enforced)                   │   │
│  │                                                                              │   │
│  │  Output:                                                                     │   │
│  │  ├── document_type (enum: 17 types)                                         │   │
│  │  ├── confidence (0.0-1.0)                                                   │   │
│  │  ├── reasoning (why this classification)                                    │   │
│  │  ├── address_verification (matches property context?)                       │   │
│  │  ├── flags[] (issues detected)                                              │   │
│  │  └── preliminary_details (quick extraction of key identifiers)              │   │
│  │                                                                              │   │
│  │  Decision Point:                                                             │   │
│  │  ├── Financial Document? → Proceed to Pass 2 (Deep Extraction)              │   │
│  │  └── Non-Financial? → Proceed to Pass 2 (Standard Extraction)               │   │
│  └─────────────────────────────────────────────────────────────────────────────┘   │
│                                         ↓                                           │
│  ┌─────────────────────────────────────────────────────────────────────────────┐   │
│  │                   STEP 5: DEEP EXTRACTION (Pass 2)                           │   │
│  │                                                                              │   │
│  │  ┌─────────────────────────────────────────────────────────────────────┐    │   │
│  │  │                    FOR FINANCIAL DOCUMENTS                           │    │   │
│  │  │             (bank_statement, loan_statement, pm_statement)           │    │   │
│  │  │                                                                      │    │   │
│  │  │  Process ALL pages in batches of 5:                                  │    │   │
│  │  │                                                                      │    │   │
│  │  │  Pages 1-5:                                                          │    │   │
│  │  │  ├── Call Claude with TRANSACTION_EXTRACTION_TOOL                    │    │   │
│  │  │  ├── Extract transactions, account info, balances                    │    │   │
│  │  │  └── Note if continuation detected                                   │    │   │
│  │  │           ↓                                                          │    │   │
│  │  │  Pages 6-10:                                                         │    │   │
│  │  │  ├── Pass previous batch summary for context                         │    │   │
│  │  │  ├── Extract transactions (continue sequence)                        │    │   │
│  │  │  └── Validate running balances match                                 │    │   │
│  │  │           ↓                                                          │    │   │
│  │  │  ... continue until all pages processed ...                          │    │   │
│  │  │           ↓                                                          │    │   │
│  │  │  Merge & Deduplicate:                                                │    │   │
│  │  │  ├── Combine all transactions                                        │    │   │
│  │  │  ├── Remove duplicates (same date + amount + description)            │    │   │
│  │  │  └── Sort by date ascending                                          │    │   │
│  │  │                                                                      │    │   │
│  │  └─────────────────────────────────────────────────────────────────────┘    │   │
│  │                                                                              │   │
│  │  ┌─────────────────────────────────────────────────────────────────────┐    │   │
│  │  │                FOR SETTLEMENT STATEMENTS (Year 1)                    │    │   │
│  │  │                                                                      │    │   │
│  │  │  Process ALL pages with SETTLEMENT_EXTRACTION_TOOL:                  │    │   │
│  │  │  ├── settlement_info (date, address, parties)                        │    │   │
│  │  │  ├── financial_details (purchase price, deposit)                     │    │   │
│  │  │  ├── apportionments (rates, water, BC, insurance)                    │    │   │
│  │  │  ├── fees_and_costs (legal, disbursements)                           │    │   │
│  │  │  ├── interest_on_deposit (Year 1 income offset)                      │    │   │
│  │  │  ├── bank_contribution (if mentioned - flag for verification)        │    │   │
│  │  │  └── all_line_items[] (every line in document order)                 │    │   │
│  │  │                                                                      │    │   │
│  │  └─────────────────────────────────────────────────────────────────────┘    │   │
│  │                                                                              │   │
│  │  ┌─────────────────────────────────────────────────────────────────────┐    │   │
│  │  │                 FOR OTHER DOCUMENT TYPES                             │    │   │
│  │  │                                                                      │    │   │
│  │  │  Use document-specific extraction tool:                              │    │   │
│  │  │  ├── CCC_EXTRACTION_TOOL                                             │    │   │
│  │  │  ├── INSURANCE_EXTRACTION_TOOL                                       │    │   │
│  │  │  ├── DEPRECIATION_EXTRACTION_TOOL                                    │    │   │
│  │  │  ├── BODY_CORPORATE_EXTRACTION_TOOL                                  │    │   │
│  │  │  ├── RATES_EXTRACTION_TOOL                                           │    │   │
│  │  │  ├── WATER_RATES_EXTRACTION_TOOL                                     │    │   │
│  │  │  ├── COMPLIANCE_DOC_EXTRACTION_TOOL (HH, meth, smoke, LIM)          │    │   │
│  │  │  └── INVOICE_EXTRACTION_TOOL                                         │    │   │
│  │  │                                                                      │    │   │
│  │  └─────────────────────────────────────────────────────────────────────┘    │   │
│  └─────────────────────────────────────────────────────────────────────────────┘   │
│                                         ↓                                           │
│  ┌─────────────────────────────────────────────────────────────────────────────┐   │
│  │                STEP 6: VERIFICATION (Pass 3 - Optional)                      │   │
│  │                                                                              │   │
│  │  For critical documents (settlement, financial), run verification:           │   │
│  │                                                                              │   │
│  │  ┌─────────────────────────────────────────────────────────────────────┐    │   │
│  │  │  Completeness Check:                                                 │    │   │
│  │  │  ├── All required fields populated?                                  │    │   │
│  │  │  ├── Date ranges complete (no gaps in financial)?                    │    │   │
│  │  │  └── Critical fields have values (not null/zero)?                    │    │   │
│  │  └─────────────────────────────────────────────────────────────────────┘    │   │
│  │                                                                              │   │
│  │  ┌─────────────────────────────────────────────────────────────────────┐    │   │
│  │  │  Mathematical Validation:                                            │    │   │
│  │  │  ├── Opening + transactions = closing balance?                       │    │   │
│  │  │  ├── Sum of line items = total?                                      │    │   │
│  │  │  └── Apportionments calculated correctly?                            │    │   │
│  │  └─────────────────────────────────────────────────────────────────────┘    │   │
│  │                                                                              │   │
│  │  ┌─────────────────────────────────────────────────────────────────────┐    │   │
│  │  │  Cross-Reference Check:                                              │    │   │
│  │  │  ├── Address matches property context?                               │    │   │
│  │  │  ├── Dates within tax year?                                          │    │   │
│  │  │  └── Client name matches?                                            │    │   │
│  │  └─────────────────────────────────────────────────────────────────────┘    │   │
│  │                                                                              │   │
│  │  If issues found → Flag for review (don't fail)                             │   │
│  └─────────────────────────────────────────────────────────────────────────────┘   │
│                                         ↓                                           │
│  ┌─────────────────────────────────────────────────────────────────────────────┐   │
│  │                      STEP 7: PERSIST TO DATABASE                             │   │
│  │                                                                              │   │
│  │  Document Record:                                                            │   │
│  │  ├── document_type                                                          │   │
│  │  ├── classification_confidence                                              │   │
│  │  ├── extracted_data (JSON)                                                  │   │
│  │  │   ├── key_details (document-specific fields)                             │   │
│  │  │   ├── transactions[] (for financial docs)                                │   │
│  │  │   ├── line_items[] (for settlement)                                      │   │
│  │  │   └── verification_result                                                │   │
│  │  ├── flags[]                                                                │   │
│  │  ├── status (CLASSIFIED)                                                    │   │
│  │  └── processing_metadata                                                    │   │
│  │      ├── pages_processed                                                    │   │
│  │      ├── batches_used                                                       │   │
│  │      ├── api_calls_made                                                     │   │
│  │      └── processing_time_ms                                                 │   │
│  └─────────────────────────────────────────────────────────────────────────────┘   │
│                                         ↓                                           │
│  ┌─────────────────────────────────────────────────────────────────────────────┐   │
│  │                   STEP 8: COMPLETENESS REVIEW (All Docs)                     │   │
│  │                                                                              │   │
│  │  After all documents processed, run completeness check:                      │   │
│  │                                                                              │   │
│  │  ├── All required documents present for scenario?                           │   │
│  │  │   ├── Year 1: Settlement + Bank + Loan statements                        │   │
│  │  │   ├── New Build: + CCC with date >= 27/03/2020                           │   │
│  │  │   └── Ongoing: Bank + Loan (if claiming interest)                        │   │
│  │  │                                                                          │   │
│  │  ├── Blocking issues detected?                                              │   │
│  │  │   ├── Wrong insurance type (home & contents)                             │   │
│  │  │   ├── Address mismatch on key documents                                  │   │
│  │  │   └── Missing critical documents                                         │   │
│  │  │                                                                          │   │
│  │  └── Generate recommendations for missing/incomplete items                  │   │
│  │                                                                              │   │
│  │  Output: TaxReturnReview { status, missing_docs, blocking_issues, score }   │   │
│  └─────────────────────────────────────────────────────────────────────────────┘   │
│                                                                                     │
│  OUTPUT → Ready for Phase 2 (AI Brain)                                             │
│                                                                                     │
└─────────────────────────────────────────────────────────────────────────────────────┘
```

---

## Comprehensive Tool Use Schemas

### 1. Document Classification Tool

```python
DOCUMENT_CLASSIFICATION_TOOL = {
    "name": "classify_document",
    "description": "Classify a document for NZ rental property tax return and extract key identifiers",
    "input_schema": {
        "type": "object",
        "required": [
            "document_type",
            "confidence",
            "reasoning",
            "address_verification",
            "key_identifiers"
        ],
        "properties": {
            "document_type": {
                "type": "string",
                "enum": [
                    "bank_statement",
                    "loan_statement",
                    "settlement_statement",
                    "depreciation_schedule",
                    "body_corporate",
                    "property_manager_statement",
                    "lim_report",
                    "healthy_homes",
                    "meth_test",
                    "smoke_alarm",
                    "ccc",
                    "landlord_insurance",
                    "rates",
                    "water_rates",
                    "maintenance_invoice",
                    "other",
                    "invalid"
                ],
                "description": "The classified document type"
            },
            "confidence": {
                "type": "number",
                "minimum": 0.0,
                "maximum": 1.0,
                "description": "Confidence in classification (0.0-1.0)"
            },
            "reasoning": {
                "type": "string",
                "minLength": 10,
                "maxLength": 200,
                "description": "One sentence explanation of why this classification was chosen"
            },
            "address_verification": {
                "type": "object",
                "required": ["address_found", "matches_context"],
                "properties": {
                    "address_found": {
                        "type": ["string", "null"],
                        "description": "Property address found in document, null if not found"
                    },
                    "matches_context": {
                        "type": "boolean",
                        "description": "True if address matches the property context provided"
                    },
                    "mismatch_details": {
                        "type": "string",
                        "description": "Explanation if address doesn't match"
                    }
                }
            },
            "key_identifiers": {
                "type": "object",
                "description": "Quick extraction of key document identifiers",
                "properties": {
                    "document_date": {
                        "type": "string",
                        "description": "Primary date on document (YYYY-MM-DD)"
                    },
                    "period_start": {
                        "type": "string",
                        "description": "Statement period start (YYYY-MM-DD)"
                    },
                    "period_end": {
                        "type": "string",
                        "description": "Statement period end (YYYY-MM-DD)"
                    },
                    "issuer_name": {
                        "type": "string",
                        "description": "Bank, insurer, council, PM company name"
                    },
                    "account_number": {
                        "type": "string",
                        "description": "Account/policy/reference number"
                    },
                    "total_amount": {
                        "type": "number",
                        "description": "Primary monetary amount on document"
                    }
                }
            },
            "flags": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["flag_code", "severity", "message"],
                    "properties": {
                        "flag_code": {
                            "type": "string",
                            "enum": [
                                "address_mismatch",
                                "wrong_insurance_type",
                                "personal_document",
                                "date_outside_tax_year",
                                "incomplete_document",
                                "poor_image_quality",
                                "password_protected",
                                "multiple_properties",
                                "client_name_mismatch",
                                "commercial_property_detected"
                            ]
                        },
                        "severity": {
                            "type": "string",
                            "enum": ["critical", "warning", "info"]
                        },
                        "message": {
                            "type": "string",
                            "description": "Human-readable description of the issue"
                        }
                    }
                }
            },
            "page_assessment": {
                "type": "object",
                "required": ["pages_visible", "appears_complete"],
                "properties": {
                    "pages_visible": {
                        "type": "integer",
                        "description": "Number of pages visible in this batch"
                    },
                    "appears_complete": {
                        "type": "boolean",
                        "description": "True if document appears complete, false if truncated"
                    },
                    "continuation_indicators": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Signs that more pages exist (e.g., 'Page 1 of 5', 'continued...')"
                    },
                    "estimated_total_pages": {
                        "type": "integer",
                        "description": "Estimated total pages if indicated in document"
                    }
                }
            }
        }
    }
}
```

---

### 2. Bank Statement Extraction Tool (COMPREHENSIVE)

```python
BANK_STATEMENT_EXTRACTION_TOOL = {
    "name": "extract_bank_statement",
    "description": "Extract ALL data from a bank statement for NZ rental property tax - handles ALL edge cases",
    "input_schema": {
        "type": "object",
        "required": [
            "account_info",
            "statement_period",
            "transactions",
            "summary",
            "extraction_metadata"
        ],
        "properties": {
            "account_info": {
                "type": "object",
                "required": ["bank_name", "account_number", "account_type"],
                "properties": {
                    "bank_name": {
                        "type": "string",
                        "enum": ["ASB", "ANZ", "BNZ", "Westpac", "Kiwibank", "TSB", "SBS", "Heartland", "Co-operative Bank", "Rabobank", "HSBC", "Other"],
                        "description": "NZ bank name"
                    },
                    "account_number": {
                        "type": "string",
                        "description": "NZ bank account number (XX-XXXX-XXXXXXX-XX format preferred)"
                    },
                    "account_name": {
                        "type": "string",
                        "description": "Name on the account"
                    },
                    "account_type": {
                        "type": "string",
                        "enum": ["transaction", "savings", "term_deposit", "loan", "offset", "revolving_credit", "call_account", "other"],
                        "description": "Type of account"
                    },
                    "is_joint_account": {
                        "type": "boolean",
                        "description": "True if joint account"
                    },
                    "is_rental_dedicated": {
                        "type": "boolean",
                        "description": "True if account appears dedicated to rental property (based on transaction patterns)"
                    },
                    "linked_loan_accounts": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Loan account numbers that appear in transactions (for interest tracking)"
                    }
                }
            },
            "statement_period": {
                "type": "object",
                "required": ["start_date", "end_date", "opening_balance", "closing_balance"],
                "properties": {
                    "start_date": {
                        "type": "string",
                        "format": "date",
                        "description": "Statement period start (YYYY-MM-DD)"
                    },
                    "end_date": {
                        "type": "string",
                        "format": "date",
                        "description": "Statement period end (YYYY-MM-DD)"
                    },
                    "opening_balance": {
                        "type": "number",
                        "description": "Balance at start of period"
                    },
                    "closing_balance": {
                        "type": "number",
                        "description": "Balance at end of period"
                    },
                    "covers_full_tax_year": {
                        "type": "boolean",
                        "description": "True if covers 1 Apr to 31 Mar"
                    },
                    "tax_year": {
                        "type": "string",
                        "description": "Tax year this belongs to (e.g., 'FY25' for 1 Apr 2024 - 31 Mar 2025)"
                    }
                }
            },
            "transactions": {
                "type": "array",
                "description": "ALL transactions in statement - extract EVERY SINGLE ONE",
                "items": {
                    "type": "object",
                    "required": ["date", "description", "amount", "transaction_type", "categorization"],
                    "properties": {
                        "date": {
                            "type": "string",
                            "format": "date",
                            "description": "Transaction date (YYYY-MM-DD)"
                        },
                        "description": {
                            "type": "string",
                            "minLength": 1,
                            "description": "Full transaction description EXACTLY as shown (preserve all details)"
                        },
                        "amount": {
                            "type": "number",
                            "description": "Amount (negative for debits/money out, positive for credits/money in)"
                        },
                        "balance": {
                            "type": ["number", "null"],
                            "description": "Running balance after transaction (null if not shown)"
                        },
                        "transaction_type": {
                            "type": "string",
                            "enum": ["debit", "credit"],
                            "description": "Whether money left (debit) or entered (credit) account"
                        },
                        "reference": {
                            "type": ["string", "null"],
                            "description": "Transaction reference/particulars/code if shown"
                        },
                        "other_party": {
                            "type": ["string", "null"],
                            "description": "Payee/payer name (cleaned, no account numbers)"
                        },
                        "categorization": {
                            "type": "object",
                            "required": ["suggested_category", "confidence", "pl_row"],
                            "properties": {
                                "suggested_category": {
                                    "type": "string",
                                    "enum": [
                                        "rental_income",
                                        "water_rates_recovered",
                                        "bank_contribution",
                                        "insurance_payout",
                                        "other_income",
                                        "interest_debit",
                                        "interest_credit",
                                        "interest_adjustment",
                                        "principal_repayment",
                                        "loan_drawdown",
                                        "offset_benefit",
                                        "council_rates",
                                        "water_rates",
                                        "body_corporate_operating",
                                        "body_corporate_reserve",
                                        "resident_society",
                                        "landlord_insurance",
                                        "mortgage_protection_insurance",
                                        "agent_fees",
                                        "letting_fee",
                                        "inspection_fee",
                                        "advertising",
                                        "repairs_maintenance",
                                        "cleaning",
                                        "capital_improvement",
                                        "legal_fees",
                                        "accounting_fees",
                                        "depreciation_valuation",
                                        "due_diligence",
                                        "bank_fees",
                                        "utilities",
                                        "travel",
                                        "sundry_expense",
                                        "transfer_between_accounts",
                                        "personal_expense",
                                        "bond_received",
                                        "bond_released",
                                        "funds_introduced",
                                        "drawing",
                                        "unknown"
                                    ]
                                },
                                "confidence": {
                                    "type": "number",
                                    "minimum": 0.0,
                                    "maximum": 1.0
                                },
                                "reasoning": {
                                    "type": "string",
                                    "description": "Brief explanation for category choice"
                                },
                                "pl_row": {
                                    "type": ["integer", "null"],
                                    "description": "P&L row number this maps to (null if excluded)"
                                },
                                "is_deductible": {
                                    "type": "boolean",
                                    "description": "True if this expense is tax deductible"
                                }
                            }
                        },
                        "special_handling": {
                            "type": "object",
                            "description": "Special handling flags for complex transactions",
                            "properties": {
                                "is_combined_bond_rent": {
                                    "type": "boolean",
                                    "description": "True if this appears to be bond + rent combined"
                                },
                                "suggested_bond_amount": {
                                    "type": ["number", "null"],
                                    "description": "If combined, suggested bond portion"
                                },
                                "suggested_rent_amount": {
                                    "type": ["number", "null"],
                                    "description": "If combined, suggested rent portion"
                                },
                                "is_loan_repayment_total": {
                                    "type": "boolean",
                                    "description": "True if this is total repayment (interest + principal)"
                                },
                                "linked_loan_account": {
                                    "type": ["string", "null"],
                                    "description": "Loan account number this relates to"
                                },
                                "is_offset_related": {
                                    "type": "boolean",
                                    "description": "True if this is an offset account benefit entry"
                                },
                                "gst_amount": {
                                    "type": ["number", "null"],
                                    "description": "GST component if identifiable"
                                },
                                "is_gst_inclusive": {
                                    "type": "boolean",
                                    "description": "True if amount includes GST"
                                }
                            }
                        },
                        "review_flags": {
                            "type": "object",
                            "properties": {
                                "needs_review": {
                                    "type": "boolean",
                                    "description": "True if transaction needs human review"
                                },
                                "reasons": {
                                    "type": "array",
                                    "items": {
                                        "type": "string",
                                        "enum": [
                                            "large_payment_over_500",
                                            "large_payment_over_2000",
                                            "large_payment_over_5000",
                                            "cash_transaction",
                                            "atm_withdrawal",
                                            "payment_to_individual",
                                            "generic_description",
                                            "retail_store",
                                            "unclear_purpose",
                                            "potential_personal_expense",
                                            "missing_invoice_required",
                                            "potential_capital_expense",
                                            "bond_rent_split_needed",
                                            "interest_principal_split_needed"
                                        ]
                                    }
                                },
                                "severity": {
                                    "type": "string",
                                    "enum": ["critical", "warning", "info"]
                                }
                            }
                        }
                    }
                }
            },
            "interest_analysis": {
                "type": "object",
                "description": "Detailed interest transaction analysis",
                "required": ["total_interest_debits", "interest_frequency", "interest_transactions"],
                "properties": {
                    "total_interest_debits": {
                        "type": "number",
                        "description": "Sum of ALL interest DEBIT transactions only (gross)"
                    },
                    "total_interest_credits": {
                        "type": "number",
                        "description": "Sum of any interest credits/adjustments (DO NOT subtract from debits)"
                    },
                    "interest_frequency": {
                        "type": "string",
                        "enum": ["weekly", "fortnightly", "monthly", "irregular"],
                        "description": "Detected frequency of interest charges"
                    },
                    "interest_transaction_count": {
                        "type": "integer",
                        "description": "Number of interest debit transactions found"
                    },
                    "interest_transactions": {
                        "type": "array",
                        "description": "All interest-related transactions for detailed tracking",
                        "items": {
                            "type": "object",
                            "properties": {
                                "date": {"type": "string", "format": "date"},
                                "amount": {"type": "number"},
                                "loan_account": {"type": ["string", "null"]},
                                "is_debit": {"type": "boolean"},
                                "include_in_deduction": {
                                    "type": "boolean",
                                    "description": "True if should be included in interest deduction"
                                },
                                "exclusion_reason": {
                                    "type": ["string", "null"],
                                    "enum": [null, "credit_not_debit", "adjustment_entry", "offset_benefit", "capitalised", "savings_interest"]
                                }
                            }
                        }
                    },
                    "monthly_interest_breakdown": {
                        "type": "object",
                        "description": "Interest by month for P&L workings",
                        "additionalProperties": {
                            "type": "number"
                        }
                    },
                    "offset_account_detected": {
                        "type": "boolean",
                        "description": "True if offset account pattern detected"
                    },
                    "offset_notes": {
                        "type": ["string", "null"],
                        "description": "Notes about offset account if detected"
                    }
                }
            },
            "rental_income_analysis": {
                "type": "object",
                "description": "Analysis of rental income patterns",
                "properties": {
                    "total_rental_income": {
                        "type": "number",
                        "description": "Total rental income detected"
                    },
                    "rental_frequency": {
                        "type": "string",
                        "enum": ["weekly", "fortnightly", "monthly", "irregular"]
                    },
                    "typical_rent_amount": {
                        "type": "number",
                        "description": "Most common rent amount"
                    },
                    "income_sources": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "source_name": {"type": "string"},
                                "total_amount": {"type": "number"},
                                "transaction_count": {"type": "integer"},
                                "is_property_manager": {"type": "boolean"}
                            }
                        }
                    },
                    "pm_detected": {
                        "type": "boolean",
                        "description": "True if property manager payments detected"
                    },
                    "pm_company_name": {
                        "type": ["string", "null"],
                        "description": "Property management company name if detected"
                    }
                }
            },
            "summary": {
                "type": "object",
                "required": [
                    "total_transactions",
                    "total_credits",
                    "total_debits",
                    "balance_validated"
                ],
                "properties": {
                    "total_transactions": {
                        "type": "integer",
                        "minimum": 0,
                        "description": "Total number of transactions extracted"
                    },
                    "total_credits": {
                        "type": "number",
                        "description": "Sum of all credit transactions"
                    },
                    "total_debits": {
                        "type": "number",
                        "description": "Sum of all debit transactions (as positive number)"
                    },
                    "net_movement": {
                        "type": "number",
                        "description": "Net change in balance (credits - debits)"
                    },
                    "balance_validated": {
                        "type": "boolean",
                        "description": "True if opening + net = closing balance"
                    },
                    "balance_variance": {
                        "type": ["number", "null"],
                        "description": "Difference if balance doesn't reconcile"
                    },
                    "flagged_count": {
                        "type": "integer",
                        "description": "Number of transactions flagged for review"
                    },
                    "critical_flags_count": {
                        "type": "integer",
                        "description": "Number of transactions with critical flags"
                    },
                    "category_totals": {
                        "type": "object",
                        "description": "Sum by category for quick reference",
                        "additionalProperties": {"type": "number"}
                    }
                }
            },
            "extraction_metadata": {
                "type": "object",
                "required": ["pages_processed", "batch_number", "is_complete"],
                "properties": {
                    "pages_processed": {
                        "type": "integer",
                        "description": "Number of pages processed in this extraction"
                    },
                    "batch_number": {
                        "type": "integer",
                        "description": "Which batch this is (1, 2, 3, etc.)"
                    },
                    "total_batches": {
                        "type": "integer",
                        "description": "Total number of batches expected"
                    },
                    "is_complete": {
                        "type": "boolean",
                        "description": "True if this appears to be the complete statement"
                    },
                    "continuation_detected": {
                        "type": "boolean",
                        "description": "True if more pages appear to follow"
                    },
                    "first_transaction_date": {
                        "type": "string",
                        "format": "date"
                    },
                    "last_transaction_date": {
                        "type": "string",
                        "format": "date"
                    },
                    "data_quality_score": {
                        "type": "number",
                        "minimum": 0.0,
                        "maximum": 1.0,
                        "description": "Confidence in extraction quality"
                    }
                }
            }
        }
    }
}
```

---

### 3. Loan Statement Extraction Tool (COMPREHENSIVE)

```python
LOAN_STATEMENT_EXTRACTION_TOOL = {
    "name": "extract_loan_statement",
    "description": "Extract ALL data from a mortgage/loan statement for NZ rental property tax - handles ALL loan types",
    "input_schema": {
        "type": "object",
        "required": [
            "loan_info",
            "statement_period",
            "interest_summary",
            "transactions",
            "extraction_metadata"
        ],
        "properties": {
            "loan_info": {
                "type": "object",
                "required": ["lender", "loan_account_number", "loan_type"],
                "properties": {
                    "lender": {
                        "type": "string",
                        "description": "Bank/lender name"
                    },
                    "loan_account_number": {
                        "type": "string",
                        "description": "Loan account number"
                    },
                    "loan_type": {
                        "type": "string",
                        "enum": ["table_loan", "revolving_credit", "interest_only", "floating", "fixed", "split", "offset", "construction", "bridging", "other"],
                        "description": "Type of loan"
                    },
                    "is_split_loan": {
                        "type": "boolean",
                        "description": "True if loan has multiple tranches (fixed + floating)"
                    },
                    "split_portions": {
                        "type": "array",
                        "description": "Details of each split portion if applicable",
                        "items": {
                            "type": "object",
                            "properties": {
                                "portion_name": {"type": "string"},
                                "loan_type": {"type": "string"},
                                "balance": {"type": "number"},
                                "interest_rate": {"type": "number"},
                                "fixed_until": {"type": ["string", "null"], "format": "date"}
                            }
                        }
                    },
                    "property_address": {
                        "type": ["string", "null"],
                        "description": "Security property address if shown"
                    },
                    "original_loan_amount": {
                        "type": ["number", "null"],
                        "description": "Original loan amount if shown"
                    },
                    "current_interest_rate": {
                        "type": ["number", "null"],
                        "description": "Current interest rate as percentage (e.g., 6.79)"
                    },
                    "is_offset_account": {
                        "type": "boolean",
                        "description": "True if this is an offset arrangement"
                    },
                    "linked_offset_account": {
                        "type": ["string", "null"],
                        "description": "Linked offset account number if applicable"
                    },
                    "offset_balance": {
                        "type": ["number", "null"],
                        "description": "Offset account balance if applicable"
                    },
                    "effective_balance_for_interest": {
                        "type": ["number", "null"],
                        "description": "Loan balance minus offset (what interest is actually calculated on)"
                    }
                }
            },
            "statement_period": {
                "type": "object",
                "required": ["start_date", "end_date"],
                "properties": {
                    "start_date": {
                        "type": "string",
                        "format": "date"
                    },
                    "end_date": {
                        "type": "string",
                        "format": "date"
                    },
                    "opening_balance": {
                        "type": "number",
                        "description": "Principal balance at start of period"
                    },
                    "closing_balance": {
                        "type": "number",
                        "description": "Principal balance at end of period"
                    },
                    "is_partial_year": {
                        "type": "boolean",
                        "description": "True if loan was drawn/discharged during period"
                    },
                    "loan_drawn_date": {
                        "type": ["string", "null"],
                        "format": "date",
                        "description": "Date loan was first drawn if during this period"
                    }
                }
            },
            "interest_summary": {
                "type": "object",
                "required": ["total_interest_charged", "interest_calculation_method"],
                "properties": {
                    "total_interest_charged": {
                        "type": "number",
                        "description": "Total interest charged in statement period (GROSS - before any credits)"
                    },
                    "total_interest_credits": {
                        "type": "number",
                        "description": "Any interest credits/refunds (DO NOT subtract for tax purposes)"
                    },
                    "net_interest_per_statement": {
                        "type": "number",
                        "description": "Net interest shown on statement (may differ from tax-deductible amount)"
                    },
                    "interest_calculation_method": {
                        "type": "string",
                        "enum": ["daily", "monthly", "not_specified"],
                        "description": "How interest is calculated"
                    },
                    "average_daily_balance": {
                        "type": ["number", "null"],
                        "description": "Average daily balance if shown"
                    },
                    "effective_interest_rate": {
                        "type": ["number", "null"],
                        "description": "Effective annual rate if different from nominal"
                    },
                    "interest_frequency": {
                        "type": "string",
                        "enum": ["weekly", "fortnightly", "monthly", "other"],
                        "description": "How often interest is charged/debited"
                    }
                }
            },
            "transactions": {
                "type": "array",
                "description": "All loan transactions (interest charges, repayments, etc.)",
                "items": {
                    "type": "object",
                    "required": ["date", "description", "amount", "transaction_type"],
                    "properties": {
                        "date": {
                            "type": "string",
                            "format": "date"
                        },
                        "description": {
                            "type": "string",
                            "description": "Full transaction description"
                        },
                        "amount": {
                            "type": "number",
                            "description": "Amount (positive = increases balance, negative = decreases)"
                        },
                        "balance": {
                            "type": ["number", "null"],
                            "description": "Balance after transaction"
                        },
                        "transaction_type": {
                            "type": "string",
                            "enum": [
                                "interest_charge",
                                "interest_credit",
                                "interest_adjustment",
                                "principal_repayment",
                                "combined_repayment",
                                "drawdown",
                                "redraw",
                                "fee",
                                "offset_benefit",
                                "other"
                            ]
                        },
                        "interest_component": {
                            "type": ["number", "null"],
                            "description": "Interest portion if combined repayment"
                        },
                        "principal_component": {
                            "type": ["number", "null"],
                            "description": "Principal portion if combined repayment"
                        },
                        "tax_treatment": {
                            "type": "object",
                            "properties": {
                                "include_in_interest_deduction": {
                                    "type": "boolean",
                                    "description": "True if this should be included in interest deduction"
                                },
                                "exclusion_reason": {
                                    "type": ["string", "null"],
                                    "description": "Why excluded if applicable"
                                }
                            }
                        }
                    }
                }
            },
            "extraction_metadata": {
                "type": "object",
                "required": ["pages_processed", "is_complete"],
                "properties": {
                    "pages_processed": {"type": "integer"},
                    "is_complete": {"type": "boolean"},
                    "data_quality_score": {"type": "number", "minimum": 0, "maximum": 1}
                }
            }
        }
    }
}
```

---

### 4. Settlement Statement Extraction Tool (COMPREHENSIVE - Year 1 Critical)

```python
SETTLEMENT_STATEMENT_EXTRACTION_TOOL = {
    "name": "extract_settlement_statement",
    "description": "Extract ALL details from settlement statement - CRITICAL for Year 1 tax calculations",
    "input_schema": {
        "type": "object",
        "required": [
            "settlement_info",
            "financial_details",
            "apportionments",
            "fees_and_costs",
            "year1_tax_calculations",
            "all_line_items"
        ],
        "properties": {
            "settlement_info": {
                "type": "object",
                "required": ["settlement_date", "property_address"],
                "properties": {
                    "settlement_date": {
                        "type": "string",
                        "format": "date",
                        "description": "Settlement date (YYYY-MM-DD) - CRITICAL for Year 1 determination"
                    },
                    "property_address": {
                        "type": "string",
                        "description": "Full property address"
                    },
                    "contract_date": {
                        "type": ["string", "null"],
                        "format": "date",
                        "description": "Date agreement was signed"
                    },
                    "vendor_name": {"type": "string"},
                    "purchaser_name": {"type": "string"},
                    "solicitor_firm": {"type": "string"},
                    "title_reference": {"type": ["string", "null"]}
                }
            },
            "financial_details": {
                "type": "object",
                "required": ["purchase_price"],
                "properties": {
                    "purchase_price": {"type": "number"},
                    "deposit_amount": {"type": ["number", "null"]},
                    "deposit_held_by": {
                        "type": ["string", "null"],
                        "enum": [null, "vendor_solicitor", "purchaser_solicitor", "agent", "stakeholder"]
                    },
                    "balance_due_on_settlement": {"type": "number"},
                    "amount_required_to_settle": {"type": "number"}
                }
            },
            "apportionments": {
                "type": "object",
                "description": "All apportionments between vendor and purchaser",
                "properties": {
                    "rates": {
                        "type": "object",
                        "properties": {
                            "period_from": {"type": "string", "format": "date"},
                            "period_to": {"type": "string", "format": "date"},
                            "annual_amount": {"type": ["number", "null"]},
                            "vendor_paid_to_date": {"type": ["number", "null"]},
                            "vendor_instalment": {
                                "type": ["number", "null"],
                                "description": "Amount vendor paid as instalment"
                            },
                            "vendor_credit": {
                                "type": ["number", "null"],
                                "description": "Credit to vendor (purchaser pays this portion)"
                            },
                            "settlement_apportionment": {
                                "type": "number",
                                "description": "CRITICAL: Amount for Year 1 rates calculation"
                            },
                            "calculation_method": {
                                "type": "string",
                                "description": "How the apportionment was calculated"
                            }
                        }
                    },
                    "water_rates": {
                        "type": "object",
                        "properties": {
                            "adjustment_amount": {"type": ["number", "null"]},
                            "direction": {
                                "type": "string",
                                "enum": ["vendor_credit", "purchaser_credit"]
                            },
                            "meter_reading_date": {"type": ["string", "null"], "format": "date"},
                            "is_metered": {"type": "boolean"}
                        }
                    },
                    "body_corporate": {
                        "type": "object",
                        "properties": {
                            "levy_amount": {"type": ["number", "null"]},
                            "period_from": {"type": ["string", "null"], "format": "date"},
                            "period_to": {"type": ["string", "null"], "format": "date"},
                            "adjustment_amount": {"type": ["number", "null"]},
                            "operating_fund_portion": {"type": ["number", "null"]},
                            "reserve_fund_portion": {"type": ["number", "null"]}
                        }
                    },
                    "insurance": {
                        "type": "object",
                        "properties": {
                            "premium_amount": {"type": ["number", "null"]},
                            "period_from": {"type": ["string", "null"], "format": "date"},
                            "period_to": {"type": ["string", "null"], "format": "date"},
                            "adjustment_amount": {"type": ["number", "null"]},
                            "policy_type": {"type": ["string", "null"]}
                        }
                    }
                }
            },
            "fees_and_costs": {
                "type": "object",
                "properties": {
                    "legal_fees": {
                        "type": "object",
                        "required": ["total"],
                        "properties": {
                            "professional_fee": {"type": ["number", "null"]},
                            "disbursements": {"type": ["number", "null"]},
                            "gst": {"type": ["number", "null"]},
                            "total": {
                                "type": "number",
                                "description": "Total legal fees including GST"
                            }
                        }
                    },
                    "registration_fees": {"type": ["number", "null"]},
                    "search_fees": {"type": ["number", "null"]},
                    "land_registry_fees": {"type": ["number", "null"]},
                    "agent_commission": {"type": ["number", "null"]},
                    "other_fees": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "description": {"type": "string"},
                                "amount": {"type": "number"}
                            }
                        }
                    }
                }
            },
            "interest_on_deposit": {
                "type": "object",
                "description": "Interest earned on deposit - Year 1 special handling",
                "properties": {
                    "amount": {
                        "type": ["number", "null"],
                        "description": "Interest earned on deposit"
                    },
                    "days": {"type": ["integer", "null"]},
                    "rate": {"type": ["number", "null"]},
                    "treatment_note": {
                        "type": "string",
                        "default": "Nets against Year 1 expenses, NOT shown as separate income"
                    }
                }
            },
            "bank_contribution": {
                "type": "object",
                "description": "Bank/lender contribution to settlement - MUST be verified against bank statement",
                "properties": {
                    "mentioned_in_settlement": {
                        "type": "boolean",
                        "description": "True if any bank contribution mentioned"
                    },
                    "amount": {
                        "type": ["number", "null"],
                        "description": "Bank contribution amount if shown"
                    },
                    "lender_name": {"type": ["string", "null"]},
                    "verification_required": {
                        "type": "boolean",
                        "default": true,
                        "description": "Always true - must verify against bank statement"
                    },
                    "verification_note": {
                        "type": "string",
                        "default": "Check bank statement for exact contribution amount"
                    }
                }
            },
            "year1_tax_calculations": {
                "type": "object",
                "description": "PRE-COMPUTED Year 1 tax calculations from settlement data",
                "required": ["rates_deductible", "legal_fees_deductible"],
                "properties": {
                    "rates_deductible": {
                        "type": "object",
                        "description": "Year 1 Rates = Settlement apportionment + Instalments paid - Vendor credit",
                        "required": ["settlement_apportionment", "total_from_settlement"],
                        "properties": {
                            "settlement_apportionment": {
                                "type": "number",
                                "description": "Rates apportionment from settlement"
                            },
                            "vendor_instalment": {
                                "type": "number",
                                "description": "Rates instalment paid by vendor before settlement"
                            },
                            "vendor_credit": {
                                "type": "number",
                                "description": "Credit back to vendor for overpayment"
                            },
                            "total_from_settlement": {
                                "type": "number",
                                "description": "Total rates amount from settlement = apportionment + instalment - credit"
                            },
                            "calculation_formula": {
                                "type": "string",
                                "description": "Shows the calculation: e.g., '1234.56 + 500 - 200 = 1534.56'"
                            }
                        }
                    },
                    "legal_fees_deductible": {
                        "type": "object",
                        "description": "Legal fees under $10k are fully deductible",
                        "properties": {
                            "total_legal_fees": {"type": "number"},
                            "is_under_10k_threshold": {
                                "type": "boolean",
                                "description": "True if total < $10,000"
                            },
                            "deductible_amount": {
                                "type": "number",
                                "description": "Amount deductible (full amount if under $10k, else 0)"
                            },
                            "note": {
                                "type": "string",
                                "default": "Legal fees < $10k are fully deductible; >= $10k are capital"
                            }
                        }
                    },
                    "interest_on_deposit_treatment": {
                        "type": "object",
                        "properties": {
                            "amount": {"type": "number"},
                            "treatment": {
                                "type": "string",
                                "default": "Nets against Year 1 interest expense"
                            }
                        }
                    }
                }
            },
            "all_line_items": {
                "type": "array",
                "description": "EVERY line item in settlement statement in document order",
                "items": {
                    "type": "object",
                    "required": ["line_number", "description", "amount"],
                    "properties": {
                        "line_number": {
                            "type": "integer",
                            "description": "Sequential line number (1, 2, 3, ...)"
                        },
                        "description": {
                            "type": "string",
                            "description": "Exact description as shown"
                        },
                        "amount": {"type": "number"},
                        "direction": {
                            "type": "string",
                            "enum": ["debit", "credit"],
                            "description": "Debit = purchaser pays, Credit = purchaser receives"
                        },
                        "category": {
                            "type": "string",
                            "enum": [
                                "purchase_price",
                                "deposit",
                                "rates_apportionment",
                                "water_apportionment",
                                "body_corporate_apportionment",
                                "insurance_apportionment",
                                "legal_fees",
                                "disbursements",
                                "registration_fees",
                                "interest_on_deposit",
                                "bank_contribution",
                                "other"
                            ]
                        },
                        "is_deductible": {
                            "type": "boolean",
                            "description": "True if this is a deductible expense"
                        },
                        "deductible_amount": {
                            "type": ["number", "null"],
                            "description": "Amount that is deductible (may differ from amount)"
                        },
                        "notes": {"type": ["string", "null"]}
                    }
                }
            },
            "extraction_metadata": {
                "type": "object",
                "required": ["pages_processed", "is_complete", "data_quality_score"],
                "properties": {
                    "pages_processed": {"type": "integer"},
                    "is_complete": {"type": "boolean"},
                    "data_quality_score": {"type": "number", "minimum": 0, "maximum": 1},
                    "missing_critical_fields": {
                        "type": "array",
                        "items": {"type": "string"}
                    }
                }
            }
        }
    }
}
```

---

### 5. Property Manager Statement Extraction Tool (COMPREHENSIVE)

```python
PM_STATEMENT_EXTRACTION_TOOL = {
    "name": "extract_pm_statement",
    "description": "Extract ALL data from property manager statement - handles ALL PM formats",
    "input_schema": {
        "type": "object",
        "required": [
            "pm_info",
            "statement_period",
            "transactions",
            "summary",
            "extraction_metadata"
        ],
        "properties": {
            "pm_info": {
                "type": "object",
                "required": ["pm_company", "property_address"],
                "properties": {
                    "pm_company": {"type": "string"},
                    "pm_contact": {"type": ["string", "null"]},
                    "property_address": {"type": "string"},
                    "owner_name": {"type": "string"},
                    "management_fee_rate": {
                        "type": ["number", "null"],
                        "description": "Management fee as percentage (e.g., 7.5)"
                    },
                    "letting_fee_policy": {"type": ["string", "null"]},
                    "is_gst_registered": {
                        "type": "boolean",
                        "description": "True if PM company is GST registered"
                    }
                }
            },
            "statement_period": {
                "type": "object",
                "required": ["start_date", "end_date"],
                "properties": {
                    "start_date": {"type": "string", "format": "date"},
                    "end_date": {"type": "string", "format": "date"},
                    "opening_balance": {"type": "number"},
                    "closing_balance": {"type": "number"}
                }
            },
            "transactions": {
                "type": "array",
                "description": "ALL transactions from PM statement",
                "items": {
                    "type": "object",
                    "required": ["date", "description", "amount", "transaction_type", "categorization"],
                    "properties": {
                        "date": {"type": "string", "format": "date"},
                        "description": {"type": "string"},
                        "amount": {"type": "number"},
                        "balance": {"type": ["number", "null"]},
                        "transaction_type": {
                            "type": "string",
                            "enum": ["receipt", "expense", "payment_to_owner", "other"]
                        },
                        "categorization": {
                            "type": "object",
                            "required": ["category", "is_deductible", "exclude_from_pl"],
                            "properties": {
                                "category": {
                                    "type": "string",
                                    "enum": [
                                        "rental_income",
                                        "letting_fee",
                                        "management_fee",
                                        "inspection_fee",
                                        "advertising",
                                        "repairs_maintenance",
                                        "rates_payment",
                                        "insurance_payment",
                                        "water_rates",
                                        "body_corporate",
                                        "tribunal_costs",
                                        "bond_received",
                                        "bond_released",
                                        "bond_claim",
                                        "payment_to_owner",
                                        "sundry",
                                        "unknown"
                                    ]
                                },
                                "is_deductible": {"type": "boolean"},
                                "exclude_from_pl": {
                                    "type": "boolean",
                                    "description": "True for bonds, transfers, payments to owner"
                                },
                                "exclusion_reason": {"type": ["string", "null"]}
                            }
                        },
                        "gst_handling": {
                            "type": "object",
                            "properties": {
                                "amount_includes_gst": {"type": "boolean"},
                                "gst_amount": {"type": ["number", "null"]},
                                "amount_excl_gst": {"type": ["number", "null"]}
                            }
                        },
                        "tenant_reference": {"type": ["string", "null"]},
                        "property_reference": {"type": ["string", "null"]}
                    }
                }
            },
            "monthly_breakdown": {
                "type": "object",
                "description": "Summary by month",
                "additionalProperties": {
                    "type": "object",
                    "properties": {
                        "rental_income": {"type": "number"},
                        "total_expenses": {"type": "number"},
                        "net_to_owner": {"type": "number"}
                    }
                }
            },
            "summary": {
                "type": "object",
                "required": ["total_rent_collected", "total_expenses", "total_paid_to_owner"],
                "properties": {
                    "total_rent_collected": {"type": "number"},
                    "total_management_fees": {"type": "number"},
                    "total_letting_fees": {"type": "number"},
                    "total_expenses": {"type": "number"},
                    "total_paid_to_owner": {"type": "number"},
                    "bond_held": {"type": ["number", "null"]},
                    "expenses_by_category": {
                        "type": "object",
                        "additionalProperties": {"type": "number"}
                    }
                }
            },
            "extraction_metadata": {
                "type": "object",
                "required": ["pages_processed", "is_complete"],
                "properties": {
                    "pages_processed": {"type": "integer"},
                    "is_complete": {"type": "boolean"},
                    "data_quality_score": {"type": "number", "minimum": 0, "maximum": 1}
                }
            }
        }
    }
}
```

---

### 6. Supporting Document Extraction Tools

```python
# Code Compliance Certificate - CRITICAL for interest deductibility
CCC_EXTRACTION_TOOL = {
    "name": "extract_ccc",
    "description": "Extract CCC details - determines interest deductibility (100% if >= 27 March 2020)",
    "input_schema": {
        "type": "object",
        "required": ["certificate_number", "issue_date", "property_address", "council_name", "interest_deductibility_result"],
        "properties": {
            "certificate_number": {"type": "string"},
            "issue_date": {
                "type": "string",
                "format": "date",
                "description": "CRITICAL: Must be >= 27 March 2020 for 100% interest deductibility"
            },
            "property_address": {"type": "string"},
            "council_name": {"type": "string"},
            "building_consent_number": {"type": ["string", "null"]},
            "building_type": {
                "type": "string",
                "enum": ["residential", "commercial", "mixed", "other"]
            },
            "interest_deductibility_result": {
                "type": "object",
                "required": ["qualifies_as_new_build", "deductibility_percentage"],
                "properties": {
                    "qualifies_as_new_build": {
                        "type": "boolean",
                        "description": "True if issue_date >= 2020-03-27"
                    },
                    "deductibility_percentage": {
                        "type": "integer",
                        "enum": [80, 100],
                        "description": "100 if new build, 80 if existing (FY25)"
                    },
                    "calculation_note": {
                        "type": "string",
                        "description": "Explanation of deductibility determination"
                    }
                }
            }
        }
    }
}

# Landlord Insurance - CRITICAL: must be landlord-specific, not home & contents
INSURANCE_EXTRACTION_TOOL = {
    "name": "extract_insurance",
    "description": "Extract landlord insurance policy details - verify it's landlord insurance NOT home & contents",
    "input_schema": {
        "type": "object",
        "required": ["insurer", "policy_number", "policy_type", "premium_amount", "is_valid_landlord_insurance"],
        "properties": {
            "insurer": {"type": "string"},
            "policy_number": {"type": "string"},
            "policy_type": {
                "type": "string",
                "enum": ["landlord_insurance", "rental_property", "investment_property", "home_and_contents", "other"],
                "description": "CRITICAL: home_and_contents is NOT deductible"
            },
            "is_valid_landlord_insurance": {
                "type": "boolean",
                "description": "True ONLY if policy_type is landlord/rental/investment - NOT home & contents"
            },
            "policy_type_evidence": {
                "type": "string",
                "description": "Quote from document proving policy type"
            },
            "property_address": {"type": "string"},
            "premium_amount": {"type": "number"},
            "premium_gst": {"type": ["number", "null"]},
            "premium_period": {
                "type": "string",
                "enum": ["annual", "monthly", "quarterly", "other"]
            },
            "period_start": {"type": "string", "format": "date"},
            "period_end": {"type": "string", "format": "date"},
            "sum_insured": {"type": ["number", "null"]},
            "excess_amount": {"type": ["number", "null"]},
            "cover_types": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Types of cover included (building, contents, loss of rent, etc.)"
            },
            "is_mortgage_protection": {
                "type": "boolean",
                "description": "True if this is mortgage protection insurance (different deductibility rules)"
            }
        }
    }
}

# Depreciation Schedule
DEPRECIATION_EXTRACTION_TOOL = {
    "name": "extract_depreciation",
    "description": "Extract depreciation schedule - must pro-rate for partial year ownership",
    "input_schema": {
        "type": "object",
        "required": ["provider", "property_address", "valuation_date", "annual_depreciation", "year1_calculation"],
        "properties": {
            "provider": {"type": "string"},
            "property_address": {"type": "string"},
            "valuation_date": {"type": "string", "format": "date"},
            "total_depreciable_value": {"type": "number"},
            "annual_depreciation": {
                "type": "number",
                "description": "FULL year depreciation (pro-rate if partial year)"
            },
            "depreciation_method": {
                "type": "string",
                "enum": ["diminishing_value", "straight_line", "mixed"]
            },
            "assets": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "asset_name": {"type": "string"},
                        "asset_value": {"type": "number"},
                        "depreciation_rate": {"type": "number"},
                        "annual_depreciation": {"type": "number"}
                    }
                }
            },
            "year1_calculation": {
                "type": "object",
                "description": "Pro-rata calculation for partial year",
                "properties": {
                    "full_year_amount": {"type": "number"},
                    "months_owned": {"type": "integer"},
                    "pro_rata_factor": {"type": "string"},
                    "deductible_amount": {"type": "number"}
                }
            }
        }
    }
}

# Body Corporate - CRITICAL: must split operating vs reserve fund
BODY_CORPORATE_EXTRACTION_TOOL = {
    "name": "extract_body_corporate",
    "description": "Extract body corporate levy - MUST split operating (deductible) from reserve fund (NOT deductible)",
    "input_schema": {
        "type": "object",
        "required": ["bc_number", "total_levy", "operating_fund_levy", "reserve_fund_levy"],
        "properties": {
            "bc_number": {"type": "string"},
            "unit_number": {"type": ["string", "null"]},
            "property_address": {"type": "string"},
            "levy_period": {"type": "string"},
            "period_start": {"type": "string", "format": "date"},
            "period_end": {"type": "string", "format": "date"},
            "operating_fund_levy": {
                "type": "number",
                "description": "Operating fund = DEDUCTIBLE (maintenance, admin, insurance)"
            },
            "reserve_fund_levy": {
                "type": "number",
                "description": "Reserve/sinking fund = NOT deductible (capital improvements)"
            },
            "total_levy": {"type": "number"},
            "deductible_portion": {
                "type": "number",
                "description": "ONLY operating fund is deductible"
            },
            "non_deductible_portion": {
                "type": "number",
                "description": "Reserve fund amount (excluded from P&L)"
            },
            "payment_frequency": {
                "type": "string",
                "enum": ["monthly", "quarterly", "annual"]
            },
            "split_explicitly_shown": {
                "type": "boolean",
                "description": "True if document explicitly shows operating vs reserve split"
            },
            "split_source": {
                "type": "string",
                "description": "Where the split information came from in the document"
            }
        }
    }
}

# Rates Notice
RATES_EXTRACTION_TOOL = {
    "name": "extract_rates",
    "description": "Extract council rates - Year 1 must combine with settlement apportionment",
    "input_schema": {
        "type": "object",
        "required": ["council_name", "property_address", "total_rates"],
        "properties": {
            "council_name": {"type": "string"},
            "property_address": {"type": "string"},
            "valuation_reference": {"type": ["string", "null"]},
            "rating_year": {"type": "string"},
            "capital_value": {"type": ["number", "null"]},
            "land_value": {"type": ["number", "null"]},
            "total_annual_rates": {"type": "number"},
            "rates_breakdown": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "rate_type": {"type": "string"},
                        "amount": {"type": "number"}
                    }
                }
            },
            "instalment_schedule": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "instalment_number": {"type": "integer"},
                        "due_date": {"type": "string", "format": "date"},
                        "amount": {"type": "number"},
                        "status": {
                            "type": "string",
                            "enum": ["paid", "unpaid", "unknown"]
                        }
                    }
                }
            },
            "year1_note": {
                "type": "string",
                "description": "Reminder: Year 1 rates = Settlement apportionment + Instalments paid - Vendor credit"
            }
        }
    }
}

# Water Rates
WATER_RATES_EXTRACTION_TOOL = {
    "name": "extract_water_rates",
    "description": "Extract water rates - separate from council rates",
    "input_schema": {
        "type": "object",
        "required": ["supplier_name", "property_address", "total_amount"],
        "properties": {
            "supplier_name": {"type": "string"},
            "property_address": {"type": "string"},
            "account_number": {"type": ["string", "null"]},
            "billing_period_start": {"type": "string", "format": "date"},
            "billing_period_end": {"type": "string", "format": "date"},
            "fixed_charges": {"type": ["number", "null"]},
            "usage_charges": {"type": ["number", "null"]},
            "total_amount": {"type": "number"},
            "is_gst_inclusive": {"type": "boolean"},
            "gst_amount": {"type": ["number", "null"]},
            "reading_type": {
                "type": "string",
                "enum": ["actual", "estimated"]
            }
        }
    }
}

# Compliance Documents (Healthy Homes, Meth Test, Smoke Alarm, LIM)
COMPLIANCE_DOC_EXTRACTION_TOOL = {
    "name": "extract_compliance_doc",
    "description": "Extract compliance document details - all are deductible as due diligence",
    "input_schema": {
        "type": "object",
        "required": ["document_type", "property_address", "date", "result"],
        "properties": {
            "document_type": {
                "type": "string",
                "enum": ["healthy_homes", "meth_test", "smoke_alarm", "lim_report", "building_inspection", "other"]
            },
            "property_address": {"type": "string"},
            "date": {"type": "string", "format": "date"},
            "provider": {"type": "string"},
            "result": {
                "type": "string",
                "enum": ["pass", "fail", "compliant", "non_compliant", "clear", "detected", "information_only"]
            },
            "cost": {"type": ["number", "null"]},
            "is_deductible": {
                "type": "boolean",
                "default": true,
                "description": "Compliance costs are generally deductible as due diligence"
            },
            "expiry_date": {"type": ["string", "null"], "format": "date"},
            "key_findings": {
                "type": "array",
                "items": {"type": "string"}
            }
        }
    }
}

# Invoice Extraction
INVOICE_EXTRACTION_TOOL = {
    "name": "extract_invoice",
    "description": "Extract invoice/receipt details for maintenance, repairs, or services",
    "input_schema": {
        "type": "object",
        "required": ["vendor_name", "invoice_date", "total_amount", "description", "category"],
        "properties": {
            "vendor_name": {"type": "string"},
            "vendor_gst_number": {"type": ["string", "null"]},
            "invoice_number": {"type": ["string", "null"]},
            "invoice_date": {"type": "string", "format": "date"},
            "property_address": {"type": ["string", "null"]},
            "description": {"type": "string"},
            "line_items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "description": {"type": "string"},
                        "quantity": {"type": ["number", "null"]},
                        "unit_price": {"type": ["number", "null"]},
                        "amount": {"type": "number"}
                    }
                }
            },
            "subtotal": {"type": ["number", "null"]},
            "gst_amount": {"type": ["number", "null"]},
            "total_amount": {"type": "number"},
            "is_gst_inclusive": {"type": "boolean"},
            "category": {
                "type": "string",
                "enum": [
                    "repairs_maintenance",
                    "cleaning",
                    "gardening",
                    "pest_control",
                    "plumbing",
                    "electrical",
                    "painting",
                    "roofing",
                    "appliance_repair",
                    "capital_improvement",
                    "professional_services",
                    "other"
                ]
            },
            "is_capital_expense": {
                "type": "boolean",
                "description": "True if this is a capital improvement (not immediately deductible)"
            },
            "capital_expense_note": {
                "type": ["string", "null"],
                "description": "Explanation if flagged as capital"
            }
        }
    }
}
```

---

## Implementation Code Changes

### 1. Batch Processing for Financial Documents

```python
# document_processor.py - NEW batch processing
async def _analyze_document(self, db, document, filename, context):
    processed = await self.file_handler.process_file(document.file_path, filename)

    # For financial documents, process ALL pages in batches
    if self._is_financial_document(processed) and processed.image_paths:
        return await self._analyze_financial_document_batched(
            db, document, processed, filename, context
        )

    # For non-financial documents, use standard processing with configurable limit
    image_data = None
    if processed.image_paths:
        max_pages = settings.MAX_PAGES_NON_FINANCIAL  # New config: default 10
        image_data = await self.claude_client.prepare_image_data(
            processed.image_paths[:max_pages]
        )

    return await self._classify_and_extract(db, document, processed, image_data, context)

async def _analyze_financial_document_batched(self, db, document, processed, filename, context):
    """Process financial documents page by page to capture ALL transactions."""
    all_transactions = []
    batch_size = settings.FINANCIAL_DOC_BATCH_SIZE  # New config: default 5

    for i in range(0, len(processed.image_paths), batch_size):
        batch_pages = processed.image_paths[i:i + batch_size]
        batch_num = (i // batch_size) + 1
        total_batches = (len(processed.image_paths) + batch_size - 1) // batch_size

        logger.info(f"Processing batch {batch_num}/{total_batches} for {filename}")

        image_data = await self.claude_client.prepare_image_data(batch_pages)

        # Extract transactions from this batch
        batch_result = await self.claude_client.extract_transactions_batch(
            text_content=processed.text_content,
            image_data=image_data,
            context=context,
            batch_info={"batch": batch_num, "total": total_batches}
        )

        all_transactions.extend(batch_result.get("transactions", []))

        # Rate limit between batches
        await asyncio.sleep(settings.BATCH_DELAY_SECONDS)  # New config: default 1.0

    # Deduplicate transactions (in case of page overlap)
    unique_transactions = self._deduplicate_transactions(all_transactions)

    return unique_transactions
```

### 2. Enhanced Retry Logic with Rate Limiting

```python
# claude_client.py - NEW rate limiting
import asyncio
import random
from contextlib import asynccontextmanager

class ClaudeClient:
    def __init__(self, api_key=None, model=None):
        self.client = AsyncAnthropic(
            api_key=api_key or settings.ANTHROPIC_API_KEY,
            timeout=httpx.Timeout(300.0, connect=30.0),
        )
        self.model = model or settings.CLAUDE_MODEL

        # Enhanced retry configuration
        self.max_retries = settings.CLAUDE_MAX_RETRIES  # default 5
        self.base_delay = settings.CLAUDE_BASE_DELAY    # default 2.0
        self.max_delay = settings.CLAUDE_MAX_DELAY      # default 60.0

        # Rate limiting
        self._semaphore = asyncio.Semaphore(settings.CLAUDE_CONCURRENT_REQUESTS)  # default 3
        self._last_request_time = 0
        self._min_request_interval = settings.CLAUDE_MIN_REQUEST_INTERVAL  # default 0.5

    @asynccontextmanager
    async def _rate_limited(self):
        """Context manager for rate-limited API calls."""
        async with self._semaphore:
            # Ensure minimum interval between requests
            now = asyncio.get_event_loop().time()
            time_since_last = now - self._last_request_time
            if time_since_last < self._min_request_interval:
                await asyncio.sleep(self._min_request_interval - time_since_last)

            try:
                yield
            finally:
                self._last_request_time = asyncio.get_event_loop().time()

    async def _call_with_retry(self, create_func):
        """Call Claude API with enhanced retry logic and exponential backoff."""
        last_error = None

        for attempt in range(self.max_retries):
            try:
                async with self._rate_limited():
                    response = await create_func()

                if hasattr(response, "usage"):
                    logger.info(
                        f"Claude API: {response.usage.input_tokens} in, "
                        f"{response.usage.output_tokens} out"
                    )

                return response

            except RateLimitError as e:
                last_error = e
                # Extract retry-after header if available
                retry_after = getattr(e, 'retry_after', None)
                if retry_after:
                    wait_time = float(retry_after)
                else:
                    # Exponential backoff with jitter
                    wait_time = min(
                        self.base_delay * (2 ** attempt) + random.uniform(0, 1),
                        self.max_delay
                    )

                logger.warning(
                    f"Rate limited, waiting {wait_time:.1f}s "
                    f"(attempt {attempt + 1}/{self.max_retries})"
                )
                await asyncio.sleep(wait_time)

            except APIError as e:
                last_error = e
                if attempt < self.max_retries - 1:
                    wait_time = self.base_delay * (attempt + 1)
                    logger.warning(
                        f"API error, retrying in {wait_time}s: {e}"
                    )
                    await asyncio.sleep(wait_time)
                else:
                    raise

        raise last_error
```

### 3. Tool Use Implementation

```python
# claude_client.py - NEW Tool Use implementation
async def analyze_document_with_tool_use(self, content, image_data, context, tool_schema):
    """Analyze document using Tool Use for guaranteed schema compliance."""

    message_content = self._build_message_content(content, image_data)

    system_prompt = self._build_extraction_prompt(context)

    response = await self._call_with_retry(
        lambda: self.client.messages.create(
            model=self.model,
            max_tokens=16384,
            temperature=0.1,
            system=system_prompt,
            tools=[tool_schema],
            tool_choice={"type": "tool", "name": tool_schema["name"]},
            messages=[{"role": "user", "content": message_content}],
        )
    )

    # Tool Use guarantees the response matches schema
    for block in response.content:
        if block.type == "tool_use" and block.name == tool_schema["name"]:
            # Input is guaranteed to match schema
            return block.input

    raise ValueError("No tool use response received")
```

### 4. Multi-Pass Extraction for Complex Documents

```python
# document_processor.py - NEW multi-pass extraction
async def _extract_settlement_statement_multipass(self, db, document, processed, context):
    """
    Multi-pass extraction for settlement statements to ensure completeness.

    Pass 1: Classification + basic extraction
    Pass 2: Detailed field extraction with verification
    Pass 3: (if needed) Gap filling for missing critical fields
    """

    # Pass 1: Initial extraction
    pass1_result = await self.claude_client.analyze_document_with_tool_use(
        processed.text_content,
        await self.claude_client.prepare_image_data(processed.image_paths),
        context,
        SETTLEMENT_STATEMENT_EXTRACTION_TOOL
    )

    # Check for critical missing fields
    critical_fields = [
        "settlement_date", "purchase_price", "property_address",
        "rates_apportionment", "legal_fees"
    ]

    missing_critical = []
    for field in critical_fields:
        if not self._has_field_value(pass1_result, field):
            missing_critical.append(field)

    if not missing_critical:
        return pass1_result

    # Pass 2: Targeted extraction for missing fields
    logger.info(f"Pass 2: Extracting missing fields: {missing_critical}")

    pass2_result = await self.claude_client.extract_targeted_fields(
        processed.text_content,
        await self.claude_client.prepare_image_data(processed.image_paths),
        context,
        missing_fields=missing_critical,
        existing_data=pass1_result
    )

    # Merge results
    merged = self._merge_extraction_results(pass1_result, pass2_result)

    # Pass 3: Verification
    verification = await self._verify_settlement_extraction(merged, processed)

    if verification.get("issues"):
        merged["extraction_warnings"] = verification["issues"]

    return merged
```

---

## NZ Tax Validation Rules

| Rule | Schema Enforcement |
|------|-------------------|
| Year 1 Rates = Settlement apportionment + Instalments - Vendor credit | `year1_tax_calculations.rates_deductible` in Settlement schema |
| Depreciation must be pro-rated for partial year | `year1_calculation` in Depreciation schema |
| Interest on deposit nets against expense, NOT shown as income | `interest_on_deposit.treatment_note` in Settlement schema |
| BC operating fund only - reserve is capital | Split fields in Body Corporate schema |
| Legal fees <$10k are fully deductible | `legal_fees_deductible.is_under_10k_threshold` in Settlement schema |
| New build (CCC >= 27 March 2020) = 100% interest deductible | `interest_deductibility_result` in CCC schema |
| Existing property (FY25) = 80% interest deductible | Applied in interest calculation |
| Home & contents insurance is NOT deductible | `is_valid_landlord_insurance` in Insurance schema |
| Bond received/released is NOT income/expense | `exclude_from_pl` flag in PM Statement transactions |
| Interest credits DO NOT reduce deductible interest | `tax_treatment.include_in_interest_deduction` in Loan schema |

---

## Configuration Settings

```python
class Phase1Settings(BaseSettings):
    # Page Processing
    MAX_PAGES_CLASSIFICATION: int = 5
    MAX_PAGES_NON_FINANCIAL: int = 10
    FINANCIAL_DOC_BATCH_SIZE: int = 5
    BATCH_DELAY_SECONDS: float = 1.0

    # Claude API Rate Limiting
    CLAUDE_MAX_RETRIES: int = 5
    CLAUDE_BASE_DELAY: float = 2.0
    CLAUDE_MAX_DELAY: float = 60.0
    CLAUDE_CONCURRENT_REQUESTS: int = 3
    CLAUDE_MIN_REQUEST_INTERVAL: float = 0.5

    # Extraction Settings
    ENABLE_MULTIPASS_EXTRACTION: bool = True
    ENABLE_EXTRACTION_VERIFICATION: bool = True
    VERIFICATION_BALANCE_TOLERANCE: float = 1.0

    # Financial Document Detection
    FINANCIAL_DOCUMENT_TYPES: List[str] = [
        "bank_statement",
        "loan_statement",
        "property_manager_statement"
    ]

    # Transaction Flagging Thresholds
    FLAG_THRESHOLD_LARGE_PAYMENT: float = 500.0
    FLAG_THRESHOLD_VERY_LARGE: float = 2000.0
    FLAG_THRESHOLD_CRITICAL: float = 5000.0

    # NZ Tax Rules
    NEW_BUILD_CCC_DATE_THRESHOLD: str = "2020-03-27"
    FY25_INTEREST_DEDUCTIBILITY_EXISTING: float = 0.80
    FY26_INTEREST_DEDUCTIBILITY_EXISTING: float = 1.00
    LEGAL_FEES_DEDUCTIBLE_THRESHOLD: float = 10000.0
```

---

## Files to Create/Modify

### New Files

| File | Purpose |
|------|---------|
| `app/services/phase1_document_intake/schemas/__init__.py` | Export all schemas |
| `app/services/phase1_document_intake/schemas/classification.py` | Document classification schema |
| `app/services/phase1_document_intake/schemas/bank_statement.py` | Bank statement extraction schema |
| `app/services/phase1_document_intake/schemas/loan_statement.py` | Loan statement extraction schema |
| `app/services/phase1_document_intake/schemas/settlement.py` | Settlement statement extraction schema |
| `app/services/phase1_document_intake/schemas/pm_statement.py` | PM statement extraction schema |
| `app/services/phase1_document_intake/schemas/supporting_docs.py` | CCC, insurance, depreciation, BC, rates, etc. |
| `app/services/phase1_document_intake/batch_processor.py` | Multi-page batch processing logic |
| `app/services/phase1_document_intake/rate_limiter.py` | API rate limiting implementation |
| `app/services/phase1_document_intake/verification.py` | Extraction verification logic |
| `app/services/phase1_document_intake/nz_tax_rules.py` | NZ tax rule validation |

### Modified Files

| File | Changes |
|------|---------|
| `document_processor.py` | Add batch processing, multi-pass extraction |
| `claude_client.py` | Add Tool Use support, enhanced rate limiting |
| `config.py` | Add new configuration settings |
| `prompts.py` | Refactor into schema-driven prompts |

---

## Implementation Priority

| Priority | Change | Impact |
|----------|--------|--------|
| **P0** | Remove 5-page limit | Stops data loss immediately |
| **P0** | Enhanced retry/rate limiting | Enables 50+ document processing |
| **P1** | Tool Use for classification | Ensures consistent output |
| **P1** | Configuration settings | Enables tuning without code changes |
| **P2** | Multi-pass extraction | Improves extraction accuracy |
| **P2** | Tool Use for all doc types | Full schema enforcement |
| **P3** | Verification pass | Catches extraction errors |

---

## Success Metrics

| Metric | Target |
|--------|--------|
| Transaction extraction completeness | 100% (all transactions from all pages) |
| Balance validation accuracy | 99%+ (opening + transactions = closing) |
| Classification accuracy | 98%+ |
| Schema compliance | 100% (Tool Use guarantees) |
| Year 1 rates calculation accuracy | 100% (includes settlement apportionment) |
| Interest deductibility determination | 100% (CCC date validated) |
| BC operating/reserve split | 100% (when shown in document) |
| Processing time (50 docs) | < 10 minutes |
| API failure rate | < 1% after retries |

---

## Risk Mitigation

| Risk | Mitigation |
|------|------------|
| Increased API costs (more calls) | Batch processing reduces total calls vs per-page |
| Longer processing time | Parallel processing where safe, progress indicators |
| Tool Use not supported | Fallback to current JSON parsing |
| Breaking existing functionality | Feature flags for gradual rollout |

---

## Summary: Current vs Proposed

| Aspect | Current | Proposed |
|--------|---------|----------|
| **Page Limit** | 5 pages hard-coded | Configurable, batch processing for financial docs |
| **Retries** | 3 retries, 1-4s waits | 5 retries, exponential backoff up to 60s |
| **Rate Limiting** | None | Semaphore + minimum interval |
| **Output Format** | Free-form JSON | Tool Use with enforced schemas |
| **Extraction** | Single pass | Multi-pass with verification |
| **Configuration** | Minimal | Full control over processing behavior |
| **Schema Enforcement** | Hope-based | Guaranteed by Tool Use |

---

## UI Changes

### 1. Upload Page Progress Updates (`upload.html`)

**Current State:** Progress shows per-document status only.

**Required Changes:** Add batch-level progress for financial documents.

```javascript
// Add to stageNames in uploadForm()
const stageNames = {
    // ... existing stages ...
    'extracting_batch': 'Extracting Data',
    'verification': 'Verifying Extraction',
    'batch_merge': 'Merging Results'
};

// Add batch progress display
<div x-show="currentBatch > 0" class="mt-2 text-xs text-gray-500">
    Processing batch <span x-text="currentBatch"></span> of <span x-text="totalBatches"></span>
    (pages <span x-text="batchPages"></span>)
</div>
```

**New SSE Event Handling:**
```javascript
// Handle batch progress events
if (data.stage === 'extracting_batch') {
    this.currentBatch = data.batch;
    this.totalBatches = data.total_batches;
    this.batchPages = data.pages;
    this.currentStageDetail = `Extracting ${data.document_name} - Batch ${data.batch}/${data.total_batches}`;
}

if (data.stage === 'verification') {
    this.currentStageDetail = `Verifying ${data.document_name} - ${data.checks_passed} checks passed`;
    if (data.warnings > 0) {
        this.verificationWarnings.push({
            document: data.document_name,
            warnings: data.warnings
        });
    }
}
```

### 2. Result Page Updates (`result.html`)

**Add Extraction Metadata Display:**
```html
<!-- Document Card Enhancement -->
<div class="bg-white shadow rounded-lg p-4">
    <div class="flex justify-between items-start">
        <div>
            <h4 class="font-medium">{{ doc.original_filename }}</h4>
            <p class="text-sm text-gray-500">{{ doc.document_type }}</p>
        </div>
        <div class="text-right text-xs text-gray-400">
            <p>{{ doc.extracted_data.extraction_metadata.pages_processed }} pages</p>
            <p>Quality: {{ "%.0f"|format(doc.extracted_data.extraction_metadata.data_quality_score * 100) }}%</p>
        </div>
    </div>

    <!-- Verification Warnings -->
    {% if doc.extracted_data.extraction_warnings %}
    <div class="mt-3 p-2 bg-yellow-50 rounded text-sm">
        <p class="font-medium text-yellow-800">Extraction Warnings:</p>
        <ul class="mt-1 text-yellow-700 text-xs">
            {% for warning in doc.extracted_data.extraction_warnings %}
            <li>{{ warning }}</li>
            {% endfor %}
        </ul>
    </div>
    {% endif %}
</div>
```

**Add Year 1 Settlement Calculations Display:**
```html
<!-- Year 1 Settlement Breakdown (when settlement statement present) -->
{% if settlement_doc and settlement_doc.extracted_data.year1_tax_calculations %}
<div class="bg-blue-50 border border-blue-200 rounded-lg p-6 mt-6">
    <h3 class="text-lg font-medium text-blue-900 mb-4">Year 1 Tax Calculations (from Settlement)</h3>

    <div class="grid grid-cols-2 gap-6">
        <!-- Rates Calculation -->
        <div>
            <h4 class="font-medium text-gray-700 mb-2">Rates Deductible</h4>
            <table class="w-full text-sm">
                <tr>
                    <td class="text-gray-600">Settlement Apportionment</td>
                    <td class="text-right">${{ "%.2f"|format(settlement_doc.extracted_data.year1_tax_calculations.rates_deductible.settlement_apportionment) }}</td>
                </tr>
                <tr>
                    <td class="text-gray-600">+ Vendor Instalment</td>
                    <td class="text-right">${{ "%.2f"|format(settlement_doc.extracted_data.year1_tax_calculations.rates_deductible.vendor_instalment or 0) }}</td>
                </tr>
                <tr>
                    <td class="text-gray-600">- Vendor Credit</td>
                    <td class="text-right">-${{ "%.2f"|format(settlement_doc.extracted_data.year1_tax_calculations.rates_deductible.vendor_credit or 0) }}</td>
                </tr>
                <tr class="font-medium border-t">
                    <td>Total Deductible</td>
                    <td class="text-right">${{ "%.2f"|format(settlement_doc.extracted_data.year1_tax_calculations.rates_deductible.total_from_settlement) }}</td>
                </tr>
            </table>
        </div>

        <!-- Legal Fees -->
        <div>
            <h4 class="font-medium text-gray-700 mb-2">Legal Fees</h4>
            <table class="w-full text-sm">
                <tr>
                    <td class="text-gray-600">Total Legal Fees</td>
                    <td class="text-right">${{ "%.2f"|format(settlement_doc.extracted_data.year1_tax_calculations.legal_fees_deductible.total_legal_fees) }}</td>
                </tr>
                <tr>
                    <td class="text-gray-600">Under $10k Threshold?</td>
                    <td class="text-right">{{ "Yes" if settlement_doc.extracted_data.year1_tax_calculations.legal_fees_deductible.is_under_10k_threshold else "No" }}</td>
                </tr>
                <tr class="font-medium border-t">
                    <td>Deductible Amount</td>
                    <td class="text-right">${{ "%.2f"|format(settlement_doc.extracted_data.year1_tax_calculations.legal_fees_deductible.deductible_amount) }}</td>
                </tr>
            </table>
        </div>
    </div>
</div>
{% endif %}
```

**Add Special Handling Indicators in Transaction List:**
```html
<!-- Transaction row with special handling indicators -->
<tr class="{% if txn.special_handling.is_combined_bond_rent %}bg-yellow-50{% endif %}">
    <td>{{ txn.date }}</td>
    <td>
        {{ txn.description }}
        {% if txn.special_handling.is_combined_bond_rent %}
        <span class="ml-2 px-2 py-0.5 text-xs bg-yellow-200 text-yellow-800 rounded">
            Bond+Rent: Split needed
        </span>
        {% endif %}
        {% if txn.special_handling.is_loan_repayment_total %}
        <span class="ml-2 px-2 py-0.5 text-xs bg-blue-200 text-blue-800 rounded">
            Interest+Principal
        </span>
        {% endif %}
    </td>
    <td class="text-right">{{ "%.2f"|format(txn.amount) }}</td>
    <td>{{ txn.categorization.suggested_category }}</td>
</tr>
```

### 3. Workings Page Updates (`workings.html`)

**Add Interest Analysis Summary:**
```html
<!-- Interest Analysis from Bank Statement -->
{% if interest_analysis %}
<div class="bg-white shadow rounded-lg p-6 mt-6">
    <h3 class="text-lg font-medium text-primary mb-4">Interest Analysis</h3>

    <div class="grid grid-cols-3 gap-4">
        <div class="text-center p-4 bg-gray-50 rounded">
            <p class="text-2xl font-bold text-gray-900">${{ "%.2f"|format(interest_analysis.total_interest_debits) }}</p>
            <p class="text-sm text-gray-500">Total Interest Debits</p>
        </div>
        <div class="text-center p-4 bg-gray-50 rounded">
            <p class="text-2xl font-bold text-gray-900">{{ interest_analysis.interest_transaction_count }}</p>
            <p class="text-sm text-gray-500">Interest Transactions</p>
        </div>
        <div class="text-center p-4 bg-gray-50 rounded">
            <p class="text-2xl font-bold text-gray-900">{{ interest_analysis.interest_frequency }}</p>
            <p class="text-sm text-gray-500">Payment Frequency</p>
        </div>
    </div>

    {% if interest_analysis.offset_account_detected %}
    <div class="mt-4 p-3 bg-blue-50 rounded">
        <p class="text-sm text-blue-800">
            <strong>Offset Account Detected:</strong> {{ interest_analysis.offset_notes }}
        </p>
    </div>
    {% endif %}
</div>
{% endif %}
```

---

## Database Migrations

### 1. Document Model Updates

```python
# app/models/db_models.py - Add to Document model

class Document(Base):
    # ... existing fields ...

    # NEW: Extraction metadata fields
    pages_processed = Column(Integer, nullable=True)
    extraction_batches = Column(Integer, nullable=True)
    verification_status = Column(String(20), nullable=True)  # 'passed', 'warnings', 'failed'
    data_quality_score = Column(Float, nullable=True)
    extraction_warnings = Column(JSONB, nullable=True)  # List of warning strings

    # NEW: Processing metadata
    processing_started_at = Column(DateTime(timezone=True), nullable=True)
    processing_completed_at = Column(DateTime(timezone=True), nullable=True)
    api_calls_used = Column(Integer, nullable=True)
```

### 2. Migration Script

**Create file: `alembic/versions/xxx_add_extraction_metadata.py`**

```python
"""Add extraction metadata to documents

Revision ID: xxx
Revises: [previous_revision]
Create Date: 2024-XX-XX

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers
revision = 'xxx_add_extraction_metadata'
down_revision = '[previous_revision]'
branch_labels = None
depends_on = None


def upgrade():
    # Add new columns to documents table
    op.add_column('documents', sa.Column('pages_processed', sa.Integer(), nullable=True))
    op.add_column('documents', sa.Column('extraction_batches', sa.Integer(), nullable=True))
    op.add_column('documents', sa.Column('verification_status', sa.String(20), nullable=True))
    op.add_column('documents', sa.Column('data_quality_score', sa.Float(), nullable=True))
    op.add_column('documents', sa.Column('extraction_warnings', JSONB(), nullable=True))
    op.add_column('documents', sa.Column('processing_started_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('documents', sa.Column('processing_completed_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('documents', sa.Column('api_calls_used', sa.Integer(), nullable=True))

    # Add index for verification status (for filtering documents needing review)
    op.create_index('ix_documents_verification_status', 'documents', ['verification_status'])


def downgrade():
    op.drop_index('ix_documents_verification_status', 'documents')
    op.drop_column('documents', 'api_calls_used')
    op.drop_column('documents', 'processing_completed_at')
    op.drop_column('documents', 'processing_started_at')
    op.drop_column('documents', 'extraction_warnings')
    op.drop_column('documents', 'data_quality_score')
    op.drop_column('documents', 'verification_status')
    op.drop_column('documents', 'extraction_batches')
    op.drop_column('documents', 'pages_processed')
```

### 3. Run Migration

```bash
# Generate migration (if using autogenerate)
poetry run alembic revision --autogenerate -m "Add extraction metadata to documents"

# Apply migration
poetry run alembic upgrade head
```

---

## Prompt Instructions

### 1. Classification System Prompt

```python
CLASSIFICATION_SYSTEM_PROMPT = """You are an expert document classifier for New Zealand rental property tax returns.

Your task is to:
1. Identify the document type from the provided categories
2. Extract key identifiers (dates, amounts, account numbers)
3. Verify the property address matches the context provided
4. Flag any issues that would block processing

DOCUMENT TYPES:
- bank_statement: Bank account transaction listing
- loan_statement: Mortgage/loan account statement
- settlement_statement: Property purchase settlement statement (solicitor's statement)
- depreciation_schedule: Asset depreciation schedule from valuer
- body_corporate: Body corporate levy notice/statement
- property_manager_statement: Property management company statement
- lim_report: Land Information Memorandum
- healthy_homes: Healthy homes compliance assessment
- meth_test: Methamphetamine test report
- smoke_alarm: Smoke alarm compliance certificate
- ccc: Code Compliance Certificate (building)
- landlord_insurance: Landlord/rental property insurance policy
- rates: Council rates notice
- water_rates: Water rates/charges notice
- maintenance_invoice: Invoice for repairs/maintenance
- other: Relevant but doesn't fit categories
- invalid: Not relevant to rental property tax

CRITICAL FLAGS TO CHECK:
1. ADDRESS MISMATCH: Document address doesn't match property context
2. WRONG INSURANCE TYPE: "Home and Contents" is NOT landlord insurance - flag as critical
3. PERSONAL DOCUMENT: Document appears to be personal, not rental-related
4. DATE OUTSIDE TAX YEAR: Document dates don't fall within the tax year
5. INCOMPLETE DOCUMENT: Document appears truncated or missing pages

Use the classify_document tool to provide your analysis."""
```

### 2. Bank Statement Extraction Prompt

```python
BANK_STATEMENT_EXTRACTION_PROMPT = """You are extracting ALL transaction data from a New Zealand bank statement for rental property tax purposes.

CRITICAL REQUIREMENTS:
1. Extract EVERY SINGLE transaction - do not skip any
2. Preserve descriptions EXACTLY as shown
3. Identify the transaction type (debit/credit) correctly
4. Suggest appropriate tax categories for each transaction

CATEGORY GUIDANCE:
- rental_income: Regular payments from tenants or property manager
- water_rates_recovered: Tenant reimbursement for water
- bank_contribution: One-time bank contribution (often on settlement)
- interest_debit: Loan interest charges (DEDUCTIBLE - include ALL)
- interest_credit: Interest refunds/adjustments (DO NOT subtract from debits)
- principal_repayment: Loan principal portion (NOT deductible)
- council_rates: Council rates payments
- water_rates: Water charges
- body_corporate_operating: BC operating fund (DEDUCTIBLE)
- body_corporate_reserve: BC reserve/sinking fund (NOT deductible)
- landlord_insurance: Insurance premiums
- agent_fees: Property management fees
- repairs_maintenance: Repairs and maintenance
- transfer_between_accounts: Internal transfers (EXCLUDE from P&L)
- personal_expense: Personal items (EXCLUDE from P&L)
- bond_received/bond_released: Bonds (EXCLUDE - not income/expense)

SPECIAL HANDLING FLAGS:
1. is_combined_bond_rent: If a deposit appears to be bond + rent combined (often first payment from tenant)
   - Look for amounts that are unusual multiples of weekly rent
   - Flag and suggest split amounts

2. is_loan_repayment_total: If payment to bank includes both interest and principal
   - Common with table loans where combined payment is made
   - Flag so loan statement can provide the split

3. is_offset_related: If transaction relates to offset account benefit
   - Look for "offset" in description or unusual interest patterns

INTEREST ANALYSIS:
- Sum ALL interest DEBITS (these are the deductible amounts)
- Track interest CREDITS separately (DO NOT net against debits)
- Note the frequency (weekly/fortnightly/monthly)
- Identify which loan account each interest charge relates to

BALANCE VALIDATION:
- Opening balance + Credits - Debits should equal Closing balance
- Flag any variance for review

Use the extract_bank_statement tool to provide the complete extraction."""
```

### 3. Loan Statement Extraction Prompt

```python
LOAN_STATEMENT_EXTRACTION_PROMPT = """You are extracting mortgage/loan data from a New Zealand loan statement for rental property tax purposes.

CRITICAL REQUIREMENTS:
1. Extract the TOTAL interest charged (gross, before any credits)
2. Identify loan type (table loan, revolving credit, interest-only, etc.)
3. Track interest vs principal split for each repayment
4. Note if offset account is linked

INTEREST RULES:
- Interest DEBITS are deductible
- Interest CREDITS do NOT reduce the deductible amount
- Capitalised interest is still deductible when charged
- Offset account benefit reduces future interest but doesn't affect deductibility

LOAN TYPES:
- table_loan: Fixed regular repayments (interest + principal combined)
- revolving_credit: Flexible facility, interest charged on balance
- interest_only: Only interest paid, no principal reduction
- floating: Variable rate loan
- fixed: Fixed rate for a term
- split: Multiple tranches (e.g., part fixed, part floating)
- offset: Loan with linked offset account

FOR SPLIT LOANS:
- Extract details for EACH portion separately
- Track interest rate and balance for each
- Note fixed term expiry dates

TAX TREATMENT PER TRANSACTION:
- include_in_interest_deduction: true for interest debits
- include_in_interest_deduction: false for credits, principal, fees

Use the extract_loan_statement tool to provide the complete extraction."""
```

### 4. Settlement Statement Extraction Prompt

```python
SETTLEMENT_STATEMENT_EXTRACTION_PROMPT = """You are extracting data from a New Zealand property settlement statement for Year 1 rental property tax purposes.

THIS IS CRITICAL FOR YEAR 1 TAX CALCULATIONS.

EXTRACT EVERYTHING IN DOCUMENT ORDER:
- Every line item as it appears
- All apportionments (rates, water, body corporate, insurance)
- All fees (legal, disbursements, registration)
- Interest on deposit if shown
- Bank contribution if mentioned

YEAR 1 RATES CALCULATION (CRITICAL):
Formula: Settlement Apportionment + Vendor Instalment - Vendor Credit = Total Deductible

Example:
- Settlement says "Rates apportionment: Vendor credit $1,234.56"
- Vendor had paid instalment of $500 before settlement
- Vendor gets credit back of $200 for overpayment
- Deductible = $1,234.56 + $500 - $200 = $1,534.56

LEGAL FEES RULE:
- If total legal fees (including GST) < $10,000: Fully deductible
- If total legal fees >= $10,000: NOT deductible (capital expense)
- Always extract the exact amount for threshold check

INTEREST ON DEPOSIT:
- If shown, this NETS against Year 1 expenses
- It is NOT shown as separate income
- Extract amount and days if available

BANK CONTRIBUTION:
- If settlement mentions bank/lender contribution, extract the amount
- ALWAYS flag for verification against bank statement
- This is taxable income in Year 1

APPORTIONMENTS:
- Rates: Extract period, annual amount, purchaser's share
- Water: Note if metered, extract adjustment direction
- Body Corporate: Note operating vs reserve fund split if shown
- Insurance: Extract if vendor's policy transferred

Use the extract_settlement_statement tool to provide the complete extraction."""
```

### 5. Property Manager Statement Extraction Prompt

```python
PM_STATEMENT_EXTRACTION_PROMPT = """You are extracting data from a New Zealand property manager statement for rental property tax purposes.

EXTRACT ALL TRANSACTIONS including:
- Rent collected
- Management fees
- Letting fees
- Inspection fees
- Repairs paid on behalf
- Payments to owner
- Bond movements

CRITICAL EXCLUSIONS (exclude_from_pl = true):
- bond_received: Bond taken from tenant - NOT income
- bond_released: Bond returned to tenant - NOT expense
- payment_to_owner: Transfer to landlord - NOT income/expense

GST HANDLING:
- Check if PM company is GST registered (usually shown on statement)
- If GST registered, amounts usually shown GST-inclusive
- Extract GST component where shown
- For GST-registered landlords, need GST-exclusive amounts

CATEGORY MAPPING:
- rental_income: Rent received from tenants
- letting_fee: Fee for finding new tenant (deductible)
- management_fee: Ongoing management percentage (deductible)
- inspection_fee: Property inspection charges (deductible)
- repairs_maintenance: Repairs paid by PM (deductible)
- tribunal_costs: Tenancy tribunal costs (deductible)
- advertising: Advertising for tenants (deductible)

MONTHLY BREAKDOWN:
- If statement covers multiple months, provide monthly totals
- This helps with P&L workings reconciliation

Use the extract_pm_statement tool to provide the complete extraction."""
```

### 6. CCC Extraction Prompt (Interest Deductibility)

```python
CCC_EXTRACTION_PROMPT = """You are extracting data from a New Zealand Code Compliance Certificate for rental property tax purposes.

THE CCC DATE IS CRITICAL FOR INTEREST DEDUCTIBILITY:
- CCC issued ON OR AFTER 27 March 2020: Property qualifies as "new build"
  → 100% interest deductible
- CCC issued BEFORE 27 March 2020: Property is "existing"
  → 80% interest deductible (FY25), phasing to 100% (FY26+)

EXTRACT:
1. Certificate number
2. Issue date (THE CRITICAL DATE)
3. Property address
4. Council name
5. Building consent number if shown
6. Building type (residential/commercial/mixed)

AUTOMATICALLY CALCULATE:
- qualifies_as_new_build: true if issue_date >= "2020-03-27"
- deductibility_percentage: 100 if new build, 80 if existing (for FY25)

Use the extract_ccc tool to provide the extraction with interest deductibility determination."""
```

### 7. Insurance Extraction Prompt (Critical Validation)

```python
INSURANCE_EXTRACTION_PROMPT = """You are extracting data from an insurance document for New Zealand rental property tax purposes.

CRITICAL VALIDATION - POLICY TYPE:
This is the MOST IMPORTANT check. Look for explicit policy type wording:

VALID (Deductible):
- "Landlord Insurance"
- "Rental Property Insurance"
- "Investment Property Insurance"
- "Residential Rental Insurance"

INVALID (NOT Deductible - flag as CRITICAL):
- "Home and Contents"
- "Homeowner Insurance"
- "Contents Insurance"
- "House Insurance" (without rental/landlord qualifier)

Look for evidence in:
- Policy title/name
- Schedule heading
- Certificate of insurance
- Cover type descriptions

EXTRACT:
1. Insurer name
2. Policy number
3. Policy type (from enum)
4. is_valid_landlord_insurance (boolean - CRITICAL)
5. policy_type_evidence (quote from document proving type)
6. Premium amount
7. GST component if shown
8. Period start/end
9. Sum insured
10. Cover types included

If the document is HOME AND CONTENTS insurance:
- Set is_valid_landlord_insurance = false
- This is a BLOCKING issue - document should be flagged

Use the extract_insurance tool to provide the extraction with validation."""
```

### 8. Body Corporate Extraction Prompt (Operating vs Reserve Split)

```python
BODY_CORPORATE_EXTRACTION_PROMPT = """You are extracting data from a New Zealand body corporate levy document for rental property tax purposes.

CRITICAL: OPERATING vs RESERVE FUND SPLIT

TAX TREATMENT:
- Operating Fund: DEDUCTIBLE (covers maintenance, insurance, admin)
- Reserve/Sinking Fund: NOT DEDUCTIBLE (capital improvements)

LOOK FOR SPLIT IN:
- Levy breakdown section
- Budget allocation
- Line items showing "Operating" vs "Reserve" or "Sinking"

IF SPLIT IS SHOWN:
- Extract both amounts separately
- deductible_portion = operating_fund_levy
- non_deductible_portion = reserve_fund_levy

IF SPLIT IS NOT SHOWN:
- Extract total levy
- Set split_explicitly_shown = false
- Note: May need to request BC budget for accurate split

COMMON SPLIT PATTERNS:
- "Operating Fund Levy: $X" + "Reserve Fund Contribution: $Y"
- "Annual Levy: $X (Operating: 70%, Reserve: 30%)"
- Budget document showing fund allocations

Use the extract_body_corporate tool to provide the extraction with fund split."""
```

---

## Error Handling & Recovery

### 1. Batch Processing Error Handling

```python
# document_processor.py

async def _analyze_financial_document_batched(self, db, document, processed, filename, context):
    """Process financial documents with error recovery."""
    all_transactions = []
    batch_size = settings.FINANCIAL_DOC_BATCH_SIZE
    failed_batches = []

    total_batches = (len(processed.image_paths) + batch_size - 1) // batch_size

    for i in range(0, len(processed.image_paths), batch_size):
        batch_pages = processed.image_paths[i:i + batch_size]
        batch_num = (i // batch_size) + 1

        try:
            logger.info(f"Processing batch {batch_num}/{total_batches} for {filename}")

            image_data = await self.claude_client.prepare_image_data(batch_pages)

            batch_result = await self.claude_client.extract_transactions_batch(
                text_content=processed.text_content,
                image_data=image_data,
                context=context,
                batch_info={"batch": batch_num, "total": total_batches}
            )

            all_transactions.extend(batch_result.get("transactions", []))

            # Save progress after each successful batch
            await self._save_batch_progress(db, document, batch_num, all_transactions)

        except RateLimitError as e:
            # Retry with longer delay
            logger.warning(f"Rate limit on batch {batch_num}, retrying after extended delay")
            await asyncio.sleep(30)

            try:
                batch_result = await self.claude_client.extract_transactions_batch(
                    text_content=processed.text_content,
                    image_data=image_data,
                    context=context,
                    batch_info={"batch": batch_num, "total": total_batches}
                )
                all_transactions.extend(batch_result.get("transactions", []))
            except Exception as retry_error:
                failed_batches.append({
                    "batch": batch_num,
                    "pages": f"{i+1}-{min(i+batch_size, len(processed.image_paths))}",
                    "error": str(retry_error)
                })

        except Exception as e:
            logger.error(f"Batch {batch_num} failed: {e}")
            failed_batches.append({
                "batch": batch_num,
                "pages": f"{i+1}-{min(i+batch_size, len(processed.image_paths))}",
                "error": str(e)
            })
            # Continue with next batch instead of failing completely
            continue

        await asyncio.sleep(settings.BATCH_DELAY_SECONDS)

    # Handle partial extraction
    if failed_batches:
        logger.warning(f"Document {filename} had {len(failed_batches)} failed batches")
        return {
            "transactions": all_transactions,
            "extraction_warnings": [
                f"Batch {fb['batch']} (pages {fb['pages']}) failed: {fb['error']}"
                for fb in failed_batches
            ],
            "partial_extraction": True,
            "failed_batches": failed_batches
        }

    return {
        "transactions": self._deduplicate_transactions(all_transactions),
        "partial_extraction": False
    }

async def _save_batch_progress(self, db, document, batch_num, transactions):
    """Save progress after each batch for recovery."""
    document.extracted_data = document.extracted_data or {}
    document.extracted_data["partial_transactions"] = [
        {"date": t.get("date"), "amount": t.get("amount"), "description": t.get("description")[:50]}
        for t in transactions[-20:]  # Save last 20 for reference
    ]
    document.extracted_data["batches_completed"] = batch_num
    await db.commit()
```

### 2. Tool Use Fallback

```python
# claude_client.py

async def analyze_document_with_tool_use(self, content, image_data, context, tool_schema):
    """Analyze document using Tool Use with fallback to JSON parsing."""

    try:
        response = await self._call_with_retry(
            lambda: self.client.messages.create(
                model=self.model,
                max_tokens=16384,
                temperature=0.1,
                system=self._build_extraction_prompt(context),
                tools=[tool_schema],
                tool_choice={"type": "tool", "name": tool_schema["name"]},
                messages=[{"role": "user", "content": self._build_message_content(content, image_data)}],
            )
        )

        # Extract tool use response
        for block in response.content:
            if block.type == "tool_use" and block.name == tool_schema["name"]:
                return block.input

        raise ValueError("No tool use response received")

    except Exception as tool_error:
        logger.warning(f"Tool Use failed, falling back to JSON parsing: {tool_error}")

        # Fallback to traditional JSON extraction
        return await self._extract_with_json_fallback(content, image_data, context, tool_schema)

async def _extract_with_json_fallback(self, content, image_data, context, tool_schema):
    """Fallback extraction using traditional JSON parsing."""

    # Build prompt that requests JSON matching the schema
    schema_description = json.dumps(tool_schema["input_schema"], indent=2)

    fallback_prompt = f"""Extract data from this document and return ONLY valid JSON matching this schema:

{schema_description}

Return ONLY the JSON object, no markdown formatting or explanation."""

    response = await self._call_with_retry(
        lambda: self.client.messages.create(
            model=self.model,
            max_tokens=16384,
            temperature=0.1,
            system=fallback_prompt,
            messages=[{"role": "user", "content": self._build_message_content(content, image_data)}],
        )
    )

    response_text = response.content[0].text

    # Clean up response
    if "```json" in response_text:
        response_text = response_text.split("```json")[1].split("```")[0].strip()
    elif "```" in response_text:
        response_text = response_text.split("```")[1].split("```")[0].strip()

    return json.loads(response_text)
```

### 3. Verification Error Handling

```python
# verification.py

async def verify_extraction(self, document, extracted_data):
    """Verify extraction completeness with graceful error handling."""

    warnings = []
    verification_status = "passed"

    try:
        # Balance validation (for bank statements)
        if document.document_type == "bank_statement":
            balance_check = self._verify_balance(extracted_data)
            if not balance_check["valid"]:
                warnings.append(f"Balance variance: ${balance_check['variance']:.2f}")
                verification_status = "warnings"

        # Required fields check
        missing_fields = self._check_required_fields(document.document_type, extracted_data)
        if missing_fields:
            warnings.extend([f"Missing field: {f}" for f in missing_fields])
            verification_status = "warnings"

        # Date range check
        date_issues = self._check_date_ranges(extracted_data)
        if date_issues:
            warnings.extend(date_issues)
            verification_status = "warnings"

    except Exception as e:
        logger.error(f"Verification error for document {document.id}: {e}")
        warnings.append(f"Verification check failed: {str(e)}")
        verification_status = "error"

    return {
        "status": verification_status,
        "warnings": warnings,
        "checks_performed": ["balance", "required_fields", "date_ranges"],
        "checks_passed": len([w for w in warnings if not w])
    }
```

---

## Phase 2 Integration

### 1. Consuming Richer Phase 1 Data

```python
# transaction_processor.py - Updated to use Phase 1 extracted data

class TransactionProcessor:
    """Process transactions using Phase 1 extracted data."""

    async def process_tax_return(self, db: AsyncSession, tax_return_id: UUID) -> dict:
        """Process all documents for a tax return."""

        # Load documents with extracted data
        documents = await self._load_documents(db, tax_return_id)

        # Group by document type
        bank_statements = [d for d in documents if d.document_type == "bank_statement"]
        loan_statements = [d for d in documents if d.document_type == "loan_statement"]
        settlement_doc = next((d for d in documents if d.document_type == "settlement_statement"), None)

        # Use Phase 1 interest analysis instead of recalculating
        interest_summary = self._aggregate_interest_from_phase1(bank_statements, loan_statements)

        # Use Phase 1 Year 1 calculations from settlement
        year1_calculations = None
        if settlement_doc and settlement_doc.extracted_data:
            year1_calculations = settlement_doc.extracted_data.get("year1_tax_calculations")

        # Process transactions with Phase 1 categorizations as starting point
        transactions = await self._process_transactions_with_phase1_hints(
            db, documents, interest_summary, year1_calculations
        )

        return {
            "transactions": transactions,
            "interest_summary": interest_summary,
            "year1_calculations": year1_calculations
        }

    def _aggregate_interest_from_phase1(self, bank_statements, loan_statements):
        """Aggregate interest data from Phase 1 extractions."""

        total_interest_debits = 0
        total_interest_credits = 0
        interest_transactions = []

        # From bank statements
        for doc in bank_statements:
            if doc.extracted_data and "interest_analysis" in doc.extracted_data:
                analysis = doc.extracted_data["interest_analysis"]
                total_interest_debits += analysis.get("total_interest_debits", 0)
                total_interest_credits += analysis.get("total_interest_credits", 0)
                interest_transactions.extend(analysis.get("interest_transactions", []))

        # From loan statements (primary source for interest)
        for doc in loan_statements:
            if doc.extracted_data and "interest_summary" in doc.extracted_data:
                summary = doc.extracted_data["interest_summary"]
                # Loan statement is authoritative for total interest
                total_interest_debits = max(
                    total_interest_debits,
                    summary.get("total_interest_charged", 0)
                )

        return {
            "total_interest_debits": total_interest_debits,
            "total_interest_credits": total_interest_credits,
            "net_interest_expense": total_interest_debits,  # DO NOT subtract credits
            "transaction_count": len(interest_transactions),
            "source": "phase1_extraction"
        }

    async def _process_transactions_with_phase1_hints(
        self, db, documents, interest_summary, year1_calculations
    ):
        """Process transactions using Phase 1 categorizations as hints."""

        all_transactions = []

        for doc in documents:
            if not doc.extracted_data or "transactions" not in doc.extracted_data:
                continue

            for txn_data in doc.extracted_data["transactions"]:
                # Use Phase 1 categorization as starting point
                phase1_category = txn_data.get("categorization", {}).get("suggested_category")
                phase1_confidence = txn_data.get("categorization", {}).get("confidence", 0)

                # Check if needs review based on special handling
                special_handling = txn_data.get("special_handling", {})
                needs_manual_split = (
                    special_handling.get("is_combined_bond_rent") or
                    special_handling.get("is_loan_repayment_total")
                )

                transaction = Transaction(
                    document_id=doc.id,
                    tax_return_id=doc.tax_return_id,
                    date=txn_data["date"],
                    description=txn_data["description"],
                    amount=txn_data["amount"],
                    category=phase1_category,
                    confidence=phase1_confidence,
                    needs_review=needs_manual_split or phase1_confidence < 0.7,
                    phase1_data=txn_data  # Store full Phase 1 extraction
                )

                all_transactions.append(transaction)

        return all_transactions
```

### 2. Using Year 1 Settlement Calculations

```python
# workbook_generator.py - Use Phase 1 Year 1 calculations

class WorkbookGenerator:
    """Generate Lighthouse Financial workbook using Phase 1 data."""

    def _calculate_rates_deduction(self, tax_return, documents):
        """Calculate rates deduction using Phase 1 settlement extraction."""

        # Find settlement statement
        settlement = next(
            (d for d in documents if d.document_type == "settlement_statement"),
            None
        )

        if settlement and settlement.extracted_data:
            year1_calc = settlement.extracted_data.get("year1_tax_calculations", {})
            rates_data = year1_calc.get("rates_deductible", {})

            if rates_data:
                # Use pre-calculated value from Phase 1
                return {
                    "amount": rates_data.get("total_from_settlement", 0),
                    "breakdown": {
                        "settlement_apportionment": rates_data.get("settlement_apportionment", 0),
                        "vendor_instalment": rates_data.get("vendor_instalment", 0),
                        "vendor_credit": rates_data.get("vendor_credit", 0)
                    },
                    "calculation_formula": rates_data.get("calculation_formula"),
                    "source": "phase1_settlement_extraction"
                }

        # Fallback to transaction-based calculation
        return self._calculate_rates_from_transactions(tax_return)

    def _calculate_legal_fees_deduction(self, tax_return, documents):
        """Calculate legal fees deduction using Phase 1 extraction."""

        settlement = next(
            (d for d in documents if d.document_type == "settlement_statement"),
            None
        )

        if settlement and settlement.extracted_data:
            year1_calc = settlement.extracted_data.get("year1_tax_calculations", {})
            legal_data = year1_calc.get("legal_fees_deductible", {})

            if legal_data:
                return {
                    "total_legal_fees": legal_data.get("total_legal_fees", 0),
                    "is_under_threshold": legal_data.get("is_under_10k_threshold", False),
                    "deductible_amount": legal_data.get("deductible_amount", 0),
                    "threshold": 10000,
                    "source": "phase1_settlement_extraction"
                }

        return {"deductible_amount": 0, "source": "not_found"}
```

### 3. Using Special Handling Flags

```python
# transaction_categorizer.py - Handle Phase 1 special flags

class TransactionCategorizer:
    """Categorize transactions using Phase 1 data and RAG."""

    async def categorize_transaction(self, transaction, phase1_data=None):
        """Categorize with awareness of Phase 1 special handling."""

        special_handling = phase1_data.get("special_handling", {}) if phase1_data else {}

        # Handle combined bond + rent
        if special_handling.get("is_combined_bond_rent"):
            return {
                "category": "needs_split",
                "action_required": "split_bond_rent",
                "suggested_bond": special_handling.get("suggested_bond_amount"),
                "suggested_rent": special_handling.get("suggested_rent_amount"),
                "confidence": 0.0,  # Requires manual confirmation
                "message": "This transaction appears to be bond + rent combined. Please confirm the split."
            }

        # Handle loan repayment total
        if special_handling.get("is_loan_repayment_total"):
            linked_loan = special_handling.get("linked_loan_account")
            return {
                "category": "needs_split",
                "action_required": "split_interest_principal",
                "linked_loan_account": linked_loan,
                "confidence": 0.0,
                "message": f"This is a combined interest + principal payment. Check loan statement {linked_loan or 'for split'}."
            }

        # Use Phase 1 categorization if high confidence
        if phase1_data and phase1_data.get("categorization", {}).get("confidence", 0) >= 0.85:
            return {
                "category": phase1_data["categorization"]["suggested_category"],
                "confidence": phase1_data["categorization"]["confidence"],
                "source": "phase1_extraction",
                "reasoning": phase1_data["categorization"].get("reasoning")
            }

        # Otherwise, use multi-layer categorization
        return await self._categorize_with_rag(transaction)
```

---

## Testing Strategy

### 1. Unit Tests for Schemas

```python
# tests/test_schemas.py

import pytest
from jsonschema import validate, ValidationError
from app.services.phase1_document_intake.schemas import (
    DOCUMENT_CLASSIFICATION_TOOL,
    BANK_STATEMENT_EXTRACTION_TOOL,
    SETTLEMENT_STATEMENT_EXTRACTION_TOOL
)


class TestClassificationSchema:
    """Test document classification schema."""

    def test_valid_classification(self):
        """Test valid classification data passes schema."""
        valid_data = {
            "document_type": "bank_statement",
            "confidence": 0.95,
            "reasoning": "Document shows bank account transactions with running balance",
            "address_verification": {
                "address_found": "123 Main St, Auckland",
                "matches_context": True
            },
            "key_identifiers": {
                "document_date": "2024-03-31",
                "issuer_name": "ASB Bank"
            }
        }

        # Should not raise
        validate(valid_data, DOCUMENT_CLASSIFICATION_TOOL["input_schema"])

    def test_invalid_document_type_rejected(self):
        """Test invalid document type is rejected."""
        invalid_data = {
            "document_type": "not_a_valid_type",
            "confidence": 0.95,
            "reasoning": "Test",
            "address_verification": {"address_found": None, "matches_context": False},
            "key_identifiers": {}
        }

        with pytest.raises(ValidationError):
            validate(invalid_data, DOCUMENT_CLASSIFICATION_TOOL["input_schema"])

    def test_confidence_out_of_range_rejected(self):
        """Test confidence > 1.0 is rejected."""
        invalid_data = {
            "document_type": "bank_statement",
            "confidence": 1.5,  # Invalid
            "reasoning": "Test",
            "address_verification": {"address_found": None, "matches_context": False},
            "key_identifiers": {}
        }

        with pytest.raises(ValidationError):
            validate(invalid_data, DOCUMENT_CLASSIFICATION_TOOL["input_schema"])


class TestBankStatementSchema:
    """Test bank statement extraction schema."""

    def test_valid_extraction(self):
        """Test valid bank statement extraction."""
        valid_data = {
            "account_info": {
                "bank_name": "ASB",
                "account_number": "12-1234-1234567-00",
                "account_type": "transaction"
            },
            "statement_period": {
                "start_date": "2024-04-01",
                "end_date": "2024-04-30",
                "opening_balance": 1000.00,
                "closing_balance": 1500.00
            },
            "transactions": [
                {
                    "date": "2024-04-15",
                    "description": "RENT PAYMENT",
                    "amount": 500.00,
                    "transaction_type": "credit",
                    "categorization": {
                        "suggested_category": "rental_income",
                        "confidence": 0.95,
                        "pl_row": 1,
                        "is_deductible": False
                    }
                }
            ],
            "summary": {
                "total_transactions": 1,
                "total_credits": 500.00,
                "total_debits": 0,
                "balance_validated": True
            },
            "extraction_metadata": {
                "pages_processed": 2,
                "batch_number": 1,
                "is_complete": True
            }
        }

        validate(valid_data, BANK_STATEMENT_EXTRACTION_TOOL["input_schema"])

    def test_special_handling_flags(self):
        """Test special handling flags are accepted."""
        transaction_with_flags = {
            "date": "2024-04-01",
            "description": "DEPOSIT",
            "amount": 2500.00,
            "transaction_type": "credit",
            "categorization": {
                "suggested_category": "rental_income",
                "confidence": 0.6,
                "pl_row": 1,
                "is_deductible": False
            },
            "special_handling": {
                "is_combined_bond_rent": True,
                "suggested_bond_amount": 2000.00,
                "suggested_rent_amount": 500.00,
                "is_loan_repayment_total": False,
                "is_offset_related": False
            }
        }

        # Validate just the transaction portion
        # (in real test, would validate full schema)
        assert transaction_with_flags["special_handling"]["is_combined_bond_rent"] is True


class TestSettlementSchema:
    """Test settlement statement extraction schema."""

    def test_year1_calculations_present(self):
        """Test Year 1 calculations are properly structured."""
        valid_data = {
            "settlement_info": {
                "settlement_date": "2024-05-15",
                "property_address": "123 Test St"
            },
            "financial_details": {
                "purchase_price": 850000
            },
            "apportionments": {},
            "fees_and_costs": {
                "legal_fees": {"total": 2500}
            },
            "year1_tax_calculations": {
                "rates_deductible": {
                    "settlement_apportionment": 1234.56,
                    "vendor_instalment": 500,
                    "vendor_credit": 200,
                    "total_from_settlement": 1534.56,
                    "calculation_formula": "1234.56 + 500 - 200 = 1534.56"
                },
                "legal_fees_deductible": {
                    "total_legal_fees": 2500,
                    "is_under_10k_threshold": True,
                    "deductible_amount": 2500
                }
            },
            "all_line_items": []
        }

        validate(valid_data, SETTLEMENT_STATEMENT_EXTRACTION_TOOL["input_schema"])
```

### 2. Integration Tests for Batch Processing

```python
# tests/test_batch_processing.py

import pytest
from unittest.mock import AsyncMock, patch
from app.services.phase1_document_intake.document_processor import DocumentProcessor


class TestBatchProcessing:
    """Test batch processing for financial documents."""

    @pytest.fixture
    def processor(self):
        return DocumentProcessor()

    @pytest.mark.asyncio
    async def test_processes_all_batches(self, processor):
        """Test that all pages are processed in batches."""
        # Mock 15-page document (3 batches of 5)
        mock_processed = AsyncMock()
        mock_processed.image_paths = [f"page_{i}.png" for i in range(15)]
        mock_processed.text_content = "Bank statement content"

        with patch.object(processor.claude_client, 'extract_transactions_batch') as mock_extract:
            mock_extract.return_value = {"transactions": [{"date": "2024-01-01", "amount": 100}]}

            result = await processor._analyze_financial_document_batched(
                db=AsyncMock(),
                document=AsyncMock(),
                processed=mock_processed,
                filename="test.pdf",
                context={}
            )

            # Should have called extract 3 times (3 batches)
            assert mock_extract.call_count == 3

    @pytest.mark.asyncio
    async def test_handles_batch_failure_gracefully(self, processor):
        """Test partial extraction when a batch fails."""
        mock_processed = AsyncMock()
        mock_processed.image_paths = [f"page_{i}.png" for i in range(10)]

        call_count = 0
        async def mock_extract(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise Exception("API Error")
            return {"transactions": [{"date": "2024-01-01", "amount": 100}]}

        with patch.object(processor.claude_client, 'extract_transactions_batch', side_effect=mock_extract):
            result = await processor._analyze_financial_document_batched(
                db=AsyncMock(),
                document=AsyncMock(),
                processed=mock_processed,
                filename="test.pdf",
                context={}
            )

            # Should have partial_extraction flag
            assert result.get("partial_extraction") is True
            assert len(result.get("failed_batches", [])) == 1
            # Should still have transactions from successful batches
            assert len(result.get("transactions", [])) > 0
```

### 3. Test Fixtures

```python
# tests/fixtures/documents.py

"""Test fixtures for document extraction testing."""

SAMPLE_BANK_STATEMENT_EXTRACTION = {
    "account_info": {
        "bank_name": "ASB",
        "account_number": "12-3456-7890123-00",
        "account_name": "John Smith",
        "account_type": "transaction",
        "is_joint_account": False,
        "is_rental_dedicated": True
    },
    "statement_period": {
        "start_date": "2024-04-01",
        "end_date": "2024-04-30",
        "opening_balance": 5000.00,
        "closing_balance": 6250.00,
        "tax_year": "FY25"
    },
    "transactions": [
        {
            "date": "2024-04-01",
            "description": "RENT - J TENANT",
            "amount": 650.00,
            "balance": 5650.00,
            "transaction_type": "credit",
            "categorization": {
                "suggested_category": "rental_income",
                "confidence": 0.95,
                "pl_row": 1,
                "is_deductible": False
            }
        },
        {
            "date": "2024-04-05",
            "description": "COUNCIL RATES AUCKLAND",
            "amount": -450.00,
            "balance": 5200.00,
            "transaction_type": "debit",
            "categorization": {
                "suggested_category": "council_rates",
                "confidence": 0.92,
                "pl_row": 5,
                "is_deductible": True
            }
        },
        {
            "date": "2024-04-15",
            "description": "RENT - J TENANT",
            "amount": 650.00,
            "balance": 5850.00,
            "transaction_type": "credit",
            "categorization": {
                "suggested_category": "rental_income",
                "confidence": 0.95,
                "pl_row": 1,
                "is_deductible": False
            }
        },
        {
            "date": "2024-04-20",
            "description": "ASB HOME LOAN INT",
            "amount": -850.00,
            "balance": 5000.00,
            "transaction_type": "debit",
            "categorization": {
                "suggested_category": "interest_debit",
                "confidence": 0.98,
                "pl_row": 3,
                "is_deductible": True
            }
        },
        {
            "date": "2024-04-25",
            "description": "NEW TENANT DEPOSIT",
            "amount": 2100.00,
            "balance": 7100.00,
            "transaction_type": "credit",
            "categorization": {
                "suggested_category": "rental_income",
                "confidence": 0.6,
                "pl_row": 1,
                "is_deductible": False
            },
            "special_handling": {
                "is_combined_bond_rent": True,
                "suggested_bond_amount": 1400.00,
                "suggested_rent_amount": 700.00
            },
            "review_flags": {
                "needs_review": True,
                "reasons": ["bond_rent_split_needed"],
                "severity": "warning"
            }
        },
        {
            "date": "2024-04-30",
            "description": "ASB LOAN REPAYMENT",
            "amount": -850.00,
            "balance": 6250.00,
            "transaction_type": "debit",
            "categorization": {
                "suggested_category": "interest_debit",
                "confidence": 0.5,
                "pl_row": 3,
                "is_deductible": True
            },
            "special_handling": {
                "is_loan_repayment_total": True,
                "linked_loan_account": "12-3456-7890124-00"
            },
            "review_flags": {
                "needs_review": True,
                "reasons": ["interest_principal_split_needed"],
                "severity": "warning"
            }
        }
    ],
    "interest_analysis": {
        "total_interest_debits": 850.00,
        "total_interest_credits": 0,
        "interest_frequency": "monthly",
        "interest_transaction_count": 1,
        "offset_account_detected": False
    },
    "summary": {
        "total_transactions": 6,
        "total_credits": 3400.00,
        "total_debits": 2150.00,
        "net_movement": 1250.00,
        "balance_validated": True,
        "flagged_count": 2
    },
    "extraction_metadata": {
        "pages_processed": 2,
        "batch_number": 1,
        "total_batches": 1,
        "is_complete": True,
        "data_quality_score": 0.95
    }
}

SAMPLE_SETTLEMENT_EXTRACTION = {
    "settlement_info": {
        "settlement_date": "2024-05-15",
        "property_address": "123 Main Street, Auckland 1010",
        "contract_date": "2024-04-01",
        "vendor_name": "Jane Vendor",
        "purchaser_name": "John Smith",
        "solicitor_firm": "Smith & Co Lawyers"
    },
    "financial_details": {
        "purchase_price": 850000.00,
        "deposit_amount": 85000.00,
        "balance_due_on_settlement": 765000.00
    },
    "apportionments": {
        "rates": {
            "settlement_apportionment": 1234.56,
            "vendor_instalment": 500.00,
            "vendor_credit": 200.00,
            "calculation_method": "Daily apportionment from settlement to end of rating year"
        },
        "water_rates": {
            "adjustment_amount": 150.00,
            "direction": "vendor_credit"
        }
    },
    "fees_and_costs": {
        "legal_fees": {
            "professional_fee": 1800.00,
            "disbursements": 450.00,
            "gst": 337.50,
            "total": 2587.50
        }
    },
    "interest_on_deposit": {
        "amount": 125.50,
        "days": 44
    },
    "bank_contribution": {
        "mentioned_in_settlement": True,
        "amount": 765000.00,
        "lender_name": "ASB Bank",
        "verification_required": True
    },
    "year1_tax_calculations": {
        "rates_deductible": {
            "settlement_apportionment": 1234.56,
            "vendor_instalment": 500.00,
            "vendor_credit": 200.00,
            "total_from_settlement": 1534.56,
            "calculation_formula": "1234.56 + 500.00 - 200.00 = 1534.56"
        },
        "legal_fees_deductible": {
            "total_legal_fees": 2587.50,
            "is_under_10k_threshold": True,
            "deductible_amount": 2587.50
        },
        "interest_on_deposit_treatment": {
            "amount": 125.50,
            "treatment": "Nets against Year 1 interest expense"
        }
    },
    "all_line_items": [
        {"line_number": 1, "description": "Purchase Price", "amount": 850000.00, "direction": "debit"},
        {"line_number": 2, "description": "Less Deposit", "amount": -85000.00, "direction": "credit"},
        {"line_number": 3, "description": "Balance Due", "amount": 765000.00, "direction": "debit"},
        {"line_number": 4, "description": "Rates Apportionment", "amount": 1234.56, "direction": "debit", "is_deductible": True},
        {"line_number": 5, "description": "Water Rates Adjustment", "amount": -150.00, "direction": "credit"},
        {"line_number": 6, "description": "Legal Fees", "amount": 2587.50, "direction": "debit", "is_deductible": True},
        {"line_number": 7, "description": "Interest on Deposit", "amount": -125.50, "direction": "credit"}
    ],
    "extraction_metadata": {
        "pages_processed": 3,
        "is_complete": True,
        "data_quality_score": 0.98
    }
}
```

---

## Backwards Compatibility

### 1. Handling Existing Documents

```python
# document_processor.py - Backwards compatibility

class DocumentProcessor:
    """Process documents with backwards compatibility for existing data."""

    async def get_extracted_data(self, document) -> dict:
        """Get extracted data with backwards compatibility."""

        if not document.extracted_data:
            return {}

        data = document.extracted_data

        # Check schema version
        schema_version = data.get("_schema_version", 1)

        if schema_version < 2:
            # Migrate v1 data to v2 format
            return self._migrate_v1_to_v2(data, document.document_type)

        return data

    def _migrate_v1_to_v2(self, old_data: dict, document_type: str) -> dict:
        """Migrate v1 schema to v2 format."""

        if document_type == "bank_statement":
            return self._migrate_bank_statement_v1_to_v2(old_data)
        elif document_type == "settlement_statement":
            return self._migrate_settlement_v1_to_v2(old_data)
        else:
            # For other types, wrap in new structure
            return {
                "_schema_version": 2,
                "_migrated_from": 1,
                "legacy_data": old_data,
                "extraction_metadata": {
                    "pages_processed": old_data.get("page_count"),
                    "is_complete": True,
                    "data_quality_score": None  # Unknown for old data
                }
            }

    def _migrate_bank_statement_v1_to_v2(self, old_data: dict) -> dict:
        """Migrate bank statement from v1 to v2."""

        # Old format: transactions were flat list with basic fields
        old_transactions = old_data.get("transactions", [])

        new_transactions = []
        for txn in old_transactions:
            new_txn = {
                "date": txn.get("date"),
                "description": txn.get("description"),
                "amount": txn.get("amount"),
                "balance": txn.get("balance"),
                "transaction_type": "credit" if txn.get("amount", 0) > 0 else "debit",
                "categorization": {
                    "suggested_category": txn.get("category", "unknown"),
                    "confidence": txn.get("confidence", 0.5),
                    "pl_row": txn.get("pl_row"),
                    "is_deductible": txn.get("is_deductible", False)
                },
                "special_handling": {},  # Not available in v1
                "review_flags": {
                    "needs_review": txn.get("needs_review", False),
                    "reasons": txn.get("review_reasons", [])
                }
            }
            new_transactions.append(new_txn)

        return {
            "_schema_version": 2,
            "_migrated_from": 1,
            "account_info": {
                "bank_name": old_data.get("bank_name", "Unknown"),
                "account_number": old_data.get("account_number", ""),
                "account_type": "transaction"
            },
            "statement_period": {
                "start_date": old_data.get("period_start"),
                "end_date": old_data.get("period_end"),
                "opening_balance": old_data.get("opening_balance"),
                "closing_balance": old_data.get("closing_balance")
            },
            "transactions": new_transactions,
            "interest_analysis": {
                "total_interest_debits": sum(
                    abs(t["amount"]) for t in new_transactions
                    if t["categorization"]["suggested_category"] == "interest_debit"
                ),
                "total_interest_credits": 0,  # Not tracked in v1
                "interest_frequency": "unknown"
            },
            "summary": {
                "total_transactions": len(new_transactions),
                "total_credits": sum(t["amount"] for t in new_transactions if t["amount"] > 0),
                "total_debits": sum(abs(t["amount"]) for t in new_transactions if t["amount"] < 0),
                "balance_validated": False  # Can't verify for migrated data
            },
            "extraction_metadata": {
                "pages_processed": old_data.get("page_count"),
                "is_complete": True,
                "data_quality_score": None
            }
        }
```

### 2. Feature Flags for Gradual Rollout

```python
# config.py - Feature flags

class Phase1Settings(BaseSettings):
    # ... existing settings ...

    # Feature Flags for Gradual Rollout
    ENABLE_TOOL_USE: bool = Field(
        default=True,
        description="Enable Tool Use for schema-enforced extraction (fallback to JSON if False)"
    )
    ENABLE_BATCH_PROCESSING: bool = Field(
        default=True,
        description="Enable batch processing for financial documents"
    )
    ENABLE_MULTIPASS_EXTRACTION: bool = Field(
        default=True,
        description="Enable multi-pass extraction for complex documents"
    )
    ENABLE_EXTRACTION_VERIFICATION: bool = Field(
        default=True,
        description="Enable verification pass"
    )
    ENABLE_NEW_SCHEMA_V2: bool = Field(
        default=True,
        description="Use new v2 schema format (v1 for backwards compatibility)"
    )

    # Rollout percentage (for A/B testing)
    NEW_EXTRACTION_ROLLOUT_PERCENTAGE: int = Field(
        default=100,
        description="Percentage of documents to process with new extraction (0-100)"
    )
```

### 3. Migration Script for Existing Tax Returns

```python
# scripts/migrate_existing_documents.py

"""
Migration script to re-process existing documents with new Phase 1 extraction.

Usage:
    poetry run python scripts/migrate_existing_documents.py --dry-run
    poetry run python scripts/migrate_existing_documents.py --tax-return-id <uuid>
    poetry run python scripts/migrate_existing_documents.py --all
"""

import asyncio
import argparse
from uuid import UUID

from app.database import get_db
from app.models.db_models import Document, TaxReturn
from app.services.phase1_document_intake.document_processor import DocumentProcessor


async def migrate_document(db, document: Document, processor: DocumentProcessor, dry_run: bool):
    """Migrate a single document to new schema."""

    print(f"  Processing: {document.original_filename} ({document.document_type})")

    if dry_run:
        print(f"    [DRY RUN] Would re-extract with new schema")
        return

    # Store old data for comparison
    old_data = document.extracted_data

    try:
        # Re-process with new extraction
        new_data = await processor.reprocess_document(db, document)

        # Add migration metadata
        new_data["_migration"] = {
            "migrated_at": datetime.now().isoformat(),
            "old_transaction_count": len(old_data.get("transactions", [])) if old_data else 0,
            "new_transaction_count": len(new_data.get("transactions", []))
        }

        document.extracted_data = new_data
        document.verification_status = "migrated"
        await db.commit()

        print(f"    Migrated: {new_data['_migration']['old_transaction_count']} -> {new_data['_migration']['new_transaction_count']} transactions")

    except Exception as e:
        print(f"    ERROR: {e}")
        await db.rollback()


async def migrate_tax_return(db, tax_return_id: UUID, processor: DocumentProcessor, dry_run: bool):
    """Migrate all documents for a tax return."""

    tax_return = await db.get(TaxReturn, tax_return_id)
    if not tax_return:
        print(f"Tax return {tax_return_id} not found")
        return

    print(f"\nMigrating tax return: {tax_return_id}")
    print(f"  Property: {tax_return.property_address}")
    print(f"  Documents: {len(tax_return.documents)}")

    for document in tax_return.documents:
        await migrate_document(db, document, processor, dry_run)


async def main():
    parser = argparse.ArgumentParser(description="Migrate documents to new Phase 1 schema")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be done without making changes")
    parser.add_argument("--tax-return-id", type=str, help="Migrate specific tax return")
    parser.add_argument("--all", action="store_true", help="Migrate all tax returns")

    args = parser.parse_args()

    processor = DocumentProcessor()

    async for db in get_db():
        if args.tax_return_id:
            await migrate_tax_return(db, UUID(args.tax_return_id), processor, args.dry_run)
        elif args.all:
            tax_returns = await db.execute(select(TaxReturn))
            for tr in tax_returns.scalars():
                await migrate_tax_return(db, tr.id, processor, args.dry_run)
        else:
            parser.print_help()


if __name__ == "__main__":
    asyncio.run(main())
```

---

## Updated Implementation Priority

| Priority | Change | Impact | Dependencies |
|----------|--------|--------|--------------|
| **P0** | Remove 5-page limit + batch processing | Stops data loss | None |
| **P0** | Enhanced retry/rate limiting | Enables 50+ docs | None |
| **P0** | Database migration | Stores new fields | None |
| **P1** | Tool Use for classification | Consistent output | None |
| **P1** | Tool Use for bank statements | Schema enforcement | P0 |
| **P1** | SSE batch progress events | UI feedback | P0 |
| **P2** | Tool Use for all doc types | Full coverage | P1 |
| **P2** | Multi-pass extraction | Better accuracy | P1 |
| **P2** | UI updates for new data | User visibility | P1 |
| **P3** | Verification pass | Error catching | P2 |
| **P3** | Phase 2 integration updates | Use richer data | P2 |
| **P3** | Backwards compatibility | Migration | P2 |
| **P4** | Testing suite | Quality assurance | P3 |

---

## Short-Term Rental / Airbnb / Hotel-Managed Properties

### Property Management Type Classification

The system must detect and handle different property management types:

```python
class PropertyManagementType(str, Enum):
    """Property management type affects GST and income handling."""
    LONG_TERM = "long_term"           # Traditional PM (Ray White, Barfoot, etc.)
    SHORT_TERM = "short_term"         # Airbnb, Bookabach, hotel-managed
    SELF_MANAGED = "self_managed"     # Owner manages directly
```

### Short-Term Rental Detection

**Indicators in documents:**
- Hotel/resort management company names (Distinction, Quest, etc.)
- "Accommodation", "Nightly rate", "Guest" terminology
- GST workings showing "No GST" income lines
- Travel agent commission references
- Daily service/cleaning charges

### GST Handling for Short-Term Rentals

```python
SHORT_TERM_GST_RULES = {
    "travel_agent_commission": {
        "has_gst": False,
        "note": "Travel agent commission has NO GST - do not divide by 1.15"
    },
    "direct_bookings": {
        "has_gst": True,
        "note": "Direct bookings typically GST-inclusive"
    },
    "ffe_contributions": {
        "has_gst": False,
        "is_capital": True,
        "note": "FFE contributions are CAPITAL - exclude entirely from expenses"
    },
    "daily_service_fee": {
        "has_gst": True,
        "category": "daily_service",
        "pl_row": 35
    }
}
```

### Schema Addition: Short-Term Rental Categories

Add to Bank Statement and PM Statement extraction schemas:

```python
"suggested_category": {
    "type": "string",
    "enum": [
        # ... existing categories ...

        # Short-term rental specific
        "short_term_rental_income",
        "travel_agent_commission",
        "daily_service_fee",
        "ffe_contribution",
        "guest_supplies",
        "platform_fees",  # Airbnb, Bookabach fees

        # ... rest of categories
    ]
}
```

### Short-Term Detection in Classification

```python
# Add to DOCUMENT_CLASSIFICATION_TOOL
"property_management_detection": {
    "type": "object",
    "properties": {
        "detected_type": {
            "type": "string",
            "enum": ["long_term", "short_term", "self_managed", "unknown"]
        },
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "indicators": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Evidence found (e.g., 'Hotel management company name', 'Travel agent references')"
        },
        "gst_workings_reference": {
            "type": "boolean",
            "description": "True if GST workings document is referenced/needed"
        }
    }
}
```

### Extraction Prompt Addition

```python
SHORT_TERM_RENTAL_PROMPT_ADDITION = """
SHORT-TERM RENTAL DETECTION:
Look for indicators that this is a short-term/holiday rental:
- Hotel or resort management company
- References to "guests", "bookings", "nightly rates"
- Travel agent commission
- Airbnb, Bookabach, or similar platform references
- Daily service or cleaning charges

IF SHORT-TERM RENTAL DETECTED:
1. Flag property_management_type = "short_term"
2. For travel agent commission:
   - Category: travel_agent_commission
   - has_gst = false (DO NOT divide by 1.15)
3. For FFE contributions:
   - Category: ffe_contribution
   - exclude_from_pl = true (CAPITAL expense)
4. Note that GST workings may be required as authoritative income source
"""
```

---

## Resident Society Handling

### Separate from Body Corporate

**Critical:** Resident Society (RSI) is a **separate category** from Body Corporate and maps to a **different P&L row**.

| Type | P&L Row | Common Names |
|------|---------|--------------|
| Body Corporate | Row 15 | "Body Corporate", "BC Levy", "Unit Title" |
| Resident Society | Row 36 | "RSI", "Resident Society", "Laneway Levy", "Community Levy" |

### Schema Addition: Resident Society Category

Add to all extraction schemas:

```python
"suggested_category": {
    "type": "string",
    "enum": [
        # ... existing categories ...

        "body_corporate_operating",   # P&L Row 15
        "body_corporate_reserve",     # Exclude (capital)
        "resident_society",           # P&L Row 36 - SEPARATE!

        # ... rest of categories
    ]
}
```

### Resident Society Extraction Tool

```python
RESIDENT_SOCIETY_EXTRACTION_TOOL = {
    "name": "extract_resident_society",
    "description": "Extract resident society levy - SEPARATE from body corporate",
    "input_schema": {
        "type": "object",
        "required": ["society_name", "property_address", "total_levy"],
        "properties": {
            "society_name": {
                "type": "string",
                "description": "Name of resident society (e.g., 'Laneway Owners Society')"
            },
            "property_address": {"type": "string"},
            "unit_number": {"type": ["string", "null"]},
            "levy_period": {"type": "string"},
            "period_start": {"type": "string", "format": "date"},
            "period_end": {"type": "string", "format": "date"},
            "total_levy": {"type": "number"},
            "payment_frequency": {
                "type": "string",
                "enum": ["monthly", "quarterly", "annual"]
            },
            "is_fully_deductible": {
                "type": "boolean",
                "default": True,
                "description": "Resident society levies are typically fully deductible (unlike BC reserve funds)"
            },
            "pl_row": {
                "type": "integer",
                "const": 36,
                "description": "Always maps to P&L Row 36"
            }
        }
    }
}
```

### Settlement Statement: Resident Society Pro-Rata

For Year 1, update settlement schema to include:

```python
"apportionments": {
    "type": "object",
    "properties": {
        # ... existing (rates, water, body_corporate, insurance) ...

        "resident_society": {
            "type": "object",
            "description": "Resident society levy apportionment (SEPARATE from BC)",
            "properties": {
                "levy_amount": {"type": ["number", "null"]},
                "period_from": {"type": ["string", "null"], "format": "date"},
                "period_to": {"type": ["string", "null"], "format": "date"},
                "adjustment_amount": {"type": ["number", "null"]},
                "direction": {
                    "type": "string",
                    "enum": ["vendor_credit", "purchaser_credit"]
                }
            }
        }
    }
}
```

### Category Detection Rules

```python
RESIDENT_SOCIETY_DETECTION = {
    "keywords": [
        "resident society",
        "residents society",
        "rsi",
        "laneway",
        "laneway levy",
        "community levy",
        "community association",
        "owners society"
    ],
    "exclude_keywords": [
        "body corporate",
        "body corp",
        "bc levy",
        "unit title"
    ],
    "pl_row": 36,
    "note": "Must be separate from Body Corporate (Row 15)"
}
```

---

## P&L Row Mapping Table

### Complete Category to P&L Row Mapping

This is the authoritative mapping used in the Lighthouse Financial template:

```python
PL_ROW_MAPPING = {
    # === INCOME (Rows 1-10) ===
    "rental_income": {"row": 6, "type": "income", "description": "Gross rental income"},
    "water_rates_recovered": {"row": 7, "type": "income", "description": "Water rates recovered from tenant"},
    "bank_contribution": {"row": 8, "type": "income", "description": "Bank cashback/contribution - TAXABLE"},
    "insurance_payout": {"row": 9, "type": "income", "description": "Insurance claim payouts"},
    "other_income": {"row": 10, "type": "income", "description": "Other rental-related income"},

    # === EXPENSES (Rows 11-50) ===
    "advertising": {"row": 12, "type": "expense", "description": "Tenant-finding ads ONLY (separate from agent fees)"},
    "agent_fees": {"row": 13, "type": "expense", "description": "PM fees + letting fees + GST"},
    "bank_fees": {"row": 14, "type": "expense", "description": "Account fees, restructure fees"},
    "body_corporate_operating": {"row": 15, "type": "expense", "description": "Operating fund ONLY (not reserve)"},
    "accounting_fees": {"row": 16, "type": "expense", "description": "Always include - standard $862.50"},
    "depreciation": {"row": 17, "type": "expense", "description": "Pro-rate for partial year"},
    "due_diligence": {"row": 18, "type": "expense", "description": "LIM, meth test, healthy homes, valuations"},
    "landlord_insurance": {"row": 24, "type": "expense", "description": "Landlord/rental insurance ONLY"},
    "interest_expense": {"row": 25, "type": "expense", "description": "After deductibility % applied"},
    "legal_fees": {"row": 27, "type": "expense", "description": "Deductible if <$10k (Year 1)"},
    "council_rates": {"row": 34, "type": "expense", "description": "Year 1: Settlement + Instalments - Vendor credit"},
    "repairs_maintenance": {"row": 35, "type": "expense", "description": "Repairs <$1000 or existing item repair"},
    "resident_society": {"row": 36, "type": "expense", "description": "SEPARATE from Body Corporate"},
    "water_rates": {"row": 41, "type": "expense", "description": "GST-inclusive for non-registered"},

    # === SHORT-TERM RENTAL SPECIFIC ===
    "daily_service_fee": {"row": 35, "type": "expense", "description": "Daily cleaning/service (short-term)"},
    "platform_fees": {"row": 13, "type": "expense", "description": "Airbnb/Bookabach fees"},
    "travel_agent_commission": {"row": 13, "type": "expense", "description": "NO GST - don't divide by 1.15"},

    # === EXCLUDED (No P&L Row) ===
    "body_corporate_reserve": {"row": None, "type": "excluded", "reason": "Capital contribution"},
    "principal_repayment": {"row": None, "type": "excluded", "reason": "Capital repayment"},
    "transfer_between_accounts": {"row": None, "type": "excluded", "reason": "Internal transfer"},
    "personal_expense": {"row": None, "type": "excluded", "reason": "Not rental-related"},
    "bond_received": {"row": None, "type": "excluded", "reason": "Not income - held in trust"},
    "bond_released": {"row": None, "type": "excluded", "reason": "Not expense - return of trust"},
    "ffe_contribution": {"row": None, "type": "excluded", "reason": "Capital contribution"},
    "capital_improvement": {"row": None, "type": "excluded", "reason": "Capital expense - depreciate instead"},
    "drawing": {"row": None, "type": "excluded", "reason": "Owner withdrawal"},
    "funds_introduced": {"row": None, "type": "excluded", "reason": "Owner contribution"},
    "loan_drawdown": {"row": None, "type": "excluded", "reason": "Capital movement"},
    "interest_credit": {"row": None, "type": "excluded", "reason": "DO NOT subtract from interest debits"},
}
```

### Validation Rules for P&L Rows

```python
PL_ROW_VALIDATION = {
    # Rows that must ALWAYS have a value
    "required_rows": {
        16: "accounting_fees",  # Always $862.50 or invoiced amount
    },

    # Rows that are mutually exclusive with others
    "exclusive_checks": {
        12: "Must be SEPARATE from Row 13 (agent_fees)",
        15: "Must be SEPARATE from Row 36 (resident_society)",
    },

    # Rows with Year 1 special handling
    "year1_special": {
        17: "Pro-rate depreciation for partial year",
        25: "Net interest on deposit against this",
        27: "Only deductible if <$10k",
        34: "Must include settlement apportionment",
    }
}
```

---

## Accounting Fees Standard Rule

### Rule: Always Include Row 16

**Critical:** Accounting fees (P&L Row 16) must **ALWAYS be included** in the return.

```python
ACCOUNTING_FEES_RULE = {
    "pl_row": 16,
    "standard_amount": 862.50,
    "description": "Consulting & Accounting Fees",
    "rule": "Always include - use standard amount if no specific invoice",
    "gst_treatment": "GST-inclusive for non-registered landlords"
}
```

### Implementation

```python
def ensure_accounting_fees_included(transactions: List[dict], documents: List[Document]) -> dict:
    """Ensure accounting fees are included in the return."""

    # Check if any transaction is categorized as accounting_fees
    has_accounting_fees = any(
        t.get("category") == "accounting_fees"
        for t in transactions
    )

    # Check documents for accounting/consulting invoices
    accounting_invoice = next(
        (d for d in documents
         if d.document_type == "maintenance_invoice"
         and "accounting" in d.extracted_data.get("description", "").lower()),
        None
    )

    if not has_accounting_fees:
        # Add standard accounting fees entry
        return {
            "action": "add_accounting_fees",
            "amount": 862.50 if not accounting_invoice else accounting_invoice.extracted_data.get("total_amount"),
            "pl_row": 16,
            "source": "standard" if not accounting_invoice else "invoice",
            "note": "Accounting fees always required - using standard amount" if not accounting_invoice else None
        }

    return {"action": "none", "note": "Accounting fees already present"}
```

### Phase 2 Integration

The workbook generator should check for accounting fees:

```python
def _ensure_required_rows(self, pl_data: dict) -> dict:
    """Ensure all required P&L rows have values."""

    # Row 16: Accounting fees - ALWAYS required
    if not pl_data.get("row_16") or pl_data["row_16"] == 0:
        pl_data["row_16"] = {
            "amount": 862.50,
            "category": "accounting_fees",
            "source": "standard_amount",
            "note": "Standard accounting fee applied"
        }

    return pl_data
```

---

## Progress Tracker Updates

### New Stage Definitions

Update `progress_tracker.py` with new stages for batch processing:

```python
class ProgressTracker:
    """Updated progress tracker with batch processing stages."""

    STAGES = {
        # Phase 1: Document Intake
        "initializing": (0, 2),
        "saving_files": (2, 5),
        "classifying": (5, 15),

        # NEW: Batch extraction stages
        "extracting": (15, 60),           # Overall extraction
        "extracting_batch": (15, 60),     # Individual batch progress
        "merging_batches": (60, 65),      # Combining batch results
        "verification": (65, 70),          # Verification pass

        # Phase 1 completion
        "completeness_check": (70, 75),

        # Phase 2: Transaction Processing (if combined flow)
        "loading_transactions": (75, 78),
        "applying_feedback": (78, 80),
        "querying_rag": (80, 85),
        "categorizing": (85, 92),
        "applying_tax_rules": (92, 95),
        "generating_summaries": (95, 98),

        # Completion
        "finalizing": (98, 100),
        "complete": (100, 100),
        "error": (0, 0),
    }

    async def emit_batch_progress(
        self,
        document_name: str,
        batch_num: int,
        total_batches: int,
        pages: str
    ):
        """Emit batch-specific progress."""
        sub_progress = batch_num / total_batches

        await self.emit(
            stage="extracting_batch",
            message=f"Extracting {document_name}",
            detail=f"Batch {batch_num}/{total_batches} (pages {pages})",
            sub_progress=sub_progress
        )

    async def emit_verification_progress(
        self,
        document_name: str,
        checks_passed: int,
        total_checks: int,
        warnings: int
    ):
        """Emit verification progress."""
        await self.emit(
            stage="verification",
            message=f"Verifying {document_name}",
            detail=f"{checks_passed}/{total_checks} checks passed, {warnings} warnings",
            sub_progress=1.0
        )
```

### SSE Event Types for UI

```python
# New SSE event types for batch processing
SSE_EVENT_TYPES = {
    "extracting_batch": {
        "fields": ["document_name", "batch", "total_batches", "pages"],
        "example": {
            "stage": "extracting_batch",
            "progress": 35,
            "message": "Extracting bank_statement.pdf",
            "detail": "Batch 2/4 (pages 6-10)",
            "document_name": "bank_statement.pdf",
            "batch": 2,
            "total_batches": 4,
            "pages": "6-10"
        }
    },
    "verification": {
        "fields": ["document_name", "checks_passed", "total_checks", "warnings"],
        "example": {
            "stage": "verification",
            "progress": 68,
            "message": "Verifying settlement.pdf",
            "detail": "5/6 checks passed, 1 warning",
            "document_name": "settlement.pdf",
            "checks_passed": 5,
            "total_checks": 6,
            "warnings": 1
        }
    },
    "merging_batches": {
        "fields": ["document_name", "transaction_count"],
        "example": {
            "stage": "merging_batches",
            "progress": 62,
            "message": "Merging batch results",
            "detail": "Consolidated 245 transactions",
            "document_name": "bank_statement.pdf",
            "transaction_count": 245
        }
    }
}
```

---

## CSV/Excel Direct Reading

### Optimization: Skip Vision API for Structured Files

CSV and Excel files don't need vision processing - direct parsing is faster and more accurate.

```python
class FileHandler:
    """Enhanced file handler with optimized CSV/Excel processing."""

    async def process_file(self, file_path: str, filename: str) -> ProcessedFile:
        """Process file with format-specific optimization."""

        mime_type = self._detect_mime_type(file_path)

        if mime_type in ["text/csv", "application/csv"]:
            return await self._process_csv_direct(file_path, filename)

        elif mime_type in [
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "application/vnd.ms-excel"
        ]:
            return await self._process_excel_direct(file_path, filename)

        elif mime_type == "application/pdf":
            return await self._process_pdf(file_path, filename)

        else:
            return await self._process_image(file_path, filename)

    async def _process_csv_direct(self, file_path: str, filename: str) -> ProcessedFile:
        """
        Process CSV directly without vision API.
        Returns structured transaction data ready for categorization.
        """
        import pandas as pd

        # Try different encodings
        for encoding in ['utf-8', 'latin-1', 'cp1252']:
            try:
                df = pd.read_csv(file_path, encoding=encoding)
                break
            except UnicodeDecodeError:
                continue

        # Detect bank statement format by column names
        format_detected = self._detect_csv_format(df)

        # Parse based on detected format
        transactions = self._parse_csv_transactions(df, format_detected)

        return ProcessedFile(
            text_content=df.to_string(),
            image_paths=[],  # No images needed
            page_count=1,
            is_structured_data=True,
            structured_transactions=transactions,
            format_detected=format_detected
        )

    def _detect_csv_format(self, df: pd.DataFrame) -> str:
        """Detect which bank's CSV format this is."""
        columns_lower = [c.lower() for c in df.columns]

        # ASB Bank format
        if "particulars" in columns_lower and "code" in columns_lower:
            return "asb"

        # ANZ format
        if "type" in columns_lower and "details" in columns_lower:
            return "anz"

        # Westpac format
        if "other party" in columns_lower:
            return "westpac"

        # BNZ format
        if "payee" in columns_lower:
            return "bnz"

        # Kiwibank format
        if "memo" in columns_lower:
            return "kiwibank"

        return "generic"

    def _parse_csv_transactions(self, df: pd.DataFrame, format: str) -> List[dict]:
        """Parse transactions based on bank format."""

        # Column mapping per bank
        COLUMN_MAPS = {
            "asb": {
                "date": "Date",
                "description": ["Particulars", "Code", "Reference"],
                "amount": "Amount",
                "balance": "Balance"
            },
            "anz": {
                "date": "Date",
                "description": ["Type", "Details", "Particulars", "Code", "Reference"],
                "amount": "Amount",
                "balance": "Balance"
            },
            # ... other banks
        }

        column_map = COLUMN_MAPS.get(format, COLUMN_MAPS["generic"])

        transactions = []
        for _, row in df.iterrows():
            txn = {
                "date": self._parse_date(row.get(column_map["date"])),
                "description": self._build_description(row, column_map["description"]),
                "amount": self._parse_amount(row.get(column_map["amount"])),
                "balance": self._parse_amount(row.get(column_map.get("balance"))),
                "source": "csv_direct"
            }
            transactions.append(txn)

        return transactions
```

### Schema Flag for Pre-Parsed Data

```python
# When sending to Claude for categorization only (no extraction needed)
CATEGORIZATION_ONLY_PROMPT = """
The following transactions have already been extracted from a CSV/Excel file.
Your task is ONLY to categorize them - do not re-extract.

Transactions:
{transactions_json}

For each transaction, provide:
1. suggested_category (from the enum)
2. confidence (0.0-1.0)
3. pl_row (integer or null if excluded)
4. special_handling flags if applicable
"""
```

---

## API Routes Documentation

### Updated Endpoints

#### `/api/returns/upload-stream` (POST)

**Request:** `multipart/form-data` with same fields as current

**SSE Response Stream:**

```
data: {"stage":"initializing","progress":0,"message":"Starting..."}

data: {"stage":"saving_files","progress":3,"message":"Saving 5 files..."}

data: {"stage":"classifying","progress":10,"message":"Classifying documents...","detail":"bank_statement.pdf"}

data: {"stage":"extracting_batch","progress":25,"message":"Extracting bank_statement.pdf","batch":1,"total_batches":4,"pages":"1-5"}

data: {"stage":"extracting_batch","progress":35,"message":"Extracting bank_statement.pdf","batch":2,"total_batches":4,"pages":"6-10"}

data: {"stage":"verification","progress":68,"message":"Verifying extraction","checks_passed":5,"warnings":1}

data: {"stage":"complete","progress":100,"message":"Processing complete","detail":"<tax_return_id>"}
```

#### New Endpoint: `/api/returns/{id}/reprocess` (POST)

Reprocess documents with new extraction (for migration):

```python
@api_router.post("/returns/{tax_return_id}/reprocess")
async def reprocess_tax_return(
    tax_return_id: UUID,
    reprocess_options: ReprocessOptions,
    db: AsyncSession = Depends(get_db)
):
    """
    Reprocess a tax return with updated extraction.

    Options:
    - documents: List of document IDs to reprocess (or "all")
    - use_new_schema: Whether to use v2 schema
    - preserve_manual_edits: Keep manually reviewed categorizations
    """
    pass
```

#### New Endpoint: `/api/documents/{id}/extraction-details` (GET)

Get detailed extraction metadata:

```python
@api_router.get("/documents/{document_id}/extraction-details")
async def get_extraction_details(
    document_id: UUID,
    db: AsyncSession = Depends(get_db)
) -> ExtractionDetailsResponse:
    """
    Get detailed extraction information for a document.

    Returns:
    - pages_processed
    - batches_used
    - verification_status
    - extraction_warnings
    - data_quality_score
    - api_calls_used
    - processing_time_ms
    """
    pass
```

### Response Schema Updates

```python
# schemas/documents.py additions

class ExtractionMetadata(BaseModel):
    """Extraction metadata included in document responses."""
    pages_processed: int
    batches_used: int
    verification_status: str  # "passed", "warnings", "failed"
    data_quality_score: float
    extraction_warnings: List[str]
    processing_time_ms: int
    api_calls_used: int

class DocumentResponseV2(DocumentResponse):
    """Enhanced document response with extraction metadata."""
    extraction_metadata: Optional[ExtractionMetadata]
    interest_analysis: Optional[dict]  # For bank statements
    year1_calculations: Optional[dict]  # For settlement statements
```

---

## Data Parsing Rules

### Date Format Standardization

NZ banks use various date formats. Standardize to ISO format (YYYY-MM-DD):

```python
DATE_FORMATS = [
    # NZ formats (most common first)
    "%d/%m/%Y",       # 31/03/2024
    "%d-%m-%Y",       # 31-03-2024
    "%d %b %Y",       # 31 Mar 2024
    "%d %B %Y",       # 31 March 2024
    "%d-%b-%Y",       # 31-Mar-2024
    "%d-%b-%y",       # 31-Mar-24

    # ISO formats
    "%Y-%m-%d",       # 2024-03-31

    # US formats (less common but possible)
    "%m/%d/%Y",       # 03/31/2024
]

def parse_date(date_string: str) -> str:
    """
    Parse date string to ISO format (YYYY-MM-DD).
    Returns None if unparseable.
    """
    if not date_string:
        return None

    date_string = date_string.strip()

    for fmt in DATE_FORMATS:
        try:
            dt = datetime.strptime(date_string, fmt)
            return dt.strftime("%Y-%m-%d")
        except ValueError:
            continue

    # Log unparseable date
    logger.warning(f"Could not parse date: {date_string}")
    return None
```

### Currency Parsing Rules

```python
CURRENCY_PATTERNS = [
    # Standard formats
    (r'^\$?([\d,]+\.?\d*)$', 1),                    # $1,234.56 or 1234.56
    (r'^\$?([\d,]+\.?\d*)\s*CR$', 1),              # $1,234.56 CR (credit)
    (r'^\$?([\d,]+\.?\d*)\s*DR$', -1),             # $1,234.56 DR (debit)
    (r'^-\$?([\d,]+\.?\d*)$', -1),                  # -$1,234.56
    (r'^\(\$?([\d,]+\.?\d*)\)$', -1),              # ($1,234.56) - negative in parens
    (r'^\$?([\d,]+\.?\d*)-$', -1),                  # $1,234.56- (trailing minus)
]

def parse_amount(amount_string: str, default_sign: int = 1) -> Optional[float]:
    """
    Parse amount string to float.

    Args:
        amount_string: The amount string to parse
        default_sign: 1 for positive (credits), -1 for negative (debits)

    Returns:
        Float amount or None if unparseable
    """
    if not amount_string:
        return None

    amount_string = str(amount_string).strip()

    for pattern, sign_multiplier in CURRENCY_PATTERNS:
        match = re.match(pattern, amount_string, re.IGNORECASE)
        if match:
            # Remove commas and parse
            amount = float(match.group(1).replace(',', ''))
            return amount * sign_multiplier * default_sign

    # Try direct float conversion as fallback
    try:
        return float(amount_string.replace(',', '').replace('$', ''))
    except ValueError:
        logger.warning(f"Could not parse amount: {amount_string}")
        return None
```

### Transaction Type Detection

```python
def detect_transaction_type(amount: float, description: str, balance_change: float = None) -> str:
    """
    Detect if transaction is credit or debit.

    Uses multiple signals:
    1. Sign of amount
    2. Description keywords
    3. Balance change direction (if available)
    """

    # Primary: Amount sign
    if amount > 0:
        likely_type = "credit"
    elif amount < 0:
        likely_type = "debit"
    else:
        likely_type = "unknown"

    # Secondary: Description keywords
    description_lower = description.lower()

    CREDIT_KEYWORDS = ["deposit", "credit", "payment received", "rent", "refund", "interest earned"]
    DEBIT_KEYWORDS = ["withdrawal", "payment", "fee", "charge", "debit", "purchase", "transfer to"]

    if any(kw in description_lower for kw in CREDIT_KEYWORDS):
        keyword_type = "credit"
    elif any(kw in description_lower for kw in DEBIT_KEYWORDS):
        keyword_type = "debit"
    else:
        keyword_type = None

    # Tertiary: Balance change
    if balance_change is not None:
        balance_type = "credit" if balance_change > 0 else "debit"
    else:
        balance_type = None

    # Resolve conflicts (amount sign is most reliable)
    return likely_type
```

---

## Multi-Property Detection

### Detection Logic

```python
class MultiPropertyDetector:
    """Detect if documents belong to multiple properties."""

    def __init__(self, expected_address: str):
        self.expected_address = self._normalize_address(expected_address)
        self.detected_addresses = {}  # address -> list of documents

    def check_document(self, document: Document) -> dict:
        """
        Check if document address matches expected property.

        Returns:
            {
                "matches": bool,
                "detected_address": str or None,
                "similarity_score": float,
                "is_multi_property_risk": bool
            }
        """
        extracted_address = document.extracted_data.get("property_address")

        if not extracted_address:
            return {
                "matches": None,
                "detected_address": None,
                "similarity_score": 0,
                "is_multi_property_risk": False
            }

        normalized = self._normalize_address(extracted_address)
        similarity = self._calculate_similarity(self.expected_address, normalized)

        # Track all addresses seen
        if normalized not in self.detected_addresses:
            self.detected_addresses[normalized] = []
        self.detected_addresses[normalized].append(document.id)

        return {
            "matches": similarity > 0.8,
            "detected_address": extracted_address,
            "similarity_score": similarity,
            "is_multi_property_risk": len(self.detected_addresses) > 1
        }

    def get_multi_property_warning(self) -> Optional[dict]:
        """Get warning if multiple properties detected."""
        if len(self.detected_addresses) <= 1:
            return None

        return {
            "warning_type": "multi_property_detected",
            "severity": "critical",
            "message": f"Documents appear to be for {len(self.detected_addresses)} different properties",
            "addresses_found": list(self.detected_addresses.keys()),
            "documents_per_address": {
                addr: len(docs) for addr, docs in self.detected_addresses.items()
            },
            "action_required": "Verify all documents are for the same property"
        }

    def _normalize_address(self, address: str) -> str:
        """Normalize address for comparison."""
        if not address:
            return ""

        # Lowercase and remove extra whitespace
        normalized = " ".join(address.lower().split())

        # Standardize common variations
        replacements = [
            ("street", "st"),
            ("road", "rd"),
            ("avenue", "ave"),
            ("drive", "dr"),
            ("place", "pl"),
            ("terrace", "tce"),
            ("crescent", "cres"),
            (",", ""),
            (".", ""),
        ]

        for old, new in replacements:
            normalized = normalized.replace(old, new)

        return normalized

    def _calculate_similarity(self, addr1: str, addr2: str) -> float:
        """Calculate similarity between two addresses."""
        from difflib import SequenceMatcher
        return SequenceMatcher(None, addr1, addr2).ratio()
```

### Integration with Document Processor

```python
async def process_tax_return(self, db, tax_return_data, files):
    # ... existing code ...

    # Initialize multi-property detector
    property_detector = MultiPropertyDetector(tax_return_data.property_address)

    # After each document classification
    for document in documents:
        address_check = property_detector.check_document(document)

        if not address_check["matches"] and address_check["detected_address"]:
            # Add flag to document
            document.extracted_data["flags"] = document.extracted_data.get("flags", [])
            document.extracted_data["flags"].append({
                "flag_code": "address_mismatch",
                "severity": "warning",
                "message": f"Document address '{address_check['detected_address']}' differs from property address",
                "similarity_score": address_check["similarity_score"]
            })

    # Check for multi-property at end
    multi_property_warning = property_detector.get_multi_property_warning()
    if multi_property_warning:
        review_result["blocking_issues"] = review_result.get("blocking_issues", [])
        review_result["blocking_issues"].append(multi_property_warning)
```

---

## Additional Minor Gaps

### Image Quality Assessment

```python
IMAGE_QUALITY_ASSESSMENT = {
    "min_resolution": {
        "width": 800,
        "height": 600,
        "description": "Minimum readable resolution"
    },
    "quality_indicators": {
        "blur_threshold": 100,  # Laplacian variance threshold
        "contrast_threshold": 50,
        "brightness_range": (30, 225)
    },
    "flags": {
        "low_resolution": "Image resolution too low for reliable extraction",
        "blurry": "Image appears blurry - extraction may be unreliable",
        "low_contrast": "Low contrast may affect text recognition",
        "too_dark": "Image too dark for reliable extraction",
        "too_bright": "Image overexposed - text may be washed out"
    }
}

async def assess_image_quality(image_path: str) -> dict:
    """Assess image quality for extraction reliability."""
    from PIL import Image
    import numpy as np

    img = Image.open(image_path)
    img_array = np.array(img.convert('L'))  # Grayscale

    # Resolution check
    width, height = img.size
    resolution_ok = width >= 800 and height >= 600

    # Blur detection (Laplacian variance)
    laplacian_var = cv2.Laplacian(img_array, cv2.CV_64F).var()
    is_blurry = laplacian_var < 100

    # Contrast check
    contrast = img_array.std()
    low_contrast = contrast < 50

    # Brightness check
    brightness = img_array.mean()
    too_dark = brightness < 30
    too_bright = brightness > 225

    quality_score = 1.0
    warnings = []

    if not resolution_ok:
        quality_score -= 0.3
        warnings.append("low_resolution")
    if is_blurry:
        quality_score -= 0.3
        warnings.append("blurry")
    if low_contrast:
        quality_score -= 0.2
        warnings.append("low_contrast")
    if too_dark:
        quality_score -= 0.2
        warnings.append("too_dark")
    if too_bright:
        quality_score -= 0.2
        warnings.append("too_bright")

    return {
        "quality_score": max(0, quality_score),
        "warnings": warnings,
        "resolution": {"width": width, "height": height},
        "metrics": {
            "blur_score": laplacian_var,
            "contrast": contrast,
            "brightness": brightness
        },
        "extraction_reliable": quality_score >= 0.6
    }
```

### Document Period Validation

```python
def validate_document_period(
    document_type: str,
    period_start: str,
    period_end: str,
    tax_year: str
) -> dict:
    """
    Validate that document period falls within the tax year.

    NZ Tax Year: 1 April to 31 March
    e.g., FY25 = 1 April 2024 to 31 March 2025
    """

    # Parse tax year
    year_num = int(tax_year.replace("FY", ""))
    fy_start = datetime(2000 + year_num - 1, 4, 1)  # e.g., FY25 -> 1 Apr 2024
    fy_end = datetime(2000 + year_num, 3, 31)        # e.g., FY25 -> 31 Mar 2025

    # Parse document dates
    doc_start = datetime.strptime(period_start, "%Y-%m-%d") if period_start else None
    doc_end = datetime.strptime(period_end, "%Y-%m-%d") if period_end else None

    issues = []

    if doc_start and doc_start < fy_start:
        issues.append({
            "type": "period_before_fy",
            "message": f"Document starts before tax year ({period_start} < {fy_start.strftime('%Y-%m-%d')})"
        })

    if doc_end and doc_end > fy_end:
        issues.append({
            "type": "period_after_fy",
            "message": f"Document ends after tax year ({period_end} > {fy_end.strftime('%Y-%m-%d')})"
        })

    # Check for partial coverage
    if doc_start and doc_end:
        doc_days = (doc_end - doc_start).days
        fy_days = (fy_end - fy_start).days
        coverage_pct = min(100, (doc_days / fy_days) * 100)

        if coverage_pct < 90 and document_type in ["bank_statement", "loan_statement"]:
            issues.append({
                "type": "partial_coverage",
                "message": f"Document covers only {coverage_pct:.0f}% of tax year",
                "severity": "warning"
            })

    return {
        "valid": len(issues) == 0,
        "issues": issues,
        "tax_year_range": {
            "start": fy_start.strftime("%Y-%m-%d"),
            "end": fy_end.strftime("%Y-%m-%d")
        }
    }
```

### Environment Variables

```python
# Complete list of environment variables for Phase 1

PHASE1_ENVIRONMENT_VARIABLES = {
    # Required
    "ANTHROPIC_API_KEY": {
        "required": True,
        "description": "Claude API key"
    },
    "DATABASE_URL": {
        "required": True,
        "description": "PostgreSQL async connection string"
    },

    # Claude API Configuration
    "CLAUDE_MODEL": {
        "required": False,
        "default": "claude-sonnet-4-20250514",
        "description": "Claude model to use"
    },
    "CLAUDE_MAX_RETRIES": {
        "required": False,
        "default": "5",
        "description": "Maximum API retry attempts"
    },
    "CLAUDE_BASE_DELAY": {
        "required": False,
        "default": "2.0",
        "description": "Base delay for exponential backoff (seconds)"
    },
    "CLAUDE_MAX_DELAY": {
        "required": False,
        "default": "60.0",
        "description": "Maximum delay cap (seconds)"
    },
    "CLAUDE_CONCURRENT_REQUESTS": {
        "required": False,
        "default": "3",
        "description": "Maximum concurrent API calls"
    },

    # Batch Processing
    "FINANCIAL_DOC_BATCH_SIZE": {
        "required": False,
        "default": "5",
        "description": "Pages per batch for financial documents"
    },
    "BATCH_DELAY_SECONDS": {
        "required": False,
        "default": "1.0",
        "description": "Delay between batches"
    },

    # Feature Flags
    "ENABLE_TOOL_USE": {
        "required": False,
        "default": "true",
        "description": "Use Claude Tool Use for schema enforcement"
    },
    "ENABLE_BATCH_PROCESSING": {
        "required": False,
        "default": "true",
        "description": "Enable batch processing for financial docs"
    },
    "ENABLE_EXTRACTION_VERIFICATION": {
        "required": False,
        "default": "true",
        "description": "Enable verification pass"
    },

    # File Processing
    "MAX_FILE_SIZE_MB": {
        "required": False,
        "default": "50",
        "description": "Maximum file size in MB"
    },
    "UPLOAD_DIR": {
        "required": False,
        "default": "uploads",
        "description": "Directory for uploaded files"
    },

    # RAG (for Phase 2 integration)
    "PINECONE_API_KEY": {
        "required": False,
        "description": "Pinecone API key for RAG"
    },
    "PINECONE_INDEX_HOST": {
        "required": False,
        "description": "Pinecone index host URL"
    },
    "OPENAI_API_KEY": {
        "required": False,
        "description": "OpenAI API key for embeddings"
    }
}
```

### Logging Strategy

```python
# Logging configuration for Phase 1

LOGGING_CONFIG = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "detailed": {
            "format": "%(asctime)s | %(levelname)-8s | %(name)s:%(lineno)d | %(message)s"
        },
        "extraction": {
            "format": "%(asctime)s | EXTRACTION | %(document_id)s | %(message)s"
        }
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "level": "INFO",
            "formatter": "detailed"
        },
        "extraction_file": {
            "class": "logging.FileHandler",
            "filename": "logs/extraction.log",
            "level": "DEBUG",
            "formatter": "extraction"
        }
    },
    "loggers": {
        "app.services.phase1_document_intake": {
            "level": "DEBUG",
            "handlers": ["console", "extraction_file"]
        },
        "app.services.phase1_document_intake.claude_client": {
            "level": "INFO",  # Reduce noise from API calls
            "handlers": ["console"]
        }
    }
}

# Extraction-specific logging
class ExtractionLogger:
    """Contextual logger for document extraction."""

    def __init__(self, document_id: str, document_name: str):
        self.logger = logging.getLogger("app.services.phase1_document_intake")
        self.extra = {
            "document_id": document_id,
            "document_name": document_name
        }

    def batch_start(self, batch_num: int, total: int, pages: str):
        self.logger.info(
            f"Starting batch {batch_num}/{total} (pages {pages})",
            extra=self.extra
        )

    def batch_complete(self, batch_num: int, transaction_count: int):
        self.logger.info(
            f"Batch {batch_num} complete: {transaction_count} transactions extracted",
            extra=self.extra
        )

    def extraction_warning(self, warning: str):
        self.logger.warning(warning, extra=self.extra)

    def extraction_error(self, error: str, exc_info=None):
        self.logger.error(error, extra=self.extra, exc_info=exc_info)
```

---

## Personal Expense Claims (Home Office, Mileage, Mobile)

### What Are Personal Expense Claims?

Landlords who actively manage their rental properties may claim legitimate expenses for:
- **Home Office**: Portion of home expenses for property management admin
- **Mileage/Travel**: Visits to property for inspections, maintenance oversight
- **Mobile Phone**: Calls to tenants, tradespeople, property managers
- **Internet**: Research, online banking for rental account

### Document Type: Personal Expense Claims

These typically arrive as:
- Client-prepared schedule/spreadsheet
- Accountant's working paper
- Supporting receipts/logs

### Schema Addition

Add to document classification:

```python
"document_type": {
    "type": "string",
    "enum": [
        # ... existing types ...
        "personal_expense_claims",  # NEW: Home office, mileage, mobile claims
        # ... rest of types
    ]
}
```

### Extraction Tool for Personal Expense Claims

```python
PERSONAL_EXPENSE_CLAIMS_EXTRACTION_TOOL = {
    "name": "extract_personal_expense_claims",
    "description": "Extract landlord's personal expense claims for rental property management",
    "input_schema": {
        "type": "object",
        "required": ["claim_type", "total_claimed"],
        "properties": {
            "claim_type": {
                "type": "string",
                "enum": ["home_office", "mileage", "mobile_phone", "internet", "other"],
                "description": "Type of personal expense claim"
            },
            "total_claimed": {
                "type": "number",
                "description": "Total amount being claimed"
            },
            "calculation_method": {
                "type": "object",
                "properties": {
                    "home_office": {
                        "type": "object",
                        "properties": {
                            "total_home_expenses": {"type": "number"},
                            "rental_percentage": {"type": "number", "description": "% attributable to rental"},
                            "hours_per_week": {"type": "number"},
                            "calculation_basis": {"type": "string", "enum": ["floor_area", "time_basis", "fixed_rate"]}
                        }
                    },
                    "mileage": {
                        "type": "object",
                        "properties": {
                            "total_km": {"type": "number"},
                            "rate_per_km": {"type": "number", "description": "IRD rate (currently $0.99/km)"},
                            "trips": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "date": {"type": "string", "format": "date"},
                                        "purpose": {"type": "string"},
                                        "km": {"type": "number"}
                                    }
                                }
                            }
                        }
                    },
                    "mobile_phone": {
                        "type": "object",
                        "properties": {
                            "total_bill": {"type": "number"},
                            "rental_percentage": {"type": "number"},
                            "basis": {"type": "string", "description": "How percentage was determined"}
                        }
                    }
                }
            },
            "supporting_documentation": {
                "type": "array",
                "items": {"type": "string"},
                "description": "List of supporting documents/receipts provided"
            },
            "pl_row": {
                "type": "integer",
                "default": 37,
                "description": "P&L Row 37: Other property costs"
            },
            "flags": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "flag_code": {
                            "type": "string",
                            "enum": ["missing_log", "high_percentage", "no_supporting_docs", "exceeds_typical"]
                        },
                        "message": {"type": "string"}
                    }
                }
            }
        }
    }
}
```

### P&L Row Mapping Addition

```python
# Add to PL_ROW_MAPPING
"home_office": {"row": 37, "type": "expense", "description": "Home office costs (rental portion)"},
"mileage": {"row": 37, "type": "expense", "description": "Travel to property (IRD rate)"},
"mobile_phone": {"row": 37, "type": "expense", "description": "Phone costs (rental portion)"},
```

### Validation Rules

```python
PERSONAL_EXPENSE_VALIDATION = {
    # Typical ranges - flag if exceeded
    "home_office_max_reasonable": 500,     # Per year
    "mileage_max_reasonable": 1000,        # Per year
    "mobile_max_reasonable": 300,          # Per year

    # IRD mileage rates
    "ird_mileage_rate_2024": 0.99,         # $/km for 2024
    "ird_mileage_rate_2025": 0.99,         # $/km for 2025 (check annually)

    # Warnings
    "require_log_for_mileage": True,
    "require_basis_for_percentage": True
}
```

---

## Depreciation Schedule Providers (Valocity & Valuit)

### Important: Two Different Services

**Critical:** For Year 1 Due Diligence, there are typically TWO separate property valuations to look for:

| Provider | Service | Cost Type | P&L Row |
|----------|---------|-----------|---------|
| **Valocity** | Property market valuation | Due Diligence | Row 18 |
| **Valuit** / **FordBaker** | Chattels depreciation schedule | Due Diligence | Row 18 |

### Why Both Matter

1. **Valocity** - Market value assessment for purchase decision
2. **Valuit/FordBaker** - Identifies depreciable chattels (carpet, blinds, appliances)

### Schema Update for Depreciation Schedule

```python
# Update depreciation schedule extraction to capture provider
"depreciation_schedule_providers": {
    "type": "object",
    "properties": {
        "depreciation_provider": {
            "type": "string",
            "enum": ["valuit", "fordbaker", "other"],
            "description": "Provider of depreciation schedule (chattels)"
        },
        "valuation_provider": {
            "type": "string",
            "enum": ["valocity", "qv", "corelogic", "other"],
            "description": "Provider of property market valuation"
        }
    }
}
```

### Updated Due Diligence Extraction

```python
# Update due_diligence in PL_ROW_MAPPING
"due_diligence": {
    "row": 18,
    "type": "expense",
    "description": "LIM, meth test, healthy homes, Valocity (valuation), Valuit/FordBaker (depreciation)"
},
```

### Prompt Addition for Due Diligence

```python
DUE_DILIGENCE_PROMPT_ADDITION = """
YEAR 1 DUE DILIGENCE - CHECK FOR ALL OF THESE:

1. LIM Report (Land Information Memorandum) - Council
2. Meth Test - Contamination testing
3. Healthy Homes Assessment - Compliance check
4. Smoke Alarm Certificate - Installation/compliance
5. Property Valuation (Valocity, QV, CoreLogic) - Market value
6. Depreciation Schedule (Valuit, FordBaker) - Chattels value

IMPORTANT: Items 5 and 6 are DIFFERENT services:
- Property valuation = What the property is worth (market value)
- Depreciation schedule = What chattels can be depreciated (carpet, blinds, etc.)

Look for BOTH in Year 1 documents. They are usually separate invoices.
Consolidate ALL due diligence costs into Row 18.
"""
```

---

## Interest Adjustment Entries - Critical Warning

### The Problem

Interest adjustments and credits appearing on bank statements can cause incorrect interest calculations if handled wrongly.

### Critical Rule: DO NOT SUBTRACT

**NEVER subtract these from gross interest:**
- Interest Adjustment (backdated corrections)
- Interest Credit (refunds)
- OFFSET Benefit (shows savings, not actual charge)
- Interest Received (this is income, not negative expense)

### Schema Enforcement

The exclusion reason enum already exists, but add explicit prompt warning:

```python
# Already in schema - ensure it's used correctly:
"exclusion_reason": {
    "type": ["string", "null"],
    "enum": [
        null,                    # Include in deduction
        "credit_not_debit",      # Interest received - DO NOT subtract
        "adjustment_entry",      # Backdated correction - already reflected
        "offset_benefit",        # Shows savings - not actual charge
        "capitalised",           # Added to principal - will appear in future charges
        "savings_interest"       # Interest on savings account - this is INCOME
    ]
}
```

### Prompt Addition for Interest Handling

```python
INTEREST_CRITICAL_WARNING = """
⚠️ CRITICAL: INTEREST ADJUSTMENT HANDLING ⚠️

When extracting interest from bank statements:

1. SUM ONLY INTEREST DEBITS (money going OUT for interest)
   ✓ Include: "Debit Interest", "Loan Interest", "Interest Charged"

2. DO NOT SUBTRACT THESE:
   ✗ "Interest Adjustment" - Already reflected in other charges
   ✗ "Interest Credit" - This is a refund, not negative expense
   ✗ "OFFSET Benefit" - Shows what you SAVED, not what you PAID
   ✗ Interest received on savings - This is INCOME (Row 8)

3. RECORD BUT DON'T SUBTRACT:
   - Track interest credits in total_interest_credits field
   - Track adjustments with exclusion_reason = "adjustment_entry"
   - These are for REFERENCE ONLY

EXAMPLE - CORRECT:
  Interest debit 1 May: $500.00
  Interest debit 15 May: $480.00
  Interest adjustment 20 May: -$50.00 (DON'T SUBTRACT)

  total_interest_debits = $980.00 ✓ (NOT $930.00)

WHY? The adjustment is usually a backdated correction that's already
reflected in other interest charges. Subtracting it would understate
the deduction.
"""
```

### Validation Check

```python
def validate_interest_calculation(extraction_result: dict) -> List[str]:
    """Validate interest wasn't incorrectly reduced."""
    warnings = []

    interest_analysis = extraction_result.get("interest_analysis", {})

    # Check if credits were improperly subtracted
    total_debits = interest_analysis.get("total_interest_debits", 0)
    total_credits = interest_analysis.get("total_interest_credits", 0)

    # Sum from transactions
    transaction_sum = sum(
        t["amount"] for t in interest_analysis.get("interest_transactions", [])
        if t.get("include_in_deduction", False)
    )

    # If transaction sum is less than total_debits, something was subtracted
    if transaction_sum < total_debits * 0.99:  # Allow 1% tolerance
        warnings.append(
            "WARNING: Interest deduction may be understated. "
            "Verify that adjustments/credits were NOT subtracted from gross interest."
        )

    # Flag if there are credits that might have been subtracted
    if total_credits > 0:
        warnings.append(
            f"INFO: ${total_credits:.2f} in interest credits detected. "
            "Ensure these were NOT subtracted from the deductible amount."
        )

    return warnings
```

---

## Final Updated Implementation Priority

| Priority | Change | Impact | Dependencies |
|----------|--------|--------|--------------|
| **P0** | Remove 5-page limit + batch processing | Stops data loss | None |
| **P0** | Enhanced retry/rate limiting | Enables 50+ docs | None |
| **P0** | Database migration | Stores new fields | None |
| **P0** | P&L Row mapping table | Correct categorization | None |
| **P0** | Resident society category | Correct P&L row (36) | None |
| **P1** | Tool Use for classification | Consistent output | None |
| **P1** | Tool Use for bank statements | Schema enforcement | P0 |
| **P1** | SSE batch progress events | UI feedback | P0 |
| **P1** | Accounting fees rule ($862.50) | Required row | P0 |
| **P1** | Short-term rental detection | Correct GST handling | P0 |
| **P2** | Tool Use for all doc types | Full coverage | P1 |
| **P2** | Multi-pass extraction | Better accuracy | P1 |
| **P2** | UI updates for new data | User visibility | P1 |
| **P2** | CSV/Excel direct reading | Performance | P1 |
| **P2** | Progress tracker updates | Batch progress UI | P1 |
| **P3** | Verification pass | Error catching | P2 |
| **P3** | Phase 2 integration updates | Use richer data | P2 |
| **P3** | Backwards compatibility | Migration | P2 |
| **P3** | Multi-property detection | Error prevention | P2 |
| **P3** | Date/currency parsing | Data accuracy | P2 |
| **P4** | Testing suite | Quality assurance | P3 |
| **P4** | Image quality assessment | Extraction reliability | P3 |
| **P4** | Document period validation | Tax year alignment | P3 |
