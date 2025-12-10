"""Tests for document processor."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.schemas.documents import DocumentClassification, TaxReturnCreate, PropertyType
from app.services.document_processor import DocumentProcessor


@pytest.fixture
def document_processor():
    """Create document processor instance."""
    return DocumentProcessor()


@pytest.fixture
def mock_tax_return_data():
    """Create mock tax return data."""
    return TaxReturnCreate(
        client_name="Test Client",
        property_address="123 Test St, Auckland",
        tax_year="FY25",
        property_type=PropertyType.NEW_BUILD,
        gst_registered=False,
        year_of_ownership=1
    )


@pytest.mark.asyncio
async def test_process_single_document(document_processor):
    """Test processing a single document."""
    # Mock dependencies
    with patch.object(document_processor.file_handler, 'save_upload') as mock_save:
        with patch.object(document_processor.file_handler, 'process_file') as mock_process:
            with patch.object(document_processor.claude_client, 'analyze_document') as mock_analyze:
                # Setup mocks
                mock_save.return_value = ("stored.pdf", "/path/to/file.pdf")
                mock_process.return_value = MagicMock(
                    text_content="Document text",
                    image_paths=None
                )
                mock_analyze.return_value = DocumentClassification(
                    document_type="bank_statement",
                    confidence=0.95,
                    reasoning="This is a bank statement",
                    flags=[],
                    key_details={"account_number": "12345"}
                )

                # Create mock objects
                mock_db = AsyncMock()
                mock_tax_return = MagicMock(id="tax-return-id")
                mock_file = MagicMock(
                    filename="test.pdf",
                    content_type="application/pdf",
                    size=1000
                )
                mock_tax_return_data = MagicMock(
                    property_address="123 Test St",
                    tax_year="FY25",
                    property_type=MagicMock(value="new_build")
                )

                # Test
                analysis, summary = await document_processor._process_single_document(
                    mock_db, mock_tax_return, mock_file, mock_tax_return_data
                )

                # Assertions
                assert analysis.filename == "test.pdf"
                assert analysis.classification.document_type == "bank_statement"
                assert summary.document_type == "bank_statement"


@pytest.mark.asyncio
async def test_get_or_create_client_existing(document_processor):
    """Test getting existing client."""
    mock_db = AsyncMock()
    mock_client = MagicMock(id="client-id", name="Test Client")

    # Mock database query
    mock_result = AsyncMock()
    mock_result.scalar_one_or_none.return_value = mock_client
    mock_db.execute.return_value = mock_result

    # Test
    with patch('app.services.document_processor.select'):
        client = await document_processor._get_or_create_client(
            mock_db, "Test Client"
        )

    # Assertions
    assert client.id == "client-id"
    assert client.name == "Test Client"


@pytest.mark.asyncio
async def test_get_or_create_client_new(document_processor):
    """Test creating new client."""
    mock_db = AsyncMock()

    # Mock database query - no existing client
    mock_result = AsyncMock()
    mock_result.scalar_one_or_none.return_value = None
    mock_db.execute.return_value = mock_result

    # Test
    with patch('app.services.document_processor.select'):
        with patch('app.services.document_processor.Client') as MockClient:
            mock_new_client = MagicMock(id="new-client-id", name="New Client")
            MockClient.return_value = mock_new_client

            client = await document_processor._get_or_create_client(
                mock_db, "New Client"
            )

            # Assertions
            mock_db.add.assert_called_once_with(mock_new_client)
            mock_db.flush.assert_called_once()
            assert client.name == "New Client"