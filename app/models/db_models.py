"""SQLAlchemy database models."""

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import relationship

from app.database import Base


def local_now():
    """Return current time in local timezone."""
    return datetime.now().astimezone()


class PropertyType(str, enum.Enum):
    """Property type enumeration."""

    NEW_BUILD = "new_build"
    EXISTING = "existing"
    NOT_SURE = "not_sure"


class TaxReturnStatus(str, enum.Enum):
    """Tax return status enumeration."""

    PENDING = "pending"
    COMPLETE = "complete"
    INCOMPLETE = "incomplete"
    BLOCKED = "blocked"


class DocumentStatus(str, enum.Enum):
    """Document status enumeration."""

    PENDING = "pending"
    CLASSIFIED = "classified"
    VERIFIED = "verified"
    ERROR = "error"


class Client(Base):
    """Client model."""

    __tablename__ = "clients"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(255), nullable=False)
    created_at = Column(DateTime(timezone=True), default=local_now, nullable=False)

    # Relationships
    tax_returns = relationship("TaxReturn", back_populates="client", cascade="all, delete-orphan")


class TaxReturn(Base):
    """Tax return model."""

    __tablename__ = "tax_returns"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    client_id = Column(UUID(as_uuid=True), ForeignKey("clients.id"), nullable=False)
    property_address = Column(Text, nullable=False)
    tax_year = Column(String(10), nullable=False)  # e.g., "FY25"
    property_type = Column(Enum(PropertyType), nullable=False)
    gst_registered = Column(Boolean, default=None, nullable=True)  # None = user wants AI suggestion
    year_of_ownership = Column(Integer, nullable=False)
    status = Column(Enum(TaxReturnStatus), default=TaxReturnStatus.PENDING, nullable=False)
    review_result = Column(JSONB, nullable=True)  # Stores full analysis
    created_at = Column(DateTime(timezone=True), default=local_now, nullable=False)
    updated_at = Column(
        DateTime(timezone=True), default=local_now, onupdate=local_now, nullable=False
    )

    # Relationships
    client = relationship("Client", back_populates="tax_returns")
    documents = relationship("Document", back_populates="tax_return", cascade="all, delete-orphan")


class Document(Base):
    """Document model."""

    __tablename__ = "documents"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tax_return_id = Column(UUID(as_uuid=True), ForeignKey("tax_returns.id"), nullable=False)
    original_filename = Column(String(255), nullable=False)
    stored_filename = Column(String(255), nullable=False)
    file_path = Column(Text, nullable=False)
    mime_type = Column(String(100), nullable=False)
    file_size = Column(Integer, nullable=False)
    content_hash = Column(String(64), nullable=True)  # SHA-256 hash for duplicate detection
    is_duplicate = Column(Boolean, default=False, nullable=False)  # Flag for duplicates
    duplicate_of_id = Column(
        UUID(as_uuid=True), ForeignKey("documents.id"), nullable=True
    )  # Reference to original
    document_type = Column(String(50), nullable=True)  # Set after classification
    classification_confidence = Column(Float, nullable=True)
    extracted_data = Column(JSONB, nullable=True)
    status = Column(Enum(DocumentStatus), default=DocumentStatus.PENDING, nullable=False)
    created_at = Column(DateTime(timezone=True), default=local_now, nullable=False)

    # Relationships
    tax_return = relationship("TaxReturn", back_populates="documents")
    duplicate_of = relationship("Document", remote_side=[id], foreign_keys=[duplicate_of_id])


# Database Indexes
Index("ix_clients_name", Client.name)
Index("ix_tax_returns_client_id", TaxReturn.client_id)
Index("ix_tax_returns_status", TaxReturn.status)
Index("ix_tax_returns_tax_year", TaxReturn.tax_year)
Index("ix_tax_returns_created_at", TaxReturn.created_at.desc())
Index("ix_documents_tax_return_id", Document.tax_return_id)
Index("ix_documents_document_type", Document.document_type)
Index("ix_documents_status", Document.status)
Index("ix_documents_content_hash", Document.content_hash)
