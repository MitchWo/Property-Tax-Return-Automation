# NZ Property Tax Document Review System

Phase 1 implementation of a document review system for NZ rental property tax returns. This system handles document intake, classification, and data extraction using Claude AI vision capabilities.

## Features

- **Document Classification**: Automatically identifies 15+ document types relevant to NZ property tax
- **Data Extraction**: Extracts key information from each document using Claude vision
- **Completeness Analysis**: Identifies missing documents and blocking issues
- **Multi-format Support**: Handles PDF (digital & scanned), images (PNG/JPG), and spreadsheets (Excel/CSV)
- **Async Processing**: Processes multiple documents concurrently for efficiency
- **Web Interface**: Simple drag-and-drop interface with HTMX for interactivity

## Tech Stack

- **Python 3.12+**
- **FastAPI** - Web framework
- **Anthropic Claude Opus 4.5** - Advanced document analysis with vision
- **PostgreSQL** - Data persistence
- **SQLAlchemy 2.0** - Async ORM
- **Alembic** - Database migrations
- **Jinja2 + HTMX** - Web UI
- **Tailwind CSS** - Styling (via CDN)
- **Docker & Docker Compose** - Containerization

## Prerequisites for Deployment

### Docker Installation (Required for Production Deployment)

Docker is essential for containerized deployment. Follow these steps to install Docker:

#### macOS Installation:
1. **Download Docker Desktop** from https://www.docker.com/products/docker-desktop/
   - Choose the correct version for your Mac:
     - Apple Silicon (M1/M2/M3): Docker Desktop for Mac with Apple silicon
     - Intel Macs: Docker Desktop for Mac with Intel chip
2. **Install Docker Desktop**:
   - Open the downloaded `.dmg` file
   - Drag Docker to your Applications folder
   - Launch Docker from Applications
   - Follow the setup wizard
3. **Verify Installation**:
   ```bash
   docker --version
   docker compose version
   ```

#### Windows Installation:
1. **System Requirements**: Windows 10/11 64-bit with WSL 2
2. **Download Docker Desktop** from https://www.docker.com/products/docker-desktop/
3. **Run the installer** and follow the setup wizard
4. **Enable WSL 2** if prompted during installation
5. **Verify Installation** in PowerShell or Command Prompt:
   ```bash
   docker --version
   docker compose version
   ```

#### Linux Installation:
```bash
# Ubuntu/Debian
sudo apt update
sudo apt install docker.io docker-compose-v2
sudo systemctl start docker
sudo systemctl enable docker
sudo usermod -aG docker $USER

# Fedora/RHEL
sudo dnf install docker docker-compose-v2
sudo systemctl start docker
sudo systemctl enable docker
sudo usermod -aG docker $USER

# Log out and back in for group changes to take effect
```

## Installation

### Using Docker (Recommended for Production)

1. **Ensure Docker is installed** (see Prerequisites above)

2. **Clone the repository**:
```bash
git clone <repository-url>
cd property-tax-agent
```

3. **Configure environment variables**:
```bash
cp .env.example .env
# Edit .env and add your ANTHROPIC_API_KEY
# IMPORTANT: Replace 'your_anthropic_api_key_here' with your actual API key
nano .env  # or use any text editor
```

4. **Start the application**:
```bash
# For newer Docker versions (20.10+)
docker compose up --build

# For older Docker versions
docker-compose up --build
```

5. **Access the application**:
   - Open your browser and navigate to `http://localhost:8000`
   - The database will be automatically initialized on first run

6. **Stopping the application**:
```bash
# Press Ctrl+C in the terminal, then:
docker compose down  # or docker-compose down
```

### Docker Deployment Tips

- **Production deployment**: Use `docker compose up -d` to run in detached mode
- **View logs**: `docker compose logs -f app`
- **Rebuild after code changes**: `docker compose up --build`
- **Clean up volumes**: `docker compose down -v` (WARNING: This deletes the database)
- **Update dependencies**: Rebuild with `docker compose build --no-cache`

### Local Development

1. Install Python 3.12+

2. Install Poetry:
```bash
pip install poetry
```

3. Install dependencies:
```bash
poetry install
```

4. Set up PostgreSQL:
```bash
# Create database
createdb property_tax
```

5. Run migrations:
```bash
poetry run alembic upgrade head
```

6. Start the application:
```bash
poetry run uvicorn app.main:app --reload
```

## Usage

### Web Interface

1. Navigate to `http://localhost:8000`
2. Fill in client and property details
3. Drag and drop or select documents to upload
4. Click "Process Documents"
5. View the analysis results

### API Endpoints

#### Create Tax Return
```bash
POST /api/returns
Content-Type: multipart/form-data

Fields:
- client_name: string
- property_address: string
- tax_year: string (FY24|FY25|FY26)
- property_type: string (new_build|existing)
- gst_registered: boolean
- year_of_ownership: integer
- files: multiple file uploads
```

#### Get Tax Return
```bash
GET /api/returns/{tax_return_id}
```

#### List Tax Returns
```bash
GET /api/returns?skip=0&limit=100
```

#### Get Documents for Tax Return
```bash
GET /api/returns/{tax_return_id}/documents
```

## Document Types Supported

The system recognizes these document types:

- **bank_statement** - Bank account statements showing rental transactions
- **loan_statement** - Mortgage/loan statements showing interest
- **settlement_statement** - Property purchase settlement
- **depreciation_schedule** - Valuit/FordBaker depreciation reports
- **body_corporate** - Body corporate levies/invoices
- **property_manager_statement** - PM statements with rent/fees
- **lim_report** - Land Information Memorandum
- **healthy_homes** - Healthy homes inspection reports
- **meth_test** - Methamphetamine testing results
- **smoke_alarm** - Smoke alarm compliance certificates
- **ccc** - Code Compliance Certificates
- **landlord_insurance** - Landlord/rental property insurance
- **rates** - Council rates notices
- **other** - Valid but uncategorized documents
- **invalid** - Not relevant to tax returns

## Blocking Conditions

The system will mark a return as BLOCKED if it detects:

1. Home and contents insurance instead of landlord insurance
2. Wrong property address on key documents
3. Personal bank statements instead of rental property account
4. Documents for wrong tax year
5. Settlement statement outside tax year for new builds

## Project Structure

```
property-tax-agent/
├── app/
│   ├── api/           # API routes
│   ├── models/        # SQLAlchemy models
│   ├── schemas/       # Pydantic schemas
│   ├── services/      # Business logic
│   ├── templates/     # Jinja2 templates
│   ├── config.py      # Configuration
│   ├── database.py    # Database setup
│   └── main.py        # FastAPI app
├── migrations/        # Alembic migrations
├── tests/            # Test suite
├── uploads/          # Document storage
├── docker-compose.yml
├── Dockerfile
├── pyproject.toml    # Dependencies
└── README.md
```

## Testing

Run tests with pytest:
```bash
poetry run pytest tests/ -v
```

With coverage:
```bash
poetry run pytest tests/ --cov=app --cov-report=html
```

## Environment Variables

See `.env.example` for all available configuration options:

- `ANTHROPIC_API_KEY` - Required for Claude AI
- `DATABASE_URL` - PostgreSQL connection string
- `UPLOAD_DIR` - Directory for uploaded files
- `MAX_FILE_SIZE_MB` - Maximum file upload size
- `CLAUDE_MODEL` - Claude model to use

## Performance Considerations

- Documents are processed concurrently (max 5 at a time)
- Large PDFs are converted to images at 300 DPI for optimal OCR
- Claude API timeout is set to 120 seconds for large documents
- Database uses connection pooling for efficiency

## Limitations

- Phase 1 focuses on document intake and classification only
- No RAG or learning features (planned for Phase 2)
- Maximum file size: 50MB per document
- Maximum 100 documents per submission

## Security Notes

- Never commit `.env` file with API keys
- Use environment variables for sensitive configuration
- Files are stored locally in the uploads directory
- Implement proper authentication before production use

## Future Enhancements (Phase 2)

- RAG system for improved accuracy
- Self-learning from corrections
- Batch processing improvements
- Document versioning
- Audit trail
- Export to accounting software
- Multi-tenant support

## License

[Your License]

## Support

For issues or questions, please create an issue in the repository.