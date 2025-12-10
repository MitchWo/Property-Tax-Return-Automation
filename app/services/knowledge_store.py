"""Knowledge store service for RAG using Pinecone."""
import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import uuid4

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


class KnowledgeStore:
    """Store and retrieve learnings from Pinecone vector database."""

    def __init__(self):
        """Initialize knowledge store."""
        self.api_key = settings.PINECONE_API_KEY
        self.index_host = settings.PINECONE_INDEX_HOST
        self.namespace = settings.PINECONE_NAMESPACE
        self.api_version = "2025-04"

        if not self.api_key or not self.index_host:
            logger.warning("Pinecone not configured - knowledge store disabled")
            self.enabled = False
        else:
            self.enabled = True

    async def search(
        self,
        query: str,
        top_k: int = None,
        min_score: float = None
    ) -> List[Dict[str, Any]]:
        """
        Search for relevant learnings.

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
            url = f"https://{self.index_host}/records/namespaces/{self.namespace}/search"

            payload = {
                "query": {
                    "top_k": top_k,
                    "inputs": {
                        "text": query
                    }
                }
            }

            headers = {
                "Api-Key": self.api_key,
                "Content-Type": "application/json",
                "X-Pinecone-API-Version": self.api_version
            }

            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(url, json=payload, headers=headers)
                response.raise_for_status()

            result = response.json()
            hits = result.get("result", {}).get("hits", [])

            # Filter by minimum score and format results
            learnings = []
            for hit in hits:
                score = hit.get("_score", 0)
                if score >= min_score:
                    learnings.append({
                        "id": hit.get("_id"),
                        "score": score,
                        "content": hit.get("fields", {}).get("content", ""),
                        "scenario": hit.get("fields", {}).get("scenario", ""),
                        "category": hit.get("fields", {}).get("category", ""),
                        "created_at": hit.get("fields", {}).get("created_at", "")
                    })

            logger.info(f"Knowledge search returned {len(learnings)} relevant results")
            return learnings

        except Exception as e:
            logger.error(f"Error searching knowledge base: {e}")
            return []

    async def store(
        self,
        content: str,
        scenario: str,
        category: str,
        source: str = "user_feedback"
    ) -> Optional[str]:
        """
        Store a new learning.

        Args:
            content: The learning content
            scenario: Scenario identifier (snake_case)
            category: Category for organization
            source: Source of learning ("user_feedback" or "agent_learning")

        Returns:
            ID of stored record, or None if failed
        """
        if not self.enabled:
            logger.warning("Cannot store learning - Pinecone not configured")
            return None

        try:
            record_id = f"{source}_{int(datetime.now().timestamp() * 1000)}"

            record = {
                "_id": record_id,
                "content": content,
                "scenario": scenario.lower().replace(" ", "_"),
                "category": category.lower().replace(" ", "_"),
                "created_at": datetime.utcnow().isoformat(),
                "source": source
            }

            url = f"https://{self.index_host}/records/namespaces/{self.namespace}/upsert"

            headers = {
                "Api-Key": self.api_key,
                "Content-Type": "application/x-ndjson",
                "X-Pinecone-API-Version": self.api_version
            }

            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    url,
                    content=json.dumps(record),
                    headers=headers
                )
                response.raise_for_status()

            logger.info(f"Stored learning: {record_id} ({scenario}/{category})")
            return record_id

        except Exception as e:
            logger.error(f"Error storing learning: {e}")
            return None

    async def list_learnings(
        self,
        limit: int = 50
    ) -> List[Dict[str, Any]]:
        """
        List recent learnings.

        Note: Pinecone doesn't support listing without a query,
        so we use a generic query to get recent items.
        """
        if not self.enabled:
            return []

        # Use a broad query to get learnings
        return await self.search(
            query="document classification tax return property",
            top_k=limit,
            min_score=0.0
        )

    async def delete(self, record_id: str) -> bool:
        """Delete a learning by ID."""
        if not self.enabled:
            return False

        try:
            url = f"https://{self.index_host}/records/namespaces/{self.namespace}/delete"

            headers = {
                "Api-Key": self.api_key,
                "Content-Type": "application/json",
                "X-Pinecone-API-Version": self.api_version
            }

            payload = {"ids": [record_id]}

            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(url, json=payload, headers=headers)
                response.raise_for_status()

            logger.info(f"Deleted learning: {record_id}")
            return True

        except Exception as e:
            logger.error(f"Error deleting learning: {e}")
            return False

    def format_knowledge_context(self, learnings: List[Dict[str, Any]]) -> str:
        """Format retrieved learnings for injection into prompts."""
        if not learnings:
            return ""

        context = "\n\n## RELEVANT KNOWLEDGE FROM PAST FEEDBACK\n\n"
        context += "Apply these learnings from past corrections where relevant:\n\n"

        for i, learning in enumerate(learnings, 1):
            score_pct = int(learning["score"] * 100)
            context += f"### Learning {i} (Relevance: {score_pct}%)\n"
            context += f"**Scenario:** {learning['scenario']}\n"
            context += f"**Category:** {learning['category']}\n"
            context += f"**Guidance:** {learning['content']}\n\n"

        context += "---\n\n"
        return context


# Singleton instance
knowledge_store = KnowledgeStore()