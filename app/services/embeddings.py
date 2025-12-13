"""OpenAI embeddings service for semantic search."""

import logging
from typing import List, Optional

from openai import AsyncOpenAI

from app.config import settings

logger = logging.getLogger(__name__)


class EmbeddingsService:
    """Generate embeddings using OpenAI."""

    def __init__(self):
        """Initialize embeddings service."""
        self.api_key = settings.OPENAI_API_KEY
        self.model = settings.EMBEDDING_MODEL
        self.dimensions = settings.EMBEDDING_DIMENSIONS

        if not self.api_key:
            logger.warning("OpenAI API key not configured - embeddings disabled")
            self.enabled = False
            self.client = None
        else:
            self.enabled = True
            self.client = AsyncOpenAI(api_key=self.api_key)

    async def embed_text(self, text: str) -> Optional[List[float]]:
        """
        Generate embedding for a single text.

        Args:
            text: Text to embed

        Returns:
            List of floats representing the embedding, or None if failed
        """
        if not self.enabled:
            logger.warning("Embeddings disabled - returning None")
            return None

        try:
            # Clean and truncate text if needed (max ~8000 tokens for embedding models)
            text = text.strip()
            if len(text) > 30000:  # Rough character limit
                text = text[:30000]

            response = await self.client.embeddings.create(
                model=self.model, input=text, dimensions=self.dimensions
            )

            embedding = response.data[0].embedding
            logger.debug(f"Generated embedding with {len(embedding)} dimensions")
            return embedding

        except Exception as e:
            logger.error(f"Error generating embedding: {e}")
            return None

    async def embed_batch(self, texts: List[str]) -> List[Optional[List[float]]]:
        """
        Generate embeddings for multiple texts.

        Args:
            texts: List of texts to embed

        Returns:
            List of embeddings (or None for failed items)
        """
        if not self.enabled:
            return [None] * len(texts)

        try:
            # Clean texts
            cleaned = [t.strip()[:30000] for t in texts]

            response = await self.client.embeddings.create(
                model=self.model, input=cleaned, dimensions=self.dimensions
            )

            # Sort by index to maintain order
            embeddings = [None] * len(texts)
            for item in response.data:
                embeddings[item.index] = item.embedding

            logger.debug(f"Generated {len(embeddings)} embeddings")
            return embeddings

        except Exception as e:
            logger.error(f"Error generating batch embeddings: {e}")
            return [None] * len(texts)


# Singleton instance
embeddings_service = EmbeddingsService()
