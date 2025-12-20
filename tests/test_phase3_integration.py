"""Integration tests for Phase 2 Transaction Processing & Learning.

Note: File kept as test_phase3_integration.py for backwards compatibility.
"""
import asyncio
import pytest
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker

from app.config import settings
from app.database import Base
from app.models.db_models import (
    Client, TaxReturn, Document, Transaction, TransactionSummary,
    TransactionPattern, CategoryFeedback, TaxRule, PLRowMapping,
    PropertyType, TaxReturnStatus, DocumentStatus
)


# =============================================================================
# FIXTURES
# =============================================================================

@pytest.fixture(scope="session")
def event_loop():
    """Create event loop for async tests."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="session")
async def engine():
    """Create test database engine."""
    # Use the same database but in a transaction we'll rollback
    engine = create_async_engine(settings.DATABASE_URL, echo=False)
    yield engine
    await engine.dispose()


@pytest.fixture
async def db_session(engine):
    """Create a test database session with rollback."""
    async_session = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )

    async with async_session() as session:
        yield session
        await session.rollback()


@pytest.fixture
async def sample_client(db_session: AsyncSession):
    """Create a sample client."""
    client = Client(name="Test Client Ltd")
    db_session.add(client)
    await db_session.commit()
    await db_session.refresh(client)
    return client


@pytest.fixture
async def sample_tax_return(db_session: AsyncSession, sample_client: Client):
    """Create a sample tax return."""
    tax_return = TaxReturn(
        client_id=sample_client.id,
        property_address="123 Test Street, Auckland 1010",
        tax_year="FY25",
        property_type=PropertyType.EXISTING,
        gst_registered=False,
        year_of_ownership=2,
        status=TaxReturnStatus.PENDING
    )
    db_session.add(tax_return)
    await db_session.commit()
    await db_session.refresh(tax_return)
    return tax_return


@pytest.fixture
async def sample_document(db_session: AsyncSession, sample_tax_return: TaxReturn):
    """Create a sample document."""
    document = Document(
        tax_return_id=sample_tax_return.id,
        original_filename="test_bank_statement.csv",
        stored_filename=f"{uuid4()}_test_bank_statement.csv",
        file_path="/tmp/test_bank_statement.csv",
        mime_type="text/csv",
        file_size=1024,
        document_type="bank_statement",
        status=DocumentStatus.CLASSIFIED
    )
    db_session.add(document)
    await db_session.commit()
    await db_session.refresh(document)
    return document


# =============================================================================
# DATABASE MODEL TESTS
# =============================================================================

class TestDatabaseModels:
    """Test database models exist and work correctly."""

    @pytest.mark.asyncio
    async def test_client_creation(self, db_session: AsyncSession):
        """Test client model creation."""
        client = Client(name="New Test Client")
        db_session.add(client)
        await db_session.commit()

        assert client.id is not None
        assert client.name == "New Test Client"
        assert client.created_at is not None

    @pytest.mark.asyncio
    async def test_tax_return_creation(self, db_session: AsyncSession, sample_client: Client):
        """Test tax return model creation."""
        tax_return = TaxReturn(
            client_id=sample_client.id,
            property_address="456 Test Ave",
            tax_year="FY25",
            property_type=PropertyType.NEW_BUILD,
            gst_registered=True,
            year_of_ownership=1,
            status=TaxReturnStatus.PENDING
        )
        db_session.add(tax_return)
        await db_session.commit()

        assert tax_return.id is not None
        assert tax_return.property_type == PropertyType.NEW_BUILD
        assert tax_return.gst_registered is True

    @pytest.mark.asyncio
    async def test_transaction_creation(self, db_session: AsyncSession, sample_tax_return: TaxReturn, sample_document: Document):
        """Test transaction model creation."""
        transaction = Transaction(
            tax_return_id=sample_tax_return.id,
            document_id=sample_document.id,
            transaction_date=date(2024, 6, 15),
            description="LOAN INTEREST CHARGED",
            amount=Decimal("-523.45"),
            category_code="interest",
            is_deductible=True,
            deductible_percentage=Decimal("80.00"),
            confidence=Decimal("0.95"),
            categorization_source="yaml_pattern",
            needs_review=False
        )
        db_session.add(transaction)
        await db_session.commit()

        assert transaction.id is not None
        assert transaction.amount == Decimal("-523.45")
        assert transaction.deductible_percentage == Decimal("80.00")

    @pytest.mark.asyncio
    async def test_transaction_summary_creation(self, db_session: AsyncSession, sample_tax_return: TaxReturn):
        """Test transaction summary model creation."""
        summary = TransactionSummary(
            tax_return_id=sample_tax_return.id,
            category_code="interest",
            transaction_count=24,
            gross_amount=Decimal("12500.00"),
            deductible_amount=Decimal("10000.00"),
            gst_amount=Decimal("0.00")
        )
        db_session.add(summary)
        await db_session.commit()

        assert summary.id is not None
        assert summary.transaction_count == 24

    @pytest.mark.asyncio
    async def test_tax_rule_exists(self, db_session: AsyncSession):
        """Test tax rules are seeded."""
        result = await db_session.execute(
            select(TaxRule).where(TaxRule.rule_type == "interest_deductibility")
        )
        rules = result.scalars().all()

        # Should have rules for different years
        assert len(rules) > 0

    @pytest.mark.asyncio
    async def test_pl_mappings_exist(self, db_session: AsyncSession):
        """Test P&L mappings are seeded."""
        result = await db_session.execute(select(PLRowMapping))
        mappings = result.scalars().all()

        # Should have all our standard mappings
        assert len(mappings) >= 30

        # Check specific mapping
        interest_mapping = next((m for m in mappings if m.category_code == "interest"), None)
        assert interest_mapping is not None
        assert interest_mapping.pl_row == 26


# =============================================================================
# YAML RULES TESTS
# =============================================================================

class TestYAMLRules:
    """Test YAML categorization rules loading and matching."""

    def test_load_categorization_rules(self):
        """Test loading categorization rules."""
        from app.rules.loader import load_categorization_rules

        rules = load_categorization_rules()

        assert rules is not None
        assert "payees" in rules
        assert "patterns" in rules
        assert "keywords" in rules

    def test_load_bank_parsers(self):
        """Test loading bank parser configs."""
        from app.rules.loader import load_bank_parsers

        parsers = load_bank_parsers()

        assert parsers is not None
        assert "asb" in parsers
        assert "anz" in parsers
        assert "kiwibank" in parsers

    def test_pattern_matcher_exact_payee(self):
        """Test exact payee matching."""
        from app.rules.loader import PatternMatcher

        matcher = PatternMatcher()

        # Test Auckland Council match
        result = matcher.match("AUCKLAND COUNCIL", other_party="Auckland Council")

        assert result is not None
        assert result["category"] == "rates"
        assert result["confidence"] >= 0.95

    def test_pattern_matcher_regex(self):
        """Test regex pattern matching."""
        from app.rules.loader import PatternMatcher

        matcher = PatternMatcher()

        # Test interest pattern
        result = matcher.match("LOAN INTEREST CHARGED", amount=-500.00)

        assert result is not None
        assert result["category"] == "interest"

    def test_pattern_matcher_no_match(self):
        """Test when no pattern matches."""
        from app.rules.loader import PatternMatcher

        matcher = PatternMatcher()

        result = matcher.match("RANDOM UNKNOWN TRANSACTION")

        # Should return None or low confidence
        assert result is None or result["confidence"] < 0.5


# =============================================================================
# TAX RULES SERVICE TESTS
# =============================================================================

class TestTaxRulesService:
    """Test tax rules service."""

    @pytest.mark.asyncio
    async def test_get_interest_deductibility_existing_fy25(self, db_session: AsyncSession):
        """Test interest deductibility for existing property FY25."""
        from app.services.tax_rules_service import get_tax_rules_service

        service = get_tax_rules_service()
        rate = await service.get_interest_deductibility(db_session, "FY25", "existing")

        assert rate == 80.0  # 80% for existing in FY25

    @pytest.mark.asyncio
    async def test_get_interest_deductibility_new_build(self, db_session: AsyncSession):
        """Test interest deductibility for new build."""
        from app.services.tax_rules_service import get_tax_rules_service

        service = get_tax_rules_service()
        rate = await service.get_interest_deductibility(db_session, "FY25", "new_build")

        assert rate == 100.0  # 100% for new builds

    @pytest.mark.asyncio
    async def test_get_accounting_fee(self, db_session: AsyncSession):
        """Test getting standard accounting fee."""
        from app.services.tax_rules_service import get_tax_rules_service

        service = get_tax_rules_service()
        fee = await service.get_accounting_fee(db_session)

        assert fee == Decimal("862.50")

# =============================================================================
# API ENDPOINT TESTS
# =============================================================================

class TestAPIEndpoints:
    """Test API endpoints."""

    @pytest.mark.asyncio
    async def test_health_check(self):
        """Test health check endpoint."""
        from fastapi.testclient import TestClient
        from app.main import app

        client = TestClient(app)
        response = client.get("/health")

        assert response.status_code == 200
        assert response.json()["status"] == "healthy"

# =============================================================================
# RUN TESTS
# =============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])