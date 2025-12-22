
# NZ Property Tax Document Review System

## Phase 1 & 2 Complete - Full AI-Powered Tax Return Processing Pipeline

A production-ready system that automatically classifies documents, extracts and categorizes transactions, and generates complete tax returns for New Zealand rental properties using Claude AI Vision and intelligent transaction processing.

## System Status

- **Version**: 3.5.0 (AI Brain & Workings Generation)
- **Status**: Production Ready with Full Audit Trail
- **AI Model**: Claude Opus 4.5 (claude-opus-4-5-20251101)
- **Transaction Processing**: Universal bank statement support via Claude AI
- **Categorization**: 6-layer with complete decision tracing
- **RAG Integration**: Pinecone vector database with 8 namespaces
- **AI Brain**: Accountant workflow with complete P&L workings generation
- **Workbook Template**: Lighthouse Financial compliant format
- **Last Updated**: December 22, 2024

### Recent Updates (v3.5.0)

- **AI Brain**: New accountant workflow that generates complete P&L workings with audit trail
- **Workings Generation**: Full line-item breakdown with calculation logic and verification
- **6-Layer Categorization**: Added document context matching as highest priority layer
- **Extraction Validation**: Balance reconciliation and verification pass for accuracy
- **8 RAG Namespaces**: Expanded knowledge store for specialized domains
- **Document Inventory**: Track provided, missing, excluded, and duplicate documents

## Table of Contents

- [Architecture Overview](#architecture-overview)
- [Phase 1: Document Intake & Classification](#phase-1-document-intake--classification)
- [Phase 2: Transaction Processing & Learning](#phase-2-transaction-processing--learning)
- [AI Brain & Workings Generation](#ai-brain--workings-generation)
- [Features](#features)
- [Installation](#installation)
- [API Documentation](#api-documentation)
- [Document Types](#document-types)
- [NZ Tax Business Rules](#nz-tax-business-rules)
- [Development](#development)

## Architecture Overview

The system operates in two main phases with an integrated RAG (Retrieval-Augmented Generation) learning system:

```
                    PHASE 1                                    PHASE 2
         Document Intake & Classification        Transaction Processing & Learning
    ┌──────────────────────────────────────┐    ┌──────────────────────────────────────┐
    │                                      │    │                                      │
    │  ┌─────────┐    ┌─────────────────┐ │    │  ┌─────────────────┐                 │
    │  │ Upload  │───>│ Claude Vision   │ │    │  │ Pre-extracted   │                 │
    │  │ Files   │    │ Classification  │ │    │  │ Transactions    │                 │
    │  └─────────┘    └────────┬────────┘ │    │  │ from Phase 1    │                 │
    │                          │          │    │  └────────┬────────┘                 │
    │  ┌───────────────────────▼────────┐ │    │           │                          │
    │  │ Extract ALL Transactions       │ │    │  ┌────────▼────────┐                 │
    │  │ (bank statements, loan stmts)  │─┼────┼─>│ Apply Phase 1   │                 │
    │  │ + Preliminary Categorization   │ │    │  │ Feedback        │                 │
    │  └───────────────────────┬────────┘ │    │  └────────┬────────┘                 │
    │                          │          │    │           │                          │
    │  ┌───────────────────────▼────────┐ │    │  ┌────────▼────────┐                 │
    │  │ Flag transactions needing      │ │    │  │ Query RAG       │                 │
    │  │ review (large, unusual, etc)   │ │    │  │ Patterns        │                 │
    │  └───────────────────────┬────────┘ │    │  └────────┬────────┘                 │
    │                          │          │    │           │                          │
    │  ┌───────────────────────▼────────┐ │    │  ┌────────▼────────┐                 │
    │  │ User Reviews Documents         │ │    │  │ Multi-layer     │                 │
    │  │ Submits Feedback               │ │    │  │ Categorization  │                 │
    │  └───────────────────────┬────────┘ │    │  │ (YAML→Learned   │                 │
    │                          │          │    │  │  →RAG→Claude)   │                 │
    └──────────────────────────┼──────────┘    │  └────────┬────────┘                 │
                               │               │           │                          │
                               │               │  ┌────────▼────────┐                 │
                               └───────────────┼─>│ User Reviews    │                 │
                                               │  │ Transactions    │                 │
                                               │  └────────┬────────┘                 │
                                               │           │                          │
                                               │  ┌────────▼────────┐                 │
                                               │  │ Save & Commit   │───> Pinecone   │
                                               │  │ Learnings       │    Vector DB   │
                                               │  │ (with dedup)    │                 │
                                               │  └────────┬────────┘                 │
                                               │           │                          │
                                               │  ┌────────▼────────┐                 │
                                               │  │ Generate        │                 │
                                               │  │ Workbook        │                 │
                                               │  └─────────────────┘                 │
                                               └──────────────────────────────────────┘
```

### Data Flow Optimization

The system is optimized to minimize Claude API calls:

1. **Phase 1 extracts ALL transactions** during document classification
2. **Phase 2 reads pre-extracted data** from the database (no redundant API calls)
3. **Feedback flows from Phase 1 to Phase 2** for improved categorization
4. **RAG patterns are queried** before falling back to Claude AI

## Phase 1: Document Intake & Classification

Phase 1 handles document upload, classification, and initial transaction extraction.

### Capabilities

- **Multi-format Support**: PDF (digital & scanned), Images (PNG/JPG/JPEG), Excel (.xlsx/.xls), CSV
- **Intelligent Classification**: Automatically identifies 15+ NZ-specific tax document types
- **Full Transaction Extraction**: Extracts ALL transactions from financial documents with preliminary categorization
- **Completeness Analysis**: Identifies missing documents and blocking issues
- **Vision AI Integration**: Uses Claude's vision capabilities for scanned document analysis

### Transaction Extraction in Phase 1

Financial documents (bank statements, loan statements, property manager statements) now have ALL transactions extracted during Phase 1, not just flagged ones:

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
      "needs_review": false
    }
  ],
  "transaction_summary": {
    "total_count": 45,
    "income_count": 12,
    "expense_count": 33,
    "flagged_count": 5,
    "total_income": 15600.00,
    "total_expenses": -8234.50
  }
}
```

## Phase 2: Transaction Processing & Learning

Phase 2 combines knowledge management and transaction processing into a unified workflow.

### Capabilities

- **Knowledge Store (Pinecone RAG)**: Semantic search for past patterns and learnings
- **Pattern Learning**: System learns from user corrections and feedback
- **Multi-layer Categorization**: YAML patterns → Learned patterns → RAG → Claude AI
- **Tax Compliance**: Automatic interest deductibility rules (80% for existing, 100% for new builds)
- **Professional Export**: Lighthouse Financial template format

### 6-Layer Categorization Flow

```
Transaction Input
        │
        ▼
┌───────────────────┐
│ 1. Document       │ ─── Loan account numbers, owner names (95%)
│    Context        │
└────────┬──────────┘
         │ No match
         ▼
┌───────────────────┐
│ 2. YAML Patterns  │ ─── Regex/payee matching from rules (95%)
└────────┬──────────┘
         │ No match
         ▼
┌───────────────────┐
│ 3. Learned Exact  │ ─── Exact description match (90%)
└────────┬──────────┘
         │ No match
         ▼
┌───────────────────┐
│ 4. Learned Fuzzy  │ ─── PostgreSQL pg_trgm similarity (80%)
└────────┬──────────┘
         │ No match
         ▼
┌───────────────────┐
│ 5. RAG Semantic   │ ─── Pinecone vector similarity
│    Search         │
└────────┬──────────┘
         │ Low confidence
         ▼
┌───────────────────┐
│ 6. Claude AI      │ ─── Batch processing (20-25 per call)
│    Fallback       │
└───────────────────┘
```

### Save & Commit Learnings

When users review and correct transactions, the system:

1. **Only saves manually reviewed transactions** (where user changed the category)
2. **Checks for duplicates** using semantic search (0.95 threshold)
3. **Stores in Pinecone** with the `transaction-coding` namespace
4. **Creates embeddings** using OpenAI text-embedding-3-small

### RAG Namespaces

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

## AI Brain & Workings Generation

The AI Brain (`app/services/phase2_ai_brain/brain.py`) implements a real accountant workflow to generate complete P&L workings with full audit trail.

### Generate Workings Flow

When "Generate Workings" is triggered:

```
Step 1: TransactionProcessor.process_tax_return_transactions()
        → Categorizes all transactions using 6-layer approach

Step 2: AIBrain.process_tax_return()
        → Generates complete workings with calculation logic
```

### Processing Steps

1. **PM Statements** - Primary rent source
2. **Bank Statements** - Cross-reference transactions
3. **Loan Statements** - Extract interest only
4. **Invoices** - Match to payments, apply >$800 rule
5. **Cross-validation** - Between documents
6. **QA Validation** - Auto-correction

### Workings Output Structure

Each income/expense line item includes:

| Field | Description |
|-------|-------------|
| `category_code` | Category identifier |
| `pl_row` | P&L row number (Lighthouse template) |
| `gross_amount` | Total amount before adjustments |
| `deductible_amount` | Tax deductible portion |
| `source_code` | BS (Bank), PM (Property Manager), LS (Loan), SS (Settlement) |
| `verification_status` | verified, needs_review, estimated |
| `calculation_logic` | Full audit trail with steps |
| `transactions` | List of individual transactions |

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

## Features

### Core Features

| Feature | Description |
|---------|-------------|
| **Universal AI Extraction** | Claude AI processes ANY bank statement format - no bank-specific parsers needed |
| **Multi-layer Categorization** | YAML patterns, learned patterns, RAG, and Claude AI fallback |
| **Tax Compliance** | Automatic interest deductibility (80%/100%), GST handling |
| **Professional Export** | Lighthouse Financial template with IRD compliance |
| **Categorization Audit** | Full trace of every categorization decision |
| **Learning System** | Saves corrections to RAG for future accuracy |

### Technical Features

- **Async Processing**: High-performance async/await architecture
- **Database Persistence**: PostgreSQL with async SQLAlchemy ORM
- **Error Recovery**: Comprehensive error handling and logging
- **Docker Ready**: Full containerization with docker-compose
- **Performance Optimized**: Batching and caching for efficiency

## Directory Structure

```
property-tax-agent/
├── app/
│   ├── api/
│   │   ├── routes.py                    # Phase 1 document routes
│   │   ├── transaction_routes.py        # Phase 2 transaction processing
│   │   ├── workings_routes.py           # AI Brain workings endpoints
│   │   └── categorization_analytics.py  # Analytics endpoints
│   ├── models/
│   │   └── db_models.py                 # SQLAlchemy ORM models
│   ├── schemas/
│   │   ├── documents.py                 # Document schemas
│   │   └── transactions.py              # Transaction schemas
│   ├── services/
│   │   ├── phase1_document_intake/      # Document processing
│   │   │   ├── claude_client.py         # Claude AI integration
│   │   │   ├── document_processor.py    # Main orchestration
│   │   │   ├── document_inventory.py    # Document tracking
│   │   │   ├── extraction_validator.py  # Balance reconciliation
│   │   │   ├── file_handler.py          # File processing
│   │   │   ├── schemas.py               # Tool Use schemas
│   │   │   └── prompts.py               # AI prompts
│   │   ├── phase2_feedback_learning/    # Knowledge & RAG system
│   │   │   ├── embeddings.py            # OpenAI embeddings
│   │   │   ├── knowledge_store.py       # Pinecone integration
│   │   │   └── skill_learning_service.py
│   │   ├── phase2_ai_brain/             # AI Brain (Accountant Workflow)
│   │   │   ├── brain.py                 # Main workings orchestrator
│   │   │   └── workings_models.py       # Pydantic models for output
│   │   ├── transaction_processor.py     # Main transaction orchestrator
│   │   ├── transaction_extractor_claude.py  # Universal AI extractor
│   │   ├── transaction_categorizer.py   # 6-layer categorization
│   │   ├── rag_categorization_integration.py # RAG integration
│   │   ├── categorization_trace.py      # Decision tracing
│   │   ├── tax_rules_service.py         # Tax compliance rules
│   │   └── workbook_generator.py        # Excel generator
│   ├── rules/
│   │   └── categorization.yaml          # Transaction patterns
│   ├── skills/
│   │   └── nz_rental_returns/           # NZ tax domain knowledge
│   ├── templates/                       # Jinja2 HTML templates
│   ├── config.py                        # Application configuration
│   ├── database.py                      # Database setup
│   └── main.py                          # FastAPI application
├── migrations/                          # Alembic migrations
├── scripts/                             # Utility scripts
│   └── reindex_teachings.py             # RAG re-indexing
├── tests/                               # Test suite
├── uploads/                             # Document storage
│   └── workbooks/                       # Generated Excel files
├── docker-compose.yml
├── Dockerfile
├── pyproject.toml
└── README.md
```

## Installation

### Prerequisites

- Python 3.12+
- PostgreSQL 15+
- Poppler (for PDF processing)
- Docker & Docker Compose (optional)

### Quick Start with Docker

```bash
# Clone the repository
git clone https://github.com/MitchWo/Property-Accounting-Automation.git
cd property-tax-agent

# Copy environment variables
cp .env.example .env
# Edit .env and add your API keys

# Start with Docker
docker-compose up --build

# Access at http://localhost:8000
```

### Local Development

```bash
# Install dependencies
pip install poetry
poetry install

# Install system dependencies (macOS)
brew install poppler postgresql

# Setup database
createdb property_tax
poetry run alembic upgrade head

# Run the application
poetry run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

## Environment Variables

```bash
# Required
ANTHROPIC_API_KEY=your_api_key_here
DATABASE_URL=postgresql+asyncpg://user:pass@localhost:5432/property_tax

# RAG System (Required for learning)
PINECONE_API_KEY=your_pinecone_key
PINECONE_INDEX_HOST=your-index.svc.pinecone.io
OPENAI_API_KEY=your_openai_key

# Optional
CLAUDE_MODEL=claude-opus-4-5-20251101
MAX_FILE_SIZE_MB=50
LOG_LEVEL=INFO
```

## API Documentation

### Phase 1 Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/returns` | POST | Upload documents and create tax return |
| `/api/returns` | GET | List all tax returns |
| `/api/returns/{id}` | GET | Get single tax return |
| `/api/returns/{id}/documents` | GET | Get documents for a return |

### Phase 2 Endpoints - Transactions

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/transactions/process/{tax_return_id}` | POST | Process all transactions |
| `/api/transactions/{transaction_id}` | PUT | Update transaction category |
| `/api/transactions/bulk-update` | POST | Bulk update categories |
| `/api/transactions/save-learnings/{tax_return_id}` | POST | Save reviewed transactions to RAG |
| `/api/transactions/totals/{tax_return_id}` | GET | Get financial totals |
| `/api/transactions/workbook/{tax_return_id}` | POST | Generate Excel workbook |
| `/api/transactions/workbook/{tax_return_id}/download` | GET | Download workbook |

### Phase 2 Endpoints - Workings (AI Brain)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/workings/{tax_return_id}/process` | POST | Generate workings (TransactionProcessor + AIBrain) |
| `/api/workings/{tax_return_id}` | GET | Get complete workings data |
| `/api/workings/{tax_return_id}/flags` | GET | Get all flags/issues |
| `/api/workings/flags/{flag_id}/resolve` | PUT | Resolve a flag |
| `/api/workings/{tax_return_id}/requests` | GET | Get document requests |
| `/api/workings/{tax_return_id}/questions` | GET | Get client questions |
| `/api/workings/{tax_return_id}/feedback` | POST | Submit calculation feedback |
| `/api/workings/{tax_return_id}/confirm` | POST | Confirm calculation is correct |

### Analytics Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/transactions/{id}/trace` | GET | Get categorization decision trace |
| `/api/categorization/{tax_return_id}/analytics` | GET | Get categorization analytics |
| `/api/categorization/{tax_return_id}/audit-report` | GET | Get full audit report |

## Document Types Supported (17 Types)

| Type | Description | Has Transaction Extraction |
|------|-------------|---------------------------|
| `bank_statement` | ANY bank format (AI-powered) | Yes (batch) |
| `loan_statement` | ANY loan format (AI-powered) | Yes (batch) |
| `property_manager_statement` | PM statements | Yes (batch) |
| `settlement_statement` | Property purchase/sale | Yes |
| `depreciation_schedule` | Valuit/FordBaker reports | Yes |
| `body_corporate` | Body corp levies (operating/reserve) | Yes |
| `rates` | Council rates notices | Yes |
| `water_rates` | Water charges | Yes |
| `landlord_insurance` | Rental property insurance | Yes |
| `maintenance_invoice` | Repair/maintenance invoices | Yes |
| `resident_society` | Resident society levies | Yes |
| `healthy_homes` | Compliance reports | No |
| `ccc` | Code Compliance Certificates | No |
| `smoke_alarm` | Safety certificates | No |
| `meth_test` | Contamination testing | No |
| `lim_report` | Land Information Memorandum | No |
| `other` / `invalid` | Unrecognized/invalid | No |

### Document Inventory Tracking

| Status | Description |
|--------|-------------|
| `PROVIDED` | Document received and extracted |
| `MISSING` | Required but not provided |
| `EXCLUDED` | Provided but not relevant |
| `PARTIAL` | Partially provided (missing months) |
| `WRONG_TYPE` | Wrong document type (e.g., home vs landlord insurance) |
| `DUPLICATE` | Duplicate of another document |

## Blocking Conditions

The system automatically detects critical issues:

1. **Wrong Insurance Type**: Home & contents instead of landlord insurance
2. **Address Mismatch**: Documents for different properties
3. **Wrong Account**: Personal bank statements instead of rental account
4. **Missing Statements**: Bank/loan statements required
5. **Year 1 Missing Settlement**: First year requires settlement statement

## Interest Deductibility Rules (NZ)

| Property Type | Tax Year | Deductibility |
|---------------|----------|---------------|
| New Build (CCC after 27 Mar 2020) | All years | 100% |
| Existing Property | FY24 (2023-24) | 50% |
| Existing Property | FY25 (2024-25) | 80% |
| Existing Property | FY26 (2025-26) | 100% |

## NZ Tax Business Rules

Key business rules implemented in the AI Brain:

| Rule | Description |
|------|-------------|
| **Interest** | Sum LOAN INTEREST debits only; exclude credits/adjustments/offsets |
| **Year 1 Rates** | Bank Paid + (Vendor Instalment − Vendor Credit) from settlement |
| **Legal Fees** | Under $10k = fully deductible (not capital) |
| **Body Corporate** | Operating fund only (reserve fund = capital, not deductible) |
| **PM Fees** | Always GST-inclusive (base + GST) |
| **Repairs >$800** | Must have matching invoice |
| **Accounting Fees** | Standard $862.50 always included |
| **Due Diligence** | LIM, meth test, valuations → Row 18 |
| **Depreciation** | Pro-rate by months if partial year ownership |

## Development

### Testing

```bash
# Run tests
poetry run pytest tests/ -v

# Run with coverage
poetry run pytest tests/ --cov=app --cov-report=html
```

### Docker Commands

```bash
# Build and start
docker-compose up --build

# Run in background
docker-compose up -d

# View logs
docker-compose logs -f app

# Stop and cleanup
docker-compose down -v
```

## Performance Metrics

- **Document Processing**: ~3-5 seconds per document
- **Transaction Processing**: ~100 transactions in 5-10 seconds
- **API Efficiency**: 95% reduction in API calls via batching
- **Accuracy**: >85% auto-categorization on first pass
- **Token Limit**: 16384 tokens (supports 100+ transactions)

## Security

- API keys stored in environment variables
- SQL injection prevention via ORM
- File upload validation and sandboxing
- CORS configured for production use
- Sensitive data excluded from logs

## License

This project is proprietary software. All rights reserved.

---

**Built for the NZ property investment community**

*Last updated: December 22, 2024*
*Version: 3.5.0 - AI Brain & Workings Generation*
