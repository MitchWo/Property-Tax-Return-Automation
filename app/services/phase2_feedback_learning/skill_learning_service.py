"""Skill Learning Service for RAG Integration.

This service manages skill-specific learnings and integrates with the existing
embeddings and Pinecone infrastructure to provide context-aware AI responses.
"""

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import UUID

from sqlalchemy import and_, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    AppliesTo,
    CategoryFeedback,
    LearningType,
    SkillLearning,
    Transaction,
)
from app.services.phase2_feedback_learning.embeddings import EmbeddingsService
from app.services.phase2_feedback_learning.openai_knowledge_store import (
    OpenAIKnowledgeStore,
    openai_knowledge_store,
)

logger = logging.getLogger(__name__)


class SkillLearningService:
    """Service for managing skill learnings and RAG integration."""

    def __init__(
        self,
        db_session: AsyncSession,
        embeddings_service: Optional[EmbeddingsService] = None,
        knowledge_store: Optional[OpenAIKnowledgeStore] = None
    ):
        """Initialize the skill learning service.

        Args:
            db_session: Database session
            embeddings_service: Optional embeddings service (will create if not provided)
            knowledge_store: Optional knowledge store (will create if not provided)
        """
        self.db = db_session
        self.embeddings = embeddings_service or EmbeddingsService()
        self.knowledge = knowledge_store or openai_knowledge_store

    async def create_learning(
        self,
        skill_name: str,
        learning_type: LearningType,
        title: str,
        content: str,
        keywords: Optional[List[str]] = None,
        applies_to: AppliesTo = AppliesTo.TRANSACTION,
        client_id: Optional[UUID] = None,
        category_code: Optional[str] = None,
        created_by: Optional[str] = None
    ) -> SkillLearning:
        """Create a new skill learning entry.

        Args:
            skill_name: Name of the skill (e.g., 'nz_rental_returns')
            learning_type: Type of learning (teaching, correction, pattern, edge_case)
            title: Brief title for the learning
            content: Detailed content of the learning
            keywords: Optional keywords for search
            applies_to: What this learning applies to
            client_id: Optional client ID for client-specific learnings
            category_code: Optional category code for category-specific learnings
            created_by: Optional creator identifier

        Returns:
            Created SkillLearning instance
        """
        try:
            # Create the learning entry
            learning = SkillLearning(
                skill_name=skill_name,
                learning_type=learning_type.value if isinstance(learning_type, LearningType) else learning_type,
                title=title,
                content=content,
                keywords=keywords,  # Store as JSONB
                applies_to=applies_to.value if isinstance(applies_to, AppliesTo) else applies_to,
                client_id=client_id,
                category_code=category_code,
                created_by=created_by
            )

            self.db.add(learning)
            await self.db.flush()  # Get the ID before embedding

            # Generate embeddings and store in Pinecone
            if self.embeddings and self.knowledge:
                try:
                    # Create searchable text combining title, content, and keywords
                    searchable_text = self._create_searchable_text(learning)

                    # Generate embedding
                    embedding = await self.embeddings.embed_text(searchable_text)

                    # Store in Pinecone with metadata
                    # Use 'global' instead of None for client_id since Pinecone doesn't support null filters
                    metadata = {
                        'skill_name': skill_name,
                        'learning_type': learning_type.value,
                        'title': title,
                        'applies_to': applies_to.value,
                        'client_id': str(client_id) if client_id else 'global',
                        'category_code': category_code,
                        'created_at': datetime.utcnow().isoformat(),
                        'created_by': created_by,
                        'learning_id': str(learning.id)
                    }

                    # Store in Pinecone
                    embedding_id = await self.knowledge.store_learning(
                        learning_id=str(learning.id),
                        embedding=embedding,
                        metadata=metadata
                    )

                    # Update learning with embedding reference
                    learning.embedding_id = embedding_id

                except Exception as e:
                    logger.error(f"Failed to create embedding for learning: {e}")
                    # Continue without embedding - learning is still useful

            await self.db.commit()
            logger.info(f"Created learning {learning.id} for skill {skill_name}")

            return learning

        except Exception as e:
            await self.db.rollback()
            logger.error(f"Failed to create learning: {e}")
            raise

    async def learn_from_correction(
        self,
        transaction_id: UUID,
        original_category: Optional[str],
        corrected_category: str,
        corrected_by: Optional[str] = None,
        notes: Optional[str] = None
    ) -> SkillLearning:
        """Learn from a category correction.

        Args:
            transaction_id: ID of the corrected transaction
            original_category: Original category code
            corrected_category: Corrected category code
            corrected_by: Who made the correction
            notes: Optional notes about the correction

        Returns:
            Created SkillLearning instance
        """
        try:
            # Get the transaction details
            transaction = await self.db.get(Transaction, transaction_id)
            if not transaction:
                raise ValueError(f"Transaction {transaction_id} not found")

            # Create feedback record
            feedback = CategoryFeedback(
                transaction_id=transaction_id,
                original_category=original_category,
                corrected_category=corrected_category,
                corrected_by=corrected_by,
                notes=notes
            )
            self.db.add(feedback)

            # Create learning content
            title = f"Correction: {transaction.description[:50]}"
            content = f"""
Category Correction Learning:
- Description: {transaction.description}
- Other Party: {transaction.other_party}
- Amount: ${abs(transaction.amount)}
- Date: {transaction.transaction_date}
- Original Category: {original_category or 'uncategorized'}
- Correct Category: {corrected_category}
- Reason: {notes or 'User correction'}

This transaction should be categorized as '{corrected_category}' based on user feedback.
"""

            # Extract keywords from transaction
            keywords = self._extract_keywords(transaction.description, transaction.other_party)

            # Create the learning
            learning = await self.create_learning(
                skill_name='nz_rental_returns',
                learning_type=LearningType.CORRECTION,
                title=title,
                content=content,
                keywords=keywords,
                applies_to=AppliesTo.TRANSACTION,
                client_id=transaction.tax_return.client_id if transaction.tax_return else None,
                category_code=corrected_category,
                created_by=corrected_by
            )

            # Update feedback with pattern reference
            feedback.pattern_created = True
            feedback.pattern_id = learning.id

            await self.db.commit()

            return learning

        except Exception as e:
            await self.db.rollback()
            logger.error(f"Failed to learn from correction: {e}")
            raise

    async def get_relevant_learnings(
        self,
        query: str,
        skill_name: str = 'nz_rental_returns',
        learning_types: Optional[List[LearningType]] = None,
        client_id: Optional[UUID] = None,
        limit: int = 5,
        min_confidence: float = 0.5
    ) -> List[Dict[str, Any]]:
        """Get relevant learnings for a query using semantic search.

        Args:
            query: Search query
            skill_name: Skill to search within
            learning_types: Optional filter by learning types
            client_id: Optional client-specific filter
            limit: Maximum number of results
            min_confidence: Minimum confidence threshold

        Returns:
            List of relevant learnings with metadata
        """
        try:
            # Build filter for Pinecone query
            filter_dict = {
                'skill_name': skill_name
            }

            if learning_types:
                filter_dict['learning_type'] = {'$in': [lt.value for lt in learning_types]}

            if client_id:
                # Filter by client_id only - Pinecone doesn't support null values in filters
                # Global learnings (client_id="global") will be included separately or by not filtering
                filter_dict['$or'] = [
                    {'client_id': str(client_id)},
                    {'client_id': 'global'}
                ]

            # Generate query embedding
            query_embedding = await self.embeddings.embed_text(query)

            # Search in Pinecone
            results = await self.knowledge.search_similar(
                embedding=query_embedding,
                filter=filter_dict,
                top_k=limit
            )

            # Filter by confidence and fetch full learning records
            learning_results = []
            for result in results:
                if result.get('score', 0) >= min_confidence:
                    learning_id = result['metadata'].get('learning_id')
                    if learning_id:
                        learning = await self.db.get(SkillLearning, UUID(learning_id))
                        if learning and learning.is_active:
                            learning_results.append({
                                'id': str(learning.id),
                                'title': learning.title,
                                'content': learning.content,
                                'learning_type': learning.learning_type,
                                'confidence': result.get('score', 0),
                                'relevance_score': result.get('score', 0),
                                'category_code': learning.category_code,
                                'applies_to': learning.applies_to,
                                'times_applied': learning.times_applied,
                                'times_confirmed': learning.times_confirmed
                            })

            return learning_results

        except Exception as e:
            logger.error(f"Failed to get relevant learnings: {e}")
            return []

    async def apply_learning(
        self,
        learning_id: UUID,
        was_successful: bool = True
    ) -> None:
        """Track when a learning is applied.

        Args:
            learning_id: ID of the learning that was applied
            was_successful: Whether the application was successful
        """
        try:
            learning = await self.db.get(SkillLearning, learning_id)
            if learning:
                learning.times_applied += 1
                if was_successful:
                    learning.times_confirmed += 1
                    # Increase confidence if consistently successful
                    if learning.times_applied >= 5:
                        success_rate = learning.times_confirmed / learning.times_applied
                        learning.confidence = min(0.95, success_rate)

                learning.updated_at = datetime.utcnow()
                await self.db.commit()

        except Exception as e:
            logger.error(f"Failed to track learning application: {e}")
            await self.db.rollback()

    async def search_learnings(
        self,
        skill_name: str,
        text_query: Optional[str] = None,
        learning_type: Optional[LearningType] = None,
        client_id: Optional[UUID] = None,
        category_code: Optional[str] = None,
        min_confidence: float = 0.0,
        limit: int = 50
    ) -> List[SkillLearning]:
        """Search for learnings with various filters.

        Args:
            skill_name: Skill name to search
            text_query: Optional text search in title/content
            learning_type: Optional learning type filter
            client_id: Optional client filter
            category_code: Optional category filter
            min_confidence: Minimum confidence threshold
            limit: Maximum results

        Returns:
            List of matching SkillLearning instances
        """
        from sqlalchemy import select

        query = select(SkillLearning).where(
            and_(
                SkillLearning.skill_name == skill_name,
                SkillLearning.is_active.is_(True),
                SkillLearning.confidence >= min_confidence
            )
        )

        if learning_type:
            query = query.where(SkillLearning.learning_type == learning_type)

        if client_id:
            # Include both global and client-specific
            query = query.where(
                or_(
                    SkillLearning.client_id == client_id,
                    SkillLearning.client_id.is_(None)
                )
            )

        if category_code:
            query = query.where(SkillLearning.category_code == category_code)

        if text_query:
            # Simple text search in title and content
            search_pattern = f"%{text_query}%"
            query = query.where(
                or_(
                    SkillLearning.title.ilike(search_pattern),
                    SkillLearning.content.ilike(search_pattern)
                )
            )

        query = query.order_by(
            SkillLearning.confidence.desc(),
            SkillLearning.created_at.desc()
        ).limit(limit)

        result = await self.db.execute(query)
        return result.scalars().all()

    async def create_teaching(
        self,
        skill_name: str,
        title: str,
        content: str,
        keywords: Optional[List[str]] = None,
        category_code: Optional[str] = None,
        created_by: str = "system"
    ) -> SkillLearning:
        """Create a teaching entry (proactive knowledge).

        Args:
            skill_name: Skill this teaching applies to
            title: Teaching title
            content: Teaching content
            keywords: Search keywords
            category_code: Optional category this applies to
            created_by: Creator identifier

        Returns:
            Created SkillLearning instance
        """
        return await self.create_learning(
            skill_name=skill_name,
            learning_type=LearningType.TEACHING,
            title=title,
            content=content,
            keywords=keywords,
            applies_to=AppliesTo.BOTH,
            category_code=category_code,
            created_by=created_by
        )

    async def get_context_for_categorization(
        self,
        description: str,
        other_party: Optional[str] = None,
        amount: Optional[float] = None,
        client_id: Optional[UUID] = None
    ) -> str:
        """Get relevant context for transaction categorization.

        Args:
            description: Transaction description
            other_party: Transaction other party
            amount: Transaction amount
            client_id: Optional client ID for client-specific learnings

        Returns:
            Formatted context string for AI prompt augmentation
        """
        try:
            # Build search query from transaction details
            query_parts = [description]
            if other_party:
                query_parts.append(other_party)

            query = " ".join(query_parts)

            # Get relevant learnings
            learnings = await self.get_relevant_learnings(
                query=query,
                skill_name='nz_rental_returns',
                learning_types=[LearningType.CORRECTION, LearningType.PATTERN],
                client_id=client_id,
                limit=3,
                min_confidence=0.6
            )

            if not learnings:
                return ""

            # Format learnings as context
            context_parts = ["Based on previous learnings:"]

            for learning in learnings:
                context_parts.append(
                    f"- {learning['title']} (confidence: {learning['confidence']:.0%}): "
                    f"{learning['content'][:200]}..."
                )

            return "\n".join(context_parts)

        except Exception as e:
            logger.error(f"Failed to get categorization context: {e}")
            return ""

    def _create_searchable_text(self, learning: SkillLearning) -> str:
        """Create searchable text from a learning entry.

        Args:
            learning: SkillLearning instance

        Returns:
            Combined searchable text
        """
        parts = [
            learning.title,
            learning.content,
            learning.skill_name,
            learning.learning_type
        ]

        if learning.keywords:
            # Keywords stored as JSONB list
            if isinstance(learning.keywords, list):
                parts.extend(learning.keywords)
            elif isinstance(learning.keywords, str):
                parts.append(learning.keywords)

        if learning.category_code:
            parts.append(learning.category_code)

        return " ".join(filter(None, parts))

    def _extract_keywords(
        self,
        description: str,
        other_party: Optional[str] = None
    ) -> List[str]:
        """Extract keywords from transaction details.

        Args:
            description: Transaction description
            other_party: Optional other party

        Returns:
            List of extracted keywords
        """
        keywords = []

        # Split description into words and filter
        if description:
            words = description.lower().split()
            # Keep meaningful words (not too short, not numbers)
            keywords.extend([
                w for w in words
                if len(w) > 3 and not w.replace('.', '').replace(',', '').isdigit()
            ])

        # Add other party as keyword
        if other_party:
            keywords.append(other_party.lower())

        # Remove duplicates while preserving order
        seen = set()
        unique_keywords = []
        for k in keywords:
            if k not in seen:
                seen.add(k)
                unique_keywords.append(k)

        return unique_keywords[:10]  # Limit to 10 keywords


# Note: The OpenAIKnowledgeStore already has store_learning method built-in
# No need for monkey-patching anymore since we're using the new OpenAIKnowledgeStore class