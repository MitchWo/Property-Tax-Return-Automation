"""OpenAI embeddings service for semantic search."""

import asyncio
import hashlib
import logging
import time
from collections import OrderedDict
from typing import Dict, List, Optional, Tuple

from openai import AsyncOpenAI

from app.config import settings

logger = logging.getLogger(__name__)


class EmbeddingCache:
    """LRU cache for embeddings with TTL support."""

    def __init__(self, max_size: int = 500, ttl_seconds: int = 3600):
        self.max_size = max_size
        self.ttl_seconds = ttl_seconds
        self._cache: OrderedDict[str, Tuple[List[float], float]] = OrderedDict()
        self._lock = asyncio.Lock()

    def _hash_text(self, text: str) -> str:
        """Create a hash key for the text."""
        return hashlib.md5(text.encode()).hexdigest()

    async def get(self, text: str) -> Optional[List[float]]:
        """Get cached embedding if exists and not expired."""
        key = self._hash_text(text)
        async with self._lock:
            if key in self._cache:
                embedding, timestamp = self._cache[key]
                if time.time() - timestamp < self.ttl_seconds:
                    # Move to end (most recently used)
                    self._cache.move_to_end(key)
                    return embedding
                else:
                    # Expired, remove it
                    del self._cache[key]
        return None

    async def set(self, text: str, embedding: List[float]) -> None:
        """Cache an embedding."""
        key = self._hash_text(text)
        async with self._lock:
            # Remove oldest if at capacity
            while len(self._cache) >= self.max_size:
                self._cache.popitem(last=False)
            self._cache[key] = (embedding, time.time())

    def stats(self) -> Dict[str, int]:
        """Return cache statistics."""
        return {
            "size": len(self._cache),
            "max_size": self.max_size
        }


class EmbeddingsService:
    """Generate embeddings using OpenAI with caching."""

    def __init__(self):
        """Initialize embeddings service."""
        self.api_key = settings.OPENAI_API_KEY
        self.model = settings.EMBEDDING_MODEL
        self.dimensions = settings.EMBEDDING_DIMENSIONS
        self._cache = EmbeddingCache(max_size=500, ttl_seconds=3600)
        self._cache_hits = 0
        self._cache_misses = 0

        if not self.api_key:
            logger.warning("OpenAI API key not configured - embeddings disabled")
            self.enabled = False
            self.client = None
        else:
            self.enabled = True
            self.client = AsyncOpenAI(api_key=self.api_key)

    async def embed_text(self, text: str, use_cache: bool = True) -> Optional[List[float]]:
        """
        Generate embedding for a single text with caching.

        Args:
            text: Text to embed
            use_cache: Whether to use cache (default True)

        Returns:
            List of floats representing the embedding, or None if failed
        """
        if not self.enabled:
            logger.warning("Embeddings disabled - returning None")
            return None

        # Clean and truncate text if needed (max ~8000 tokens for embedding models)
        text = text.strip()
        if len(text) > 30000:  # Rough character limit
            text = text[:30000]

        # Check cache first
        if use_cache:
            cached = await self._cache.get(text)
            if cached is not None:
                self._cache_hits += 1
                logger.debug(f"Embedding cache hit (hits: {self._cache_hits}, misses: {self._cache_misses})")
                return cached

        try:
            self._cache_misses += 1
            response = await self.client.embeddings.create(
                model=self.model, input=text, dimensions=self.dimensions
            )

            embedding = response.data[0].embedding
            logger.debug(f"Generated embedding with {len(embedding)} dimensions")

            # Cache the result
            if use_cache:
                await self._cache.set(text, embedding)

            return embedding

        except Exception as e:
            logger.error(f"Error generating embedding: {e}")
            return None

    def cache_stats(self) -> Dict[str, int]:
        """Return cache statistics."""
        stats = self._cache.stats()
        stats["hits"] = self._cache_hits
        stats["misses"] = self._cache_misses
        hit_rate = self._cache_hits / max(1, self._cache_hits + self._cache_misses) * 100
        stats["hit_rate_percent"] = round(hit_rate, 1)
        return stats

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
