"""
RAG Categorization Integration - Connects learnings to transaction categorization.

This service:
1. Searches for relevant learnings before Claude is called
2. Formats learnings for injection into Claude prompts
3. Creates learnings from user corrections
"""

import logging
import re
from decimal import Decimal
from typing import List, Optional, Dict, Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.db_models import Transaction, SkillLearning, LearningType, AppliesTo
from app.services.phase2_feedback_learning.skill_learning_service import SkillLearningService
from app.services.phase2_feedback_learning.embeddings import EmbeddingsService
from app.services.phase2_feedback_learning.knowledge_store import knowledge_store

logger = logging.getLogger(__name__)


class RAGCategorizationIntegration:
    """Integrates RAG learnings with transaction categorization."""

    def __init__(self, db: AsyncSession):
        self.db = db
        self.skill_service = SkillLearningService(db)
        self.embeddings = EmbeddingsService()

        # Thresholds
        self.HIGH_CONFIDENCE_THRESHOLD = 0.85  # Use learning directly
        self.CONTEXT_THRESHOLD = 0.65  # Include in Claude prompt

    async def get_categorization_context(
        self,
        description: str,
        other_party: Optional[str] = None,
        amount: Optional[Decimal] = None,
        client_id: Optional[UUID] = None
    ) -> dict:
        """
        Get RAG context for categorizing a transaction.

        Searches MULTIPLE namespaces for comprehensive context:
        - skill_learnings: Domain knowledge, tax rules, teachings
        - transaction-coding: Specific transaction patterns

        Returns:
            {
                "direct_match": {category, confidence, learning_id, title} or None,
                "context_learnings": [formatted learnings for Claude prompt],
                "raw_learnings": [full learning objects]
            }
        """
        # Build search query
        query_parts = [description]
        if other_party:
            query_parts.append(other_party)
        query = " ".join(query_parts)

        # Search for relevant learnings from skill learning service (database + Pinecone)
        learnings = await self.skill_service.get_relevant_learnings(
            query=query,
            skill_name="nz_rental_returns",
            learning_types=[LearningType.CORRECTION, LearningType.PATTERN, LearningType.TEACHING],
            client_id=client_id,
            limit=5,
            min_confidence=0.5
        )

        # Also search the transaction-coding namespace directly via knowledge_store
        # This catches patterns that may not be in the SkillLearning database
        try:
            if knowledge_store.enabled:
                transaction_patterns = await knowledge_store.search_for_categorization(
                    description=description,
                    other_party=other_party,
                    top_k=5
                )

                # Add transaction patterns to learnings if they provide new information
                existing_ids = {item.get("id") for item in learnings if item.get("id")}
                for pattern in transaction_patterns:
                    pattern_id = pattern.get("id")
                    if pattern_id and pattern_id not in existing_ids:
                        learnings.append({
                            "id": pattern_id,
                            "title": pattern.get("scenario", "Transaction Pattern"),
                            "content": pattern.get("content", ""),
                            "learning_type": "pattern",
                            "category_code": pattern.get("category", ""),
                            "relevance_score": pattern.get("score", 0),
                            "confidence": pattern.get("score", 0),
                            "source_namespace": pattern.get("source_namespace", "transaction-coding")
                        })
                        existing_ids.add(pattern_id)

                # Sort combined learnings by score/confidence
                learnings.sort(
                    key=lambda x: x.get("relevance_score", x.get("confidence", 0)),
                    reverse=True
                )
        except Exception as e:
            logger.warning(f"Failed to search transaction-coding namespace: {e}")

        result = {
            "direct_match": None,
            "context_learnings": [],
            "raw_learnings": learnings,
            "query": query
        }

        if not learnings:
            return result

        # Check for high-confidence direct match
        best_match = learnings[0] if learnings else None
        if best_match and best_match.get("relevance_score", 0) >= self.HIGH_CONFIDENCE_THRESHOLD:
            if best_match.get("category_code"):
                result["direct_match"] = {
                    "category": best_match["category_code"],
                    "confidence": best_match.get("relevance_score", 0),
                    "learning_id": best_match.get("id"),
                    "title": best_match.get("title", ""),
                    "content": best_match.get("content", "")
                }

        # Gather context learnings for Claude prompt
        for learning in learnings:
            score = learning.get("relevance_score", learning.get("confidence", 0))
            if score >= self.CONTEXT_THRESHOLD:
                result["context_learnings"].append({
                    "title": learning.get("title", ""),
                    "content": learning.get("content", ""),
                    "category": learning.get("category_code"),
                    "type": learning.get("learning_type", ""),
                    "confidence": score,
                    "source_namespace": learning.get("source_namespace", "skill_learnings")
                })

        return result

    def format_learnings_for_prompt(self, context: dict) -> str:
        """
        Format learnings for injection into Claude prompt.

        Returns a string to append to the categorization prompt.
        """
        learnings = context.get("context_learnings", [])

        if not learnings:
            return ""

        lines = [
            "",
            "=== RELEVANT KNOWLEDGE FROM PREVIOUS LEARNINGS ===",
            "Use this context to help categorize the transaction:",
            ""
        ]

        for idx, learning in enumerate(learnings, 1):
            # Show source namespace for transparency (all 8 namespaces)
            source = learning.get('source_namespace', 'skill_learnings')
            source_label = {
                'skill_learnings': '[Domain Knowledge]',
                'common-errors': '[Common Error Pattern]',
                'document-review': '[Document Pattern]',
                'workbook-structure': '[Workbook Structure]',
                'pnl-mapping': '[P&L Mapping]',
                'gst-rules': '[GST Rule]',
                'tax-rules': '[Tax Rule/Treatment]',
                'transaction-coding': '[Transaction Pattern]'
            }.get(source, f'[{source}]')

            lines.append(f"{idx}. {learning['title']} {source_label}")
            lines.append(f"   {learning['content']}")
            if learning.get('category'):
                lines.append(f"   → Suggests category: {learning['category']}")
            lines.append("")

        lines.append("Consider these learnings when making your categorization decision.")
        lines.append("=" * 50)

        return "\n".join(lines)

    async def learn_from_correction(
        self,
        transaction: Transaction,
        original_category: Optional[str],
        corrected_category: str,
        corrected_by: Optional[str] = None,
        notes: Optional[str] = None
    ) -> Optional[SkillLearning]:
        """
        Create a learning from a user correction.

        This is called when a user changes a transaction's category.
        """
        # Build descriptive content
        content_parts = [
            f"Transaction '{transaction.description}'"
        ]
        if transaction.other_party:
            content_parts.append(f"from '{transaction.other_party}'")
        content_parts.append(f"should be categorized as '{corrected_category}'")
        if original_category and original_category != corrected_category:
            content_parts.append(f"(was incorrectly '{original_category}')")
        if notes:
            content_parts.append(f". Note: {notes}")

        content = " ".join(content_parts)

        # Extract keywords from description
        keywords = self._extract_keywords(transaction.description, transaction.other_party)

        # Create the learning
        try:
            learning = await self.skill_service.create_learning(
                skill_name="nz_rental_returns",
                learning_type=LearningType.CORRECTION,
                title=f"Correction: {transaction.description[:50]}",
                content=content,
                keywords=keywords,
                applies_to=AppliesTo.TRANSACTION,
                category_code=corrected_category,
                client_id=transaction.tax_return.client_id if transaction.tax_return else None,
                created_by=corrected_by
            )

            logger.info(f"Created learning from correction: {learning.id}")
            return learning

        except Exception as e:
            logger.error(f"Failed to create learning from correction: {e}")
            return None

    async def record_pattern(
        self,
        description: str,
        other_party: Optional[str],
        category_code: str,
        confidence: float = 0.8,
        client_id: Optional[UUID] = None,
        source: str = "ai_categorization"
    ) -> Optional[SkillLearning]:
        """
        Record a successful categorization pattern for future use.

        Called after Claude successfully categorizes a transaction.
        """
        content = f"Transactions with description '{description}'"
        if other_party:
            content += f" from '{other_party}'"
        content += f" are typically categorized as '{category_code}'"

        keywords = self._extract_keywords(description, other_party)

        try:
            learning = await self.skill_service.create_learning(
                skill_name="nz_rental_returns",
                learning_type=LearningType.PATTERN,
                title=f"Pattern: {description[:50]}",
                content=content,
                keywords=keywords,
                applies_to=AppliesTo.TRANSACTION,
                category_code=category_code,
                client_id=client_id,
                created_by=source
            )

            # Set initial confidence
            learning.confidence = confidence
            await self.db.commit()

            logger.info(f"Created pattern learning: {learning.id}")
            return learning

        except Exception as e:
            logger.error(f"Failed to create pattern learning: {e}")
            return None

    async def track_categorization_success(
        self,
        learning_id: UUID,
        was_successful: bool = True
    ) -> None:
        """
        Track whether a learning-based categorization was successful.

        Updates confidence scores based on feedback.
        """
        await self.skill_service.apply_learning(
            learning_id=learning_id,
            was_successful=was_successful
        )

    def _extract_keywords(self, description: str, other_party: Optional[str] = None) -> List[str]:
        """Extract keywords from transaction description."""
        # Combine sources
        text = description.lower()
        if other_party:
            text += " " + other_party.lower()

        # Remove common words and numbers
        stop_words = {
            'the', 'a', 'an', 'and', 'or', 'but', 'in', 'on', 'at', 'to', 'for',
            'of', 'with', 'by', 'from', 'as', 'is', 'was', 'are', 'were', 'been',
            'be', 'have', 'has', 'had', 'do', 'does', 'did', 'will', 'would',
            'could', 'should', 'may', 'might', 'must', 'shall', 'can', 'need',
            'payment', 'paid', 'pay', 'ref', 'reference', 'transaction', 'transfer',
            'debit', 'credit', 'withdrawal', 'deposit', 'fee', 'charge', 'purchase'
        }

        # Tokenize and filter
        words = re.findall(r'[a-z]+', text)
        keywords = [w for w in words if w not in stop_words and len(w) > 2]

        # Dedupe and limit
        seen = set()
        unique_keywords = []
        for kw in keywords:
            if kw not in seen:
                seen.add(kw)
                unique_keywords.append(kw)

        return unique_keywords[:10]

    async def get_category_teachings(
        self,
        category_code: str,
        limit: int = 3
    ) -> List[Dict[str, Any]]:
        """
        Get teaching learnings for a specific category.

        Useful for understanding why a category was chosen.
        """
        learnings = await self.skill_service.search_learnings(
            skill_name="nz_rental_returns",
            learning_type=LearningType.TEACHING,
            category_code=category_code,
            limit=limit,
            min_confidence=0.7
        )

        return [
            {
                "id": str(learning.id),
                "title": learning.title,
                "content": learning.content,
                "confidence": learning.confidence
            }
            for learning in learnings
        ]

    async def suggest_categories(
        self,
        description: str,
        other_party: Optional[str] = None,
        amount: Optional[Decimal] = None,
        client_id: Optional[UUID] = None,
        top_k: int = 3
    ) -> List[Dict[str, Any]]:
        """
        Suggest categories based on learnings without calling Claude.

        Returns list of suggestions with confidence scores.
        """
        context = await self.get_categorization_context(
            description=description,
            other_party=other_party,
            amount=amount,
            client_id=client_id
        )

        suggestions = []
        categories_seen = set()

        # Add direct match if available
        if context["direct_match"]:
            suggestions.append({
                "category_code": context["direct_match"]["category"],
                "confidence": context["direct_match"]["confidence"],
                "source": "high_confidence_match",
                "reasoning": context["direct_match"]["title"]
            })
            categories_seen.add(context["direct_match"]["category"])

        # Add other suggestions from context learnings
        for learning in context["context_learnings"]:
            if learning.get("category") and learning["category"] not in categories_seen:
                suggestions.append({
                    "category_code": learning["category"],
                    "confidence": learning["confidence"],
                    "source": "relevant_learning",
                    "reasoning": learning["title"]
                })
                categories_seen.add(learning["category"])

                if len(suggestions) >= top_k:
                    break

        return suggestions


def get_rag_integration(db: AsyncSession) -> RAGCategorizationIntegration:
    """Factory function for RAG integration service."""
    return RAGCategorizationIntegration(db)