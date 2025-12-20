"""Application configuration using pydantic-settings."""

from pathlib import Path
from typing import List

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # API Keys
    ANTHROPIC_API_KEY: str = Field(..., description="Anthropic API key")

    # Database
    DATABASE_URL: str = Field(
        default="postgresql+asyncpg://postgres:postgres@localhost:5432/property_tax",
        description="PostgreSQL connection URL",
    )

    # File Upload Settings
    UPLOAD_DIR: Path = Field(default=Path("./uploads"), description="Upload directory path")
    MAX_FILE_SIZE_MB: int = Field(default=50, description="Maximum file size in MB")

    # Claude Model
    # Available models (December 2025):
    # - claude-opus-4-5-20251101 (best accuracy, highest cost)
    # - claude-sonnet-4-5-20250929 (excellent balance)
    # - claude-sonnet-4-20250514 (fast, good accuracy)
    # - claude-haiku-4-5-20251001 (fastest, lowest cost)
    CLAUDE_MODEL: str = Field(
        default="claude-opus-4-5-20251101", description="Claude model to use for document analysis"
    )

    # Application Settings
    LOG_LEVEL: str = Field(default="INFO", description="Logging level")
    DEBUG: bool = Field(default=False, description="Debug mode")

    # Server Settings
    HOST: str = Field(default="0.0.0.0", description="Server host")
    PORT: int = Field(default=8000, description="Server port")

    # Processing Settings
    CHUNK_SIZE: int = Field(default=1000, description="Text chunk size")
    CHUNK_OVERLAP: int = Field(default=200, description="Chunk overlap size")
    EMBEDDING_DIMENSION: int = Field(default=1536, description="Embedding dimension")

    # Phase 1 Batch Processing Settings
    FINANCIAL_DOC_BATCH_SIZE: int = Field(
        default=5, description="Number of pages per batch for financial documents"
    )
    MAX_CONCURRENT_API_CALLS: int = Field(
        default=3, description="Maximum concurrent Claude API calls"
    )
    MIN_REQUEST_INTERVAL: float = Field(
        default=0.5, description="Minimum seconds between API requests"
    )
    BATCH_DELAY_SECONDS: float = Field(
        default=1.0, description="Delay between processing batches"
    )

    # Enhanced Retry Settings
    MAX_API_RETRIES: int = Field(default=5, description="Maximum API retry attempts")
    RETRY_BASE_DELAY: float = Field(default=2.0, description="Base delay for exponential backoff")
    RETRY_MAX_DELAY: float = Field(default=60.0, description="Maximum delay between retries")
    RETRY_JITTER: float = Field(default=1.0, description="Random jitter added to retry delay")

    # Feature Flags
    ENABLE_BATCH_PROCESSING: bool = Field(
        default=True, description="Enable batch processing for financial documents"
    )
    ENABLE_TOOL_USE: bool = Field(
        default=True, description="Enable Claude Tool Use for schema enforcement"
    )
    ENABLE_EXTRACTION_VERIFICATION: bool = Field(
        default=True, description="Enable verification pass for extractions"
    )

    # Allowed file extensions
    ALLOWED_EXTENSIONS: List[str] = Field(
        default=[".pdf", ".png", ".jpg", ".jpeg", ".xlsx", ".xls", ".csv"],
        description="Allowed file extensions",
    )

    # Allowed MIME types
    ALLOWED_MIME_TYPES: List[str] = Field(
        default=[
            "application/pdf",
            "image/png",
            "image/jpeg",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "application/vnd.ms-excel",
            "text/csv",
        ],
        description="Allowed MIME types",
    )

    # Pinecone Configuration (1536-dim OpenAI embeddings)
    PINECONE_API_KEY: str = Field(default="", description="Pinecone API key")
    PINECONE_INDEX_HOST: str = Field(
        default="",
        description="Pinecone index host URL (e.g., skill-learnings-xxxxx.svc.aped-4627-b74a.pinecone.io)",
    )
    PINECONE_NAMESPACE: str = Field(
        default="skill_learnings", description="Pinecone namespace for knowledge storage"
    )

    # Alias for backwards compatibility
    PINECONE_OPENAI_INDEX_HOST: str = Field(
        default="",
        description="Alias for PINECONE_INDEX_HOST (deprecated, use PINECONE_INDEX_HOST)"
    )
    PINECONE_OPENAI_NAMESPACE: str = Field(
        default="skill_learnings", description="Alias for PINECONE_NAMESPACE (deprecated)"
    )

    # Knowledge Retrieval
    KNOWLEDGE_TOP_K: int = Field(default=5, description="Number of relevant learnings to retrieve")

    # Google Maps API
    GOOGLE_MAPS_API_KEY: str = Field(
        default="",
        description="Google Maps API key for address autocomplete (requires Places API enabled)",
    )
    KNOWLEDGE_RELEVANCE_THRESHOLD: float = Field(
        default=0.3, description="Minimum relevance score for knowledge retrieval"
    )

    # OpenAI Configuration (for embeddings)
    OPENAI_API_KEY: str = Field(default="", description="OpenAI API key for embeddings")
    EMBEDDING_MODEL: str = Field(
        default="text-embedding-3-small", description="OpenAI embedding model to use"
    )
    EMBEDDING_DIMENSIONS: int = Field(
        default=1536, description="Embedding dimensions (1536 for OpenAI text-embedding-3-small)"
    )

    @field_validator("UPLOAD_DIR", mode="before")
    @classmethod
    def ensure_upload_dir(cls, v: str | Path) -> Path:
        """Ensure upload directory exists."""
        upload_dir = Path(v)
        upload_dir.mkdir(parents=True, exist_ok=True)
        return upload_dir

    @property
    def max_file_size_bytes(self) -> int:
        """Return max file size in bytes."""
        return self.MAX_FILE_SIZE_MB * 1024 * 1024

    @property
    def DATABASE_URL_SYNC(self) -> str:
        """Return synchronous database URL for Alembic."""
        # Replace asyncpg with psycopg2 for synchronous operations
        return self.DATABASE_URL.replace("postgresql+asyncpg", "postgresql")


# Create settings instance
settings = Settings()

# Ensure upload directory exists
settings.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
