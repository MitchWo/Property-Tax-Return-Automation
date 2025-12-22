"""
Script to re-index all existing skill learnings from database to Pinecone.
This will generate embeddings and store them in the Pinecone vector index.
"""

import asyncio
import logging
from datetime import datetime
from sqlalchemy import select
from app.database import AsyncSessionLocal
from app.models.db_models import SkillLearning
from app.services.phase2_feedback_learning.embeddings import embeddings_service
from app.services.phase2_feedback_learning.knowledge_store import knowledge_store

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def reindex_all_learnings():
    """Re-index all skill learnings from database to Pinecone."""

    async with AsyncSessionLocal() as db:
        # Get all active skill learnings
        result = await db.execute(
            select(SkillLearning)
            .where(SkillLearning.is_active == True)
            .order_by(SkillLearning.created_at.desc())
        )
        learnings = result.scalars().all()

        total = len(learnings)
        logger.info(f"Found {total} active learnings to re-index")

        if total == 0:
            logger.info("No learnings to index")
            return

        success_count = 0
        skip_count = 0
        error_count = 0

        for i, learning in enumerate(learnings, 1):
            try:
                # Skip if already has embedding_id (already indexed)
                if learning.embedding_id:
                    logger.info(f"[{i}/{total}] Skipping '{learning.title}' - already indexed")
                    skip_count += 1
                    continue

                logger.info(f"[{i}/{total}] Indexing: {learning.title}")

                # Prepare content for embedding
                content = f"{learning.title}\n{learning.content}"
                if learning.keywords:
                    content += f"\nKeywords: {', '.join(learning.keywords)}"

                # Prepare metadata for Pinecone
                metadata = {
                    "learning_id": str(learning.id),
                    "skill_name": learning.skill_name,
                    "learning_type": learning.learning_type,
                    "title": learning.title,
                    "content": learning.content[:1000],  # Truncate for metadata
                    "category_code": learning.category_code,
                    "confidence": learning.confidence,
                    "keywords": ', '.join(learning.keywords) if learning.keywords else '',
                    "applies_to": learning.applies_to,
                    "created_at": learning.created_at.isoformat() if learning.created_at else None,
                    "created_by": learning.created_by,
                    "times_applied": learning.times_applied,
                    "times_confirmed": learning.times_confirmed
                }

                # Add client_id if present
                if learning.client_id:
                    metadata["client_id"] = str(learning.client_id)

                # Store in Pinecone with OpenAI embedding
                vector_id = f"learning_{learning.id}"
                result = await knowledge_store.store(
                    content=content,
                    metadata=metadata,
                    vector_id=vector_id
                )

                if result:
                    # Update the database with embedding_id
                    learning.embedding_id = vector_id
                    await db.commit()
                    logger.info(f"  ✓ Successfully indexed with ID: {vector_id}")
                    success_count += 1
                else:
                    logger.warning(f"  ✗ Failed to store in Pinecone")
                    error_count += 1

            except Exception as e:
                logger.error(f"  ✗ Error indexing learning {learning.id}: {e}")
                error_count += 1
                continue

        logger.info("\n" + "="*50)
        logger.info("RE-INDEXING COMPLETE")
        logger.info(f"Total learnings: {total}")
        logger.info(f"Successfully indexed: {success_count}")
        logger.info(f"Skipped (already indexed): {skip_count}")
        logger.info(f"Errors: {error_count}")
        logger.info("="*50)

        # Test search to verify
        if success_count > 0:
            logger.info("\nTesting search functionality...")
            test_results = await knowledge_store.search(
                query="council rates insurance interest",
                top_k=5
            )
            logger.info(f"Search returned {len(test_results)} results")
            for r in test_results[:3]:
                logger.info(f"  - {r.get('content', '')[:100]}... (score: {r.get('score', 0):.2f})")


if __name__ == "__main__":
    asyncio.run(reindex_all_learnings())