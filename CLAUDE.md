# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

NZ Property Tax Document Review System - An AI-powered FastAPI application that automatically classifies, analyzes, and validates tax documents for New Zealand rental property tax returns (IR3R) using Claude AI Vision.

## System Architecture

The system operates in **two main phases**:

### Phase 1: Document Intake & Classification

Phase 1 handles document upload, classification, and data extraction:

#### Processing Flow
1. **File Upload & Deduplication**
   - Files saved via `FileHandler` with content hashing
   - Duplicate detection (filename and content hash)
   - Supports PDF, Excel, CSV, images

2. **Document Classification** (per document)
   - Claude Vision analyzes each document
   - Uses Tool Use schema (`DOCUMENT_CLASSIFICATION_TOOL`) for guaranteed structure
   - Returns: document_type, confidence, address_verification, flags
   - 17+ document types recognized

3. **Transaction Extraction** (for financial documents)
   - Financial documents trigger full extraction via Tool Use
   - Bank statements: `BANK_STATEMENT_EXTRACTION_TOOL`
   - Settlement statements: `SETTLEMENT_STATEMENT_EXTRACTION_TOOL`
   - PM statements, loan statements, etc. have dedicated extractors
   - ALL transactions extracted with preliminary categorization

4. **Completeness Review**
   - Final Claude call reviews all document summaries
   - Checks for missing required documents
   - Identifies blocking issues (wrong insurance type, missing statements)
   - Returns: status (complete/incomplete/blocked), recommendations

5. **Flagged Transaction Collection**
   - Transactions needing review aggregated from all documents
   - Critical flags can downgrade status from complete → incomplete

#### Key Features
- **Sequential processing**: Documents processed one at a time (rate limit safety)
- **SSE progress tracking**: Real-time updates via `process_tax_return_with_progress()`
- **Duplicate handling**: Duplicates detected but still tracked
- **Tool Use schemas**: Guaranteed JSON structure for all extractions

#### Extraction Validation (`app/services/phase1_document_intake/extraction_validator.py`)
Enabled via `ENABLE_EXTRACTION_VERIFICATION=true` (default: true)

**1. Balance Reconciliation**
- Bank statements: `opening_balance + credits - debits = closing_balance`
- Loan statements: Verify interest totals match transaction sum
- PM statements: Verify income - expenses - disbursed = closing balance
- Auto-flags documents with variance > $0.02

**2. Verification Pass (Second Claude Call)**
- Shows Claude what was extracted
- Asks it to verify against source document
- Identifies missing transactions
- Auto-adds suggested corrections with `needs_review=true` flag

**3. Cross-Document Validation**
- Interest validation: Bank statement interest ≈ Loan statement interest (5% tolerance)
- Rent validation: PM gross rent vs bank deposits
- Settlement validation: Settlement transactions appear in bank statement

### Phase 2: Transaction Processing & Learning

Phase 2 has **two components** that work together:

#### Component A: Transaction Processor (Multi-Layer Categorization)
- Reads pre-extracted transactions from Phase 1 (avoids double API calls)
- Builds cross-document context (loan accounts, client names)
- **6-layer categorization** (in priority order):
  1. Document Context - matches loan account numbers, owner names
  2. YAML Patterns - regex/payee matching from `app/rules/`
  3. Learned Exact - exact description match in TransactionPattern table
  4. Learned Fuzzy - PostgreSQL pg_trgm similarity matching
  5. RAG Learnings - semantic search across Pinecone namespaces
  6. Claude AI - batch categorization (20-25 per call)
- Saves categorized transactions to database

#### Component B: AI Brain (Accountant Workflow)
- Located in `app/services/phase2_ai_brain/brain.py`
- Implements real accountant workflow in a single Claude call
- Generates complete P&L workings with full audit trail
- **Processing steps**:
  1. PM Statements (primary rent source)
  2. Bank Statements (cross-reference)
  3. Loan Statements (extract interest only)
  4. Invoices (match to payments, >$800 rule)
  5. Cross-validation between documents
  6. QA validation with auto-correction

#### Generate Workings Flow
When "Generate Workings" is triggered (`POST /api/workings/{id}/process`):
```
Step 1: TransactionProcessor.process_tax_return_transactions()
        → Categorizes all transactions using 6-layer approach

Step 2: AIBrain.process_tax_return()
        → Generates complete workings with calculation logic
```

## Common Commands

```bash
# Install dependencies
poetry install

# Run development server (with hot reload)
poetry run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

# Run tests
poetry run pytest tests/ -v

# Run single test file
poetry run pytest tests/test_document_processor.py -v

# Run tests with coverage
poetry run pytest tests/ --cov=app --cov-report=html

# Database migrations
poetry run alembic upgrade head
poetry run alembic revision --autogenerate -m "description"

# Format code
poetry run black app tests
poetry run isort app tests

# Lint
poetry run ruff check app tests
poetry run mypy app

# Docker
docker-compose up --build
docker-compose down -v  # cleanup with volumes
```

## Core Flow

### Phase 1: Document Upload → Classification → Extraction
1. **Document Upload** (`app/api/routes.py`) → Web form or API receives files
2. **DocumentProcessor.process_tax_return_with_progress()** orchestrates:
   - Saves files via `FileHandler` (with duplicate detection)
   - For each document (sequential):
     - Claude Vision classifies document type
     - Financial documents → full transaction extraction via Tool Use
     - Results persisted to Document.extracted_data
   - Final completeness review → missing docs, blocking issues
   - Returns `TaxReturnReview` with status

### Phase 2: Transaction Processing → Workings Generation
3. **TransactionProcessor.process_tax_return_transactions()** → Categorization:
   - Reads pre-extracted transactions from Document.extracted_data
   - 6-layer categorization (context → YAML → learned → fuzzy → RAG → Claude)
   - Saves categorized Transaction records

4. **AIBrain.process_tax_return()** → Workings Generation:
   - Loads all context (documents, transactions, rules, RAG)
   - Single Claude call with accountant workflow
   - Returns complete P&L workings with audit trail

5. **WorkbookGenerator** → Creates Lighthouse Financial Excel template

## Key Services

### Phase 1 Services (`app/services/phase1_document_intake/`)
- **DocumentProcessor**: Main orchestrator - `process_tax_return()` and `process_tax_return_with_progress()`
- **ClaudeClient**: Claude API calls with retry logic, rate limiting, vision processing
- **FileHandler**: Processes PDFs (via pdf2image), Excel, CSV, images; content hashing
- **document_inventory.py**: Tracks provided, missing, excluded documents with severity levels
- **schemas.py**: Tool Use schemas for guaranteed JSON extraction:
  - `DOCUMENT_CLASSIFICATION_TOOL` - 17 document types
  - `BANK_STATEMENT_EXTRACTION_TOOL` - Full transaction extraction
  - `SETTLEMENT_STATEMENT_EXTRACTION_TOOL` - Line item extraction
  - `PL_ROW_MAPPING` - Category to P&L row mapping
- **prompts.py**: Classification and extraction prompts, flagging rules

### Phase 2 Services (`app/services/phase2_feedback_learning/`)
- **KnowledgeStore**: Pinecone integration for RAG with 8 namespaces
- **EmbeddingsService**: OpenAI text-embedding-3-small integration
- **skill_learning_service.py**: Skill learnings and teachings
- **rag_categorization.py**: Pattern matching from RAG

### Phase 2 AI Brain (`app/services/phase2_ai_brain/`)
- **brain.py**: Accountant workflow orchestrator - generates complete workings
- **workings_models.py**: Pydantic models for workings output (LineItem, CalculationLogic, etc.)

### Core Services (`app/services/`)
- **transaction_processor.py**: Main orchestrator - reads Phase 1 data, applies feedback
- **transaction_categorizer.py**: 6-layer categorization engine
- **rag_categorization_integration.py**: RAG integration bridge
- **categorization_trace.py**: Full audit trail for decisions
- **tax_rules_service.py**: Interest deductibility and tax rule application

## Database Models (`app/models/db_models.py`)

### Core Models
- **Client** → **TaxReturn** (1:N) → **Document** (1:N) → **Transaction** (1:N)
- **PLRowMapping**: Category to P&L row mapping
- **TransactionSummary**: Aggregated totals by category
- **TransactionPattern**: Learned categorization patterns
- **CategoryFeedback**: User corrections audit trail

### Workings Models (AI Brain output)
- **TaxReturnWorkings**: Complete workings with income/expense breakdowns
- **WorkingsFlag**: Issues found during processing (severity, category, action)
- **DocumentRequest**: Missing documents to request from client
- **ClientQuestion**: Questions to ask client for clarification
- **DocumentInventoryRecord**: Document completeness tracking
- **SkillLearning**: Domain knowledge stored for RAG retrieval

Statuses: `PENDING`, `COMPLETE`, `INCOMPLETE`, `BLOCKED`

## API Structure

### Phase 1 Routes
- `/api/returns` - POST: Create tax return with documents, GET: List all
- `/api/returns/{id}` - GET: Single return
- `/api/returns/{id}/documents` - GET: Documents for a return

### Phase 2 Routes - Transactions
- `/api/transactions/process/{tax_return_id}` - POST: Process transactions
- `/api/transactions/{id}` - PUT: Update category (auto-learns)
- `/api/transactions/bulk-update` - POST: Bulk update
- `/api/transactions/save-learnings/{tax_return_id}` - POST: Save to RAG
- `/api/transactions/totals/{tax_return_id}` - GET: Financial totals
- `/api/transactions/workbook/{id}` - POST/GET: Generate/download workbook

### Phase 2 Routes - Workings (AI Brain)
- `/api/workings/{tax_return_id}/process` - POST: Generate workings (runs both TransactionProcessor + AIBrain)
- `/api/workings/{tax_return_id}` - GET: Get complete workings data
- `/api/workings/{tax_return_id}/flags` - GET: Get all flags
- `/api/workings/flags/{flag_id}/resolve` - PUT: Resolve a flag
- `/api/workings/{tax_return_id}/requests` - GET: Document requests
- `/api/workings/{tax_return_id}/questions` - GET: Client questions
- `/api/workings/{tax_return_id}/feedback` - POST: Submit calculation feedback
- `/api/workings/{tax_return_id}/confirm` - POST: Confirm calculation is correct

### Analytics Routes
- `/api/transactions/{id}/trace` - GET: Categorization decision trace
- `/api/categorization/{id}/analytics` - GET: Analytics dashboard

Web routes serve Jinja2 templates at `/`, `/upload`, `/result/{id}`, `/transactions/{id}`, `/workings/{id}`

## NZ Tax Domain Knowledge

### Document Types (17 recognized)
| Type | Description | Has Tool Use Extraction |
|------|-------------|------------------------|
| `bank_statement` | Bank account transactions | Yes (batch) |
| `loan_statement` | Mortgage/loan transactions | Yes (batch) |
| `property_manager_statement` | PM statements (rent, fees) | Yes (batch) |
| `settlement_statement` | Property purchase/sale | Yes |
| `depreciation_schedule` | Asset depreciation | Yes |
| `body_corporate` | BC levies (operating/reserve) | Yes |
| `rates` | Council rates | Yes |
| `water_rates` | Water charges | Yes |
| `landlord_insurance` | Landlord insurance policy | Yes |
| `maintenance_invoice` | Repair/maintenance invoices | Yes |
| `resident_society` | Resident society levies | Yes |
| `healthy_homes` | Healthy homes compliance | No |
| `ccc` | Code Compliance Certificate | No |
| `smoke_alarm` | Smoke alarm certificate | No |
| `meth_test` | Meth testing report | No |
| `lim_report` | Land Information Memorandum | No |
| `other` / `invalid` | Unrecognized/invalid | No |

### Document Inventory Tracking
Phase 1 tracks document status via `DocumentInventory`:
| Status | Description |
|--------|-------------|
| `PROVIDED` | Document received and extracted |
| `MISSING` | Required but not provided |
| `EXCLUDED` | Provided but not relevant |
| `PARTIAL` | Partially provided (missing months) |
| `WRONG_TYPE` | Wrong document type (e.g., home vs landlord insurance) |
| `DUPLICATE` | Duplicate of another document |

### Blocking Conditions (Critical)
- Wrong insurance type (home & contents vs landlord insurance)
- Missing bank/loan statements
- First-year purchase missing settlement statement
- New build missing CCC for 100% interest deductibility
- Property address mismatch on key documents

### Interest Deductibility Rules
- New builds (CCC after 27 March 2020): 100% deductible
- Existing properties: 80% deductible (FY25), 100% (FY26)

## RAG System

### Pinecone Namespaces (8 total)
| Namespace | Purpose |
|-----------|---------|
| `transaction-coding` | Transaction categorization patterns |
| `skill_learnings` | General domain knowledge and teachings |
| `document-review` | Document classification feedback |
| `tax-rules` | Tax deductibility and treatment rules |
| `gst-rules` | GST treatment and rules |
| `pnl-mapping` | P&L row mapping knowledge |
| `common-errors` | Common error patterns and corrections |
| `workbook-structure` | Workbook/spreadsheet structure knowledge |

### Optimized Search
- Single embedding generated per query
- All namespaces searched in parallel
- Results combined and sorted by relevance score

### Learning Flow
1. User reviews/corrects transaction category
2. System marks transaction as `manually_reviewed = True`
3. On "Save & Commit Learnings":
   - Only manually reviewed transactions are saved
   - Duplicate check via semantic search (0.95 threshold)
   - Stored in Pinecone with OpenAI embeddings

### Workings Feedback Flow
1. User confirms or corrects a calculation on workings page
2. Feedback saved as SkillLearning in PostgreSQL
3. Embedded and stored in Pinecone `skill_learnings` namespace
4. Optional: Trigger recalculation of single line item or full workings

## Environment Variables

Required:
- `ANTHROPIC_API_KEY` - Claude API key
- `DATABASE_URL` - PostgreSQL async connection string

Required for RAG:
- `PINECONE_API_KEY` - Pinecone API key
- `PINECONE_INDEX_HOST` - Pinecone index host
- `OPENAI_API_KEY` - For embeddings

Optional:
- `CLAUDE_MODEL` - Default: `claude-opus-4-5-20251101`

## Key Implementation Details

### Data Flow Optimization
- **No double API calls**: Phase 1 extracts all transactions → stored in `Document.extracted_data` → Phase 2 reads from database
- **Feedback flow**: Phase 1 user feedback flows to Phase 2 for improved categorization

### Claude API Handling
- **Sequential document processing**: Documents processed one at a time to avoid rate limits
- **Rate limiting**: Global semaphore + minimum request interval
- **Retry logic**: Exponential backoff with jitter (base 1s, max delay configurable)
- **Low temperature (0.1)**: Used for deterministic classification results
- **Tool Use**: Guarantees JSON schema compliance for all extractions

### Vision Processing
- **PDF handling**: Scanned PDFs converted to images via pdf2image/poppler
- **Image limits**: Max 5 pages per document, resized to 1568x1568 max
- **Supported formats**: PDF, PNG, JPG, Excel, CSV

### Processing Features
- **SSE deduplication**: Progress tracker registry prevents duplicate processing on reconnects
- **Batch categorization**: Transactions sent to Claude in batches of 20-25
- **Settlement statement ordering**: Line items extracted in document order with auto-calculated deductibles
- **Duplicate detection**: Content hash + filename matching within upload and across returns

## Transaction Summary Cards

The transaction review page shows key financial totals:
- **Total Income**: Rent, bank contributions, rates recovered, etc.
- **Interest Expense**: Mortgage interest (deductible portion)
- **Other Expenses**: Rates, insurance, repairs, etc.
- **Total Deductions**: Interest + other expenses
- **Net Rental Income**: Taxable amount for IR3R
- **Needs Review**: Items requiring attention

## Category Groups

Categories are organized into groups for UI display:
- Income
- Interest & Finance
- Rates & Levies
- Insurance
- Property Management
- Repairs & Maintenance
- Utilities
- Professional Services
- Other Expenses
- Excluded (non-deductible items like principal repayments, personal expenses)

## AI Brain Workings Output

The AI Brain generates structured workings with full audit trail:

### Line Item Structure
Each income/expense line item includes:
- `category_code`: Category identifier
- `pl_row`: P&L row number (Lighthouse template)
- `gross_amount` / `deductible_amount`
- `source_code`: BS (Bank), PM (Property Manager), LS (Loan), SS (Settlement), etc.
- `verification_status`: verified, needs_review, estimated
- `calculation_logic`: Full audit trail with steps
- `transactions`: List of individual transactions

### Key Business Rules (in AI Brain prompt)
| Rule | Description |
|------|-------------|
| Interest | Sum LOAN INTEREST debits only; exclude credits/adjustments/offsets |
| Year 1 Rates | Bank Paid + (Vendor Instalment − Vendor Credit) from settlement |
| Legal Fees | Under $10k = fully deductible (not capital) |
| Body Corporate | Operating fund only (reserve = capital) |
| PM Fees | Always GST-inclusive (base + GST) |
| Repairs >$800 | Must have invoice |
| Accounting Fees | Standard $862.50 always included |
| Due Diligence | LIM, meth test, valuations → Row 18 |
| Depreciation | Pro-rate by months if partial year |

### P&L Row Mapping (Lighthouse Template)
| Row | Category |
|-----|----------|
| 6 | Rental Income |
| 7 | Water Recovered |
| 8 | Bank Contribution |
| 12 | Advertising |
| 13 | Agent Fees (PM + letting, GST-inclusive) |
| 15 | Body Corporate (operating only) |
| 16 | Consulting & Accounting ($862.50) |
| 17 | Depreciation |
| 18 | Due Diligence |
| 24 | Insurance |
| 25 | Interest Expense |
| 27 | Legal Fees |
| 34 | Rates |
| 35 | Repairs & Maintenance |
| 36 | Resident Society |
| 41 | Water Rates |
