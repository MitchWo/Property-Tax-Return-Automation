"""Application configuration using pydantic-settings."""
from pathlib import Path
from typing import List, Optional

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
        description="PostgreSQL connection URL"
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
        default="claude-opus-4-5-20251101",
        description="Claude model to use for document analysis"
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

    # Allowed file extensions
    ALLOWED_EXTENSIONS: List[str] = Field(
        default=[".pdf", ".png", ".jpg", ".jpeg", ".xlsx", ".xls", ".csv"],
        description="Allowed file extensions"
    )

    # Allowed MIME types
    ALLOWED_MIME_TYPES: List[str] = Field(
        default=[
            "application/pdf",
            "image/png",
            "image/jpeg",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "application/vnd.ms-excel",
            "text/csv"
        ],
        description="Allowed MIME types"
    )

    # Pinecone Configuration
    PINECONE_API_KEY: str = Field(default="", description="Pinecone API key")
    PINECONE_INDEX_HOST: str = Field(
        default="",
        description="Pinecone index host URL (e.g., phase1-feedback-xxxxx.svc.aped-4627-b74a.pinecone.io)"
    )
    PINECONE_NAMESPACE: str = Field(
        default="document-review",
        description="Pinecone namespace for knowledge storage"
    )

    # Knowledge Retrieval
    KNOWLEDGE_TOP_K: int = Field(
        default=5,
        description="Number of relevant learnings to retrieve"
    )
    KNOWLEDGE_RELEVANCE_THRESHOLD: float = Field(
        default=0.3,
        description="Minimum relevance score for knowledge retrieval"
    )

    # OpenAI Configuration (for embeddings)
    OPENAI_API_KEY: str = Field(default="", description="OpenAI API key for embeddings")
    EMBEDDING_MODEL: str = Field(
        default="text-embedding-3-small",
        description="OpenAI embedding model to use"
    )
    EMBEDDING_DIMENSIONS: int = Field(
        default=1024,
        description="Embedding dimensions (must match Pinecone index)"
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