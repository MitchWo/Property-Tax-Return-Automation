"""RAG-enhanced transaction categorization.

Integrates skill learnings with transaction categorization to improve accuracy
through retrieval-augmented generation.
"""

import logging
from typing import Any, Dict, List, Optional, Tuple
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    AppliesTo,
    LearningType,
    Transaction,
    TransactionPattern,
)
from app.services.phase2_feedback_learning.skill_learning_service import (
    SkillLearningService,
)

logger = logging.getLogger(__name__)


class RAGCategorizer:
    """Enhanced categorizer using RAG for improved accuracy."""

    def __init__(
        self,
        db_session: AsyncSession,
        skill_learning_service: Optional[SkillLearningService] = None
    ):
        """Initialize the RAG categorizer.

        Args:
            db_session: Database session
            skill_learning_service: Optional skill learning service
        """
        self.db = db_session
        self.skill_service = skill_learning_service or SkillLearningService(db_session)

    async def enhance_categorization_prompt(
        self,
        transaction: Transaction,
        base_prompt: str,
        client_id: Optional[UUID] = None
    ) -> Tuple[str, List[Dict[str, Any]]]:
        """Enhance a categorization prompt with relevant learnings.

        Args:
            transaction: Transaction to categorize
            base_prompt: Base categorization prompt
            client_id: Optional client ID for client-specific learnings

        Returns:
            Tuple of (enhanced_prompt, relevant_learnings)
        """
        try:
            # Get relevant learnings for this transaction
            relevant_learnings = await self._get_transaction_learnings(
                description=transaction.description,
                other_party=transaction.other_party,
                amount=float(transaction.amount) if transaction.amount else None,
                client_id=client_id
            )

            if not relevant_learnings:
                return base_prompt, []

            # Build context section
            context_lines = [
                "\n## Relevant Historical Learnings\n",
                "Consider these previous categorization decisions and patterns:\n"
            ]

            for idx, learning in enumerate(relevant_learnings, 1):
                context_lines.append(
                    f"\n### Learning {idx} ({learning['learning_type']}, "
                    f"confidence: {learning['confidence']:.0%})"
                )

                # Add relevant content
                if learning['learning_type'] == 'correction':
                    context_lines.append(f"- Previous correction: {learning['title']}")
                    # Extract key information from content
                    if 'Correct Category:' in learning['content']:
                        for line in learning['content'].split('\n'):
                            if 'Correct Category:' in line or 'Reason:' in line:
                                context_lines.append(f"  {line.strip()}")

                elif learning['learning_type'] == 'pattern':
                    context_lines.append(f"- Recognized pattern: {learning['title']}")
                    if learning.get('category_code'):
                        context_lines.append(f"  Typically categorized as: {learning['category_code']}")

                elif learning['learning_type'] == 'teaching':
                    context_lines.append(f"- Rule: {learning['title']}")
                    # Include first few lines of content
                    content_lines = learning['content'].split('\n')[:3]
                    for line in content_lines:
                        if line.strip():
                            context_lines.append(f"  {line.strip()}")

            # Combine with base prompt
            enhanced_prompt = base_prompt + "\n" + "\n".join(context_lines)

            # Add instruction to consider learnings
            enhanced_prompt += (
                "\n\n**Important**: Consider the historical learnings above when "
                "making your categorization decision. If a similar transaction was "
                "previously corrected, apply that learning."
            )

            return enhanced_prompt, relevant_learnings

        except Exception as e:
            logger.error(f"Failed to enhance prompt: {e}")
            return base_prompt, []

    async def learn_from_categorization(
        self,
        transaction: Transaction,
        category_code: str,
        confidence: float,
        source: str,
        reasoning: Optional[str] = None
    ) -> None:
        """Create a learning from a successful categorization.

        Args:
            transaction: Categorized transaction
            category_code: Assigned category
            confidence: Confidence level
            source: Source of categorization
            reasoning: Optional reasoning for the categorization
        """
        try:
            # Only learn from high-confidence categorizations
            if confidence < 0.8:
                return

            # Don't learn from manual corrections (those go through learn_from_correction)
            if source == 'manual':
                return

            # Check if we already have a pattern for this
            existing = await self._check_existing_pattern(
                transaction.description,
                transaction.other_party
            )

            if existing:
                # Update existing pattern confidence
                existing.confidence = min(0.95, existing.confidence + 0.05)
                existing.times_applied += 1
                return

            # Create a new pattern learning
            title = f"Pattern: {transaction.description[:50]}"
            content = f"""
Categorization Pattern Learned:
- Description: {transaction.description}
- Other Party: {transaction.other_party or 'N/A'}
- Amount: ${abs(transaction.amount)}
- Category: {category_code}
- Confidence: {confidence:.0%}
- Source: {source}
- Reasoning: {reasoning or 'Automated categorization'}

This pattern can be applied to similar future transactions.
"""

            keywords = self.skill_service._extract_keywords(
                transaction.description,
                transaction.other_party
            )

            await self.skill_service.create_learning(
                skill_name='nz_rental_returns',
                learning_type=LearningType.PATTERN,
                title=title,
                content=content,
                keywords=keywords,
                applies_to=AppliesTo.TRANSACTION,
                client_id=transaction.tax_return.client_id if transaction.tax_return else None,
                category_code=category_code,
                created_by=source
            )

            logger.info(f"Learned pattern from transaction {transaction.id}")

        except Exception as e:
            logger.error(f"Failed to learn from categorization: {e}")

    async def get_categorization_suggestions(
        self,
        description: str,
        other_party: Optional[str] = None,
        amount: Optional[float] = None,
        client_id: Optional[UUID] = None,
        top_k: int = 3
    ) -> List[Dict[str, Any]]:
        """Get categorization suggestions based on learnings.

        Args:
            description: Transaction description
            other_party: Transaction other party
            amount: Transaction amount
            client_id: Optional client ID
            top_k: Number of suggestions to return

        Returns:
            List of categorization suggestions with confidence scores
        """
        try:
            # Get relevant learnings
            learnings = await self._get_transaction_learnings(
                description=description,
                other_party=other_party,
                amount=amount,
                client_id=client_id,
                limit=top_k * 2  # Get more to filter
            )

            # Extract category suggestions
            suggestions = {}
            for learning in learnings:
                category = learning.get('category_code')
                if category:
                    if category not in suggestions:
                        suggestions[category] = {
                            'category_code': category,
                            'confidence': learning['confidence'],
                            'sources': [],
                            'reasoning': []
                        }

                    suggestions[category]['sources'].append(learning['learning_type'])
                    suggestions[category]['confidence'] = max(
                        suggestions[category]['confidence'],
                        learning['confidence']
                    )

                    # Add reasoning from learning
                    if learning['learning_type'] == 'correction':
                        suggestions[category]['reasoning'].append(
                            "Previously corrected similar transaction"
                        )
                    elif learning['learning_type'] == 'pattern':
                        suggestions[category]['reasoning'].append(
                            f"Matches learned pattern: {learning['title']}"
                        )

            # Sort by confidence and return top k
            sorted_suggestions = sorted(
                suggestions.values(),
                key=lambda x: x['confidence'],
                reverse=True
            )[:top_k]

            return sorted_suggestions

        except Exception as e:
            logger.error(f"Failed to get suggestions: {e}")
            return []

    async def record_edge_case(
        self,
        transaction: Transaction,
        issue_description: str,
        resolution: Optional[str] = None,
        category_code: Optional[str] = None
    ) -> None:
        """Record an edge case for future reference.

        Args:
            transaction: Transaction representing edge case
            issue_description: Description of what makes this an edge case
            resolution: How it was resolved
            category_code: Final category if determined
        """
        try:
            title = f"Edge case: {transaction.description[:40]}"
            content = f"""
Edge Case Identified:
- Description: {transaction.description}
- Other Party: {transaction.other_party or 'N/A'}
- Amount: ${abs(transaction.amount)}
- Issue: {issue_description}
- Resolution: {resolution or 'Pending manual review'}
- Category: {category_code or 'Undetermined'}

This transaction represents an edge case that requires special handling.
"""

            keywords = self.skill_service._extract_keywords(
                transaction.description,
                transaction.other_party
            )
            keywords.append("edge_case")

            await self.skill_service.create_learning(
                skill_name='nz_rental_returns',
                learning_type=LearningType.EDGE_CASE,
                title=title,
                content=content,
                keywords=keywords,
                applies_to=AppliesTo.TRANSACTION,
                client_id=transaction.tax_return.client_id if transaction.tax_return else None,
                category_code=category_code,
                created_by="system"
            )

            logger.info(f"Recorded edge case for transaction {transaction.id}")

        except Exception as e:
            logger.error(f"Failed to record edge case: {e}")

    async def _get_transaction_learnings(
        self,
        description: str,
        other_party: Optional[str] = None,
        amount: Optional[float] = None,
        client_id: Optional[UUID] = None,
        limit: int = 5
    ) -> List[Dict[str, Any]]:
        """Get relevant learnings for a transaction.

        Args:
            description: Transaction description
            other_party: Transaction other party
            amount: Transaction amount
            client_id: Optional client ID
            limit: Maximum number of learnings

        Returns:
            List of relevant learnings
        """
        # Build search query
        query_parts = [description]
        if other_party:
            query_parts.append(other_party)

        # Include amount range if significant
        if amount and abs(amount) > 100:
            if amount > 0:
                query_parts.append("expense")
            else:
                query_parts.append("income")

        query = " ".join(query_parts)

        # Get learnings from skill service
        return await self.skill_service.get_relevant_learnings(
            query=query,
            skill_name='nz_rental_returns',
            learning_types=[
                LearningType.CORRECTION,
                LearningType.PATTERN,
                LearningType.TEACHING
            ],
            client_id=client_id,
            limit=limit,
            min_confidence=0.5
        )

    async def _check_existing_pattern(
        self,
        description: str,
        other_party: Optional[str] = None
    ) -> Optional[TransactionPattern]:
        """Check if a pattern already exists.

        Args:
            description: Transaction description
            other_party: Transaction other party

        Returns:
            Existing pattern if found
        """
        from sqlalchemy import select

        query = select(TransactionPattern).where(
            TransactionPattern.description_normalized == description.lower().strip()
        )

        if other_party:
            query = query.where(
                TransactionPattern.other_party_normalized == other_party.lower().strip()
            )

        result = await self.db.execute(query)
        return result.scalar_one_or_none()


async def create_initial_teachings(
    db_session: AsyncSession,
    skill_service: SkillLearningService
) -> None:
    """Create initial teaching entries for NZ rental tax rules.

    Args:
        db_session: Database session
        skill_service: Skill learning service
    """
    teachings = [
        {
            "title": "Interest Deductibility Rules",
            "content": """
Interest on loans for rental properties:
- New builds (CCC after 27 March 2020): 100% deductible
- Existing properties (FY24/25): 80% deductible
- Existing properties (FY25/26): 100% deductible
Always check the property type and year when calculating interest deductibility.
""",
            "keywords": ["interest", "deductibility", "loan", "mortgage", "new build"],
            "category_code": "interest"
        },
        {
            "title": "Property Management Fees",
            "content": """
Property management fees are fully deductible expenses.
Common property managers in NZ: Quinovic, Harcourts, Ray White, Barfoot & Thompson.
These should be categorized as 'property_management' and are 100% deductible.
""",
            "keywords": ["property", "management", "quinovic", "harcourts", "ray white"],
            "category_code": "property_management"
        },
        {
            "title": "Body Corporate Fees",
            "content": """
Body corporate fees for investment properties are fully deductible.
These typically appear as regular payments to body corporate entities.
Categorize as 'body_corporate' - 100% deductible operating expense.
""",
            "keywords": ["body", "corporate", "strata", "owners", "corporation"],
            "category_code": "body_corporate"
        },
        {
            "title": "Insurance Requirements",
            "content": """
Only landlord insurance is deductible for rental properties.
Home and contents insurance is NOT deductible (personal use).
Check policy type carefully - must be specifically landlord/rental insurance.
""",
            "keywords": ["insurance", "landlord", "rental", "home", "contents"],
            "category_code": "insurance"
        },
        {
            "title": "Council Rates",
            "content": """
Council rates for rental properties are fully deductible.
Usually paid quarterly to local councils (Auckland Council, Wellington City, etc.).
Categorize as 'rates' - 100% deductible operating expense.
""",
            "keywords": ["council", "rates", "auckland", "wellington", "city"],
            "category_code": "rates"
        }
    ]

    for teaching in teachings:
        try:
            await skill_service.create_teaching(
                skill_name='nz_rental_returns',
                title=teaching["title"],
                content=teaching["content"],
                keywords=teaching.get("keywords"),
                category_code=teaching.get("category_code"),
                created_by="system_init"
            )
            logger.info(f"Created teaching: {teaching['title']}")
        except Exception as e:
            logger.error(f"Failed to create teaching {teaching['title']}: {e}")