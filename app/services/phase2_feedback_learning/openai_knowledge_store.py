"""Knowledge store service for OpenAI embeddings (1536 dimensions).

Uses the skill-learnings Pinecone index for storing and retrieving learnings.
"""

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

import httpx

from app.config import settings
from app.models.db_models import local_now
from app.services.phase2_feedback_learning.embeddings import embeddings_service

logger = logging.getLogger(__name__)


class OpenAIKnowledgeStore:
    """Store and retrieve learnings from Pinecone using OpenAI embeddings (1536 dims)."""

    def __init__(self):
        """Initialize OpenAI knowledge store with skill-learnings index."""
        self.api_key = settings.PINECONE_API_KEY
        # Use PINECONE_INDEX_HOST (skill-learnings) as primary, PINECONE_OPENAI_INDEX_HOST as fallback
        self.index_host = settings.PINECONE_INDEX_HOST or settings.PINECONE_OPENAI_INDEX_HOST
        self.namespace = settings.PINECONE_NAMESPACE
        self.api_version = "2024-10"

        # Check if properly configured
        if not self.api_key:
            logger.warning("Pinecone API key not configured - OpenAI knowledge store disabled")
            self.enabled = False
        elif not self.index_host:
            logger.warning("PINECONE_INDEX_HOST not set - knowledge store disabled")
            self.enabled = False
        else:
            self.enabled = True
            logger.info(f"OpenAI knowledge store initialized with index: {self.index_host}")

        # Verify OpenAI embeddings are available
        if not embeddings_service.enabled:
            logger.warning("OpenAI API not configured - knowledge store will use fallback random vectors")

    async def search(
        self, query: str, top_k: int = None, min_score: float = None
    ) -> List[Dict[str, Any]]:
        """
        Search for relevant learnings using OpenAI embeddings.

        Args:
            query: Search query text
            top_k: Number of results to return
            min_score: Minimum relevance score threshold

        Returns:
            List of relevant learnings with scores
        """
        if not self.enabled:
            return []

        top_k = top_k or settings.KNOWLEDGE_TOP_K
        min_score = min_score or settings.KNOWLEDGE_RELEVANCE_THRESHOLD

        try:
            # Generate query embedding with OpenAI
            query_vector = await embeddings_service.embed_text(query)
            if not query_vector:
                logger.warning("Failed to generate OpenAI embedding for query")
                return []

            # Verify dimensions
            if len(query_vector) != 1536:
                logger.error(
                    f"OpenAI embedding has wrong dimensions: {len(query_vector)} (expected 1536)"
                )
                return []

            url = f"https://{self.index_host}/query"

            payload = {
                "namespace": self.namespace,
                "topK": top_k,
                "vector": query_vector,
                "includeMetadata": True,
            }

            headers = {
                "Api-Key": self.api_key,
                "Content-Type": "application/json",
                "X-Pinecone-API-Version": self.api_version,
            }

            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(url, json=payload, headers=headers)
                response.raise_for_status()

            result = response.json()
            matches = result.get("matches", [])

            # Filter by minimum score and format results
            learnings = []
            for match in matches:
                score = match.get("score", 0)
                metadata = match.get("metadata", {})
                if score >= min_score:
                    learnings.append(
                        {
                            "id": match.get("id"),
                            "score": score,
                            "content": metadata.get("content", ""),
                            "scenario": metadata.get("scenario", ""),
                            "category": metadata.get("category", ""),
                            "created_at": metadata.get("created_at", ""),
                            "metadata": metadata,  # Include full metadata
                        }
                    )

            logger.info(f"OpenAI knowledge search returned {len(learnings)} relevant results")
            return learnings

        except httpx.HTTPStatusError as e:
            if e.response.status_code == 400:
                logger.error(
                    f"Pinecone 400 error - likely dimension mismatch. "
                    f"Ensure PINECONE_INDEX_HOST points to a 1536-dimension index (skill-learnings). "
                    f"Error: {e.response.text}"
                )
            else:
                logger.error(f"Pinecone API error: {e}")
            return []
        except Exception as e:
            logger.error(f"Error searching OpenAI knowledge base: {e}")
            return []

    async def store(
        self,
        content: str,
        metadata: Dict[str, Any],
        vector_id: Optional[str] = None
    ) -> Optional[str]:
        """
        Store a new learning with OpenAI embedding.

        Args:
            content: The learning content to embed
            metadata: Metadata to store with the vector
            vector_id: Optional custom vector ID

        Returns:
            ID of stored record, or None if failed
        """
        if not self.enabled:
            logger.warning("Cannot store learning - OpenAI knowledge store not configured")
            return None

        try:
            # Generate embedding with OpenAI
            vector = await embeddings_service.embed_text(content)
            if not vector:
                logger.warning("Failed to generate OpenAI embedding for content")
                return None

            # Verify dimensions
            if len(vector) != 1536:
                logger.error(
                    f"OpenAI embedding has wrong dimensions: {len(vector)} (expected 1536)"
                )
                return None

            # Generate ID if not provided
            if not vector_id:
                import hashlib
                vector_id = hashlib.md5(
                    f"{content}_{datetime.now().isoformat()}".encode()
                ).hexdigest()

            # Add timestamp to metadata
            if "created_at" not in metadata:
                metadata["created_at"] = local_now().isoformat()

            url = f"https://{self.index_host}/vectors/upsert"

            payload = {
                "vectors": [{"id": vector_id, "values": vector, "metadata": metadata}],
                "namespace": self.namespace,
            }

            headers = {
                "Api-Key": self.api_key,
                "Content-Type": "application/json",
                "X-Pinecone-API-Version": self.api_version,
            }

            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(url, json=payload, headers=headers)
                response.raise_for_status()

            logger.info(f"Stored learning in OpenAI knowledge store: {vector_id}")
            return vector_id

        except httpx.HTTPStatusError as e:
            if e.response.status_code == 400:
                logger.error(
                    f"Pinecone 400 error when storing - likely dimension mismatch. "
                    f"Ensure PINECONE_INDEX_HOST points to a 1536-dimension index (skill-learnings). "
                    f"Error: {e.response.text}"
                )
            else:
                logger.error(f"Pinecone API error: {e}")
            return None
        except Exception as e:
            logger.error(f"Error storing in OpenAI knowledge store: {e}")
            return None

    async def search_similar(
        self,
        embedding: List[float],
        filter: Optional[Dict[str, Any]] = None,
        top_k: int = 5,
    ) -> List[Dict[str, Any]]:
        """
        Search for similar vectors using a pre-computed embedding.

        Args:
            embedding: The query embedding vector (must be 1536 dimensions)
            filter: Optional metadata filter
            top_k: Number of results to return

        Returns:
            List of similar items with scores and metadata
        """
        if not self.enabled:
            return []

        # Verify dimensions
        if len(embedding) != 1536:
            logger.error(
                f"Embedding has wrong dimensions: {len(embedding)} (expected 1536)"
            )
            return []

        try:
            url = f"https://{self.index_host}/query"

            payload = {
                "namespace": self.namespace,
                "topK": top_k,
                "vector": embedding,
                "includeMetadata": True,
            }

            if filter:
                # Clean filter to remove null values (Pinecone doesn't support null in filters)
                cleaned_filter = self._clean_filter(filter)
                if cleaned_filter:
                    payload["filter"] = cleaned_filter

            headers = {
                "Api-Key": self.api_key,
                "Content-Type": "application/json",
                "X-Pinecone-API-Version": self.api_version,
            }

            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(url, json=payload, headers=headers)
                response.raise_for_status()

            result = response.json()
            matches = result.get("matches", [])

            # Format results
            results = []
            for match in matches:
                results.append({
                    "id": match.get("id"),
                    "score": match.get("score", 0),
                    "metadata": match.get("metadata", {})
                })

            return results

        except httpx.HTTPStatusError as e:
            if e.response.status_code == 400:
                logger.error(
                    f"Pinecone 400 error in search_similar - dimension mismatch. "
                    f"Error: {e.response.text}"
                )
            else:
                logger.error(f"Pinecone API error: {e}")
            return []
        except Exception as e:
            logger.error(f"Error searching similar vectors: {e}")
            return []

    async def upsert(self, vectors: list) -> bool:
        """
        Upsert multiple vectors to Pinecone.

        Args:
            vectors: List of (id, values, metadata) tuples where values are 1536-dim vectors

        Returns:
            True if successful, False otherwise
        """
        if not self.enabled:
            logger.warning("Cannot upsert - OpenAI knowledge store not configured")
            return False

        try:
            url = f"https://{self.index_host}/vectors/upsert"

            # Format vectors for Pinecone API
            formatted_vectors = []
            for vector_tuple in vectors:
                if len(vector_tuple) == 3:
                    vector_id, values, metadata = vector_tuple

                    # Verify dimensions
                    if len(values) != 1536:
                        logger.error(
                            f"Vector {vector_id} has wrong dimensions: {len(values)} (expected 1536)"
                        )
                        continue

                    formatted_vectors.append({
                        "id": vector_id,
                        "values": values,
                        "metadata": metadata
                    })

            if not formatted_vectors:
                logger.warning("No valid vectors to upsert")
                return False

            payload = {
                "vectors": formatted_vectors,
                "namespace": self.namespace
            }

            headers = {
                "Api-Key": self.api_key,
                "Content-Type": "application/json",
                "X-Pinecone-API-Version": self.api_version
            }

            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(url, json=payload, headers=headers)
                response.raise_for_status()

            logger.info(f"Upserted {len(formatted_vectors)} vectors to OpenAI knowledge store")
            return True

        except httpx.HTTPStatusError as e:
            if e.response.status_code == 400:
                logger.error(
                    f"Pinecone 400 error during upsert - dimension mismatch. "
                    f"Error: {e.response.text}"
                )
            else:
                logger.error(f"Pinecone API error: {e}")
            return False
        except Exception as e:
            logger.error(f"Error upserting vectors: {e}")
            return False

    async def delete(self, vector_ids: List[str]) -> bool:
        """Delete vectors by IDs."""
        if not self.enabled:
            return False

        try:
            url = f"https://{self.index_host}/vectors/delete"

            headers = {
                "Api-Key": self.api_key,
                "Content-Type": "application/json",
                "X-Pinecone-API-Version": self.api_version
            }

            payload = {"ids": vector_ids, "namespace": self.namespace}

            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(url, json=payload, headers=headers)
                response.raise_for_status()

            logger.info(f"Deleted {len(vector_ids)} vectors from OpenAI knowledge store")
            return True

        except Exception as e:
            logger.error(f"Error deleting vectors: {e}")
            return False

    # Compatibility properties and methods for legacy code
    @property
    def pinecone_index(self):
        """Legacy property for compatibility."""
        return self

    async def store_learning(
        self,
        learning_id: str,
        embedding: List[float],
        metadata: Dict[str, Any]
    ) -> Optional[str]:
        """
        Store a skill learning in Pinecone (compatibility method).

        Args:
            learning_id: Unique ID for the learning
            embedding: Embedding vector (must be 1536 dimensions)
            metadata: Learning metadata

        Returns:
            Pinecone vector ID or None if failed
        """
        if len(embedding) != 1536:
            logger.error(
                f"Embedding has wrong dimensions: {len(embedding)} (expected 1536)"
            )
            return None

        vector_id = f"learning_{learning_id}"
        vectors = [(vector_id, embedding, metadata)]

        success = await self.upsert(vectors)
        return vector_id if success else None

    def _clean_filter(self, filter_dict: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Clean a filter dictionary to remove null values.

        Pinecone doesn't support null values in filters, so we need to remove them.
        This recursively cleans nested structures like $or and $and conditions.

        Args:
            filter_dict: The filter dictionary to clean

        Returns:
            Cleaned filter dictionary, or None if empty after cleaning
        """
        if not filter_dict:
            return None

        cleaned = {}
        for key, value in filter_dict.items():
            if value is None:
                # Skip null values
                continue
            elif key in ('$or', '$and'):
                # Clean nested conditions
                cleaned_conditions = []
                for condition in value:
                    if isinstance(condition, dict):
                        cleaned_condition = self._clean_filter(condition)
                        if cleaned_condition:
                            cleaned_conditions.append(cleaned_condition)
                if cleaned_conditions:
                    cleaned[key] = cleaned_conditions
            elif isinstance(value, dict):
                # Recursively clean nested dicts
                cleaned_value = self._clean_filter(value)
                if cleaned_value:
                    cleaned[key] = cleaned_value
            else:
                cleaned[key] = value

        return cleaned if cleaned else None


# Singleton instance for OpenAI embeddings
openai_knowledge_store = OpenAIKnowledgeStore()