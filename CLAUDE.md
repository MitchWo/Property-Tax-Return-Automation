# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

NZ Property Tax Document Review System - An AI-powered FastAPI application that automatically classifies, analyzes, and validates tax documents for New Zealand rental property tax returns (IR3R) using Claude AI Vision.

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

## Architecture

### Core Flow

1. **Document Upload** (`app/api/routes.py`) → Web form or API endpoint receives files
2. **Document Processor** (`app/services/document_processor.py`) → Orchestrates the entire workflow:
   - Saves files via `FileHandler`
   - Sends to Claude via `ClaudeClient` for classification
   - Persists results to PostgreSQL
3. **Claude Client** (`app/services/claude_client.py`) → Handles Claude API calls with retry logic, vision processing for scanned documents
4. **Final Review** → Claude reviews all documents for completeness using `COMPLETENESS_REVIEW_PROMPT`

### Key Services

- **ClaudeClient**: Implements exponential backoff retry, handles both text and image (vision) content, parses JSON responses from Claude
- **FileHandler**: Processes PDFs (via pdf2image for scanned docs), Excel (.xlsx/.xls), CSV, images
- **KnowledgeStore**: RAG system using Pinecone vectors + OpenAI embeddings for semantic search of past feedback
- **EmbeddingsService** (`app/services/embeddings.py`): OpenAI text-embedding-3-small integration

### Database Models (`app/models/db_models.py`)

- **Client** → **TaxReturn** (1:N) → **Document** (1:N)
- Uses async SQLAlchemy with PostgreSQL (asyncpg driver)
- Statuses: `PENDING`, `COMPLETE`, `INCOMPLETE`, `BLOCKED`

### API Structure

- `/api/returns` - POST: Create tax return with documents, GET: List all
- `/api/returns/{id}` - GET: Single return
- `/api/returns/{id}/documents` - GET: Documents for a return
- `/api/feedback` - POST: Submit learning feedback to Pinecone
- `/api/learnings` - GET: Search stored learnings
- `/health` - Health check

Web routes serve Jinja2 templates at `/`, `/upload`, `/result/{id}`, `/feedback`, `/learnings`

## NZ Tax Domain Knowledge

### Document Types (15+)
`bank_statement`, `loan_statement`, `settlement_statement`, `depreciation_schedule`, `body_corporate`, `property_manager_statement`, `rates`, `landlord_insurance`, `healthy_homes`, `ccc` (Code Compliance Certificate), `smoke_alarm`, `meth_test`, `lim_report`, `other`, `invalid`

### Blocking Conditions (Critical)
- Wrong insurance type (home & contents vs landlord insurance)
- Missing bank/loan statements
- First-year purchase missing settlement statement
- New build missing CCC for 100% interest deductibility
- Property address mismatch on key documents

### Interest Deductibility Rules
- New builds (CCC after 27 March 2020): 100% deductible
- Existing properties: 80% deductible (2024/25 tax year)

## Environment Variables

Required:
- `ANTHROPIC_API_KEY` - Claude API key
- `DATABASE_URL` - PostgreSQL async connection string

Optional:
- `CLAUDE_MODEL` - Default: `claude-opus-4-5-20251101`
- `OPENAI_API_KEY` - For embeddings (semantic search)
- `PINECONE_API_KEY`, `PINECONE_INDEX_HOST` - For RAG knowledge store

## Key Implementation Details

- **Sequential document processing**: Documents are processed one at a time (not concurrent) to avoid rate limits and database session race conditions
- **Vision API**: Scanned PDFs are converted to images via pdf2image/poppler, then sent to Claude Vision
- **Image limits**: Max 5 pages per document, resized to 1568x1568 max
- **Retry logic**: Exponential backoff (1, 2, 4 seconds) for rate limits
- **Low temperature (0.1)**: Used for deterministic classification results
