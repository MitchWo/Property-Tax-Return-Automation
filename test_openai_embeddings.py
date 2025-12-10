#!/usr/bin/env python3
"""Test OpenAI embeddings integration with Pinecone."""
import asyncio
import os
from dotenv import load_dotenv

load_dotenv()

async def test_embeddings():
    """Test OpenAI embeddings and Pinecone integration."""
    print("🧪 Testing OpenAI Embeddings Integration...")
    print("-" * 50)

    # Check configuration
    openai_key = os.getenv("OPENAI_API_KEY", "")
    if not openai_key:
        print("❌ OpenAI API key not configured!")
        print("\nTo enable OpenAI embeddings:")
        print("1. Get your API key from: https://platform.openai.com/api-keys")
        print("2. Add to .env file: OPENAI_API_KEY=sk-...")
        print("\nCurrently using random vectors as fallback.")
        return

    print("✅ OpenAI API key detected")

    # Import after env is loaded
    from app.services.embeddings import embeddings_service
    from app.services.knowledge_store import knowledge_store

    print(f"   Model: {embeddings_service.model}")
    print(f"   Dimensions: {embeddings_service.dimensions}")
    print(f"   Pinecone: {'✅ Enabled' if knowledge_store.enabled else '❌ Disabled'}")

    if not embeddings_service.enabled:
        print("❌ Embeddings service not enabled")
        return

    # Test embedding generation
    print("\n📝 Testing embedding generation...")
    test_text = "Insurance policies labeled as 'home and contents' should be flagged as personal insurance, not landlord insurance."

    embedding = await embeddings_service.embed_text(test_text)
    if embedding:
        print(f"✅ Generated embedding with {len(embedding)} dimensions")
    else:
        print("❌ Failed to generate embedding")
        return

    # Test storing with real embeddings
    print("\n📦 Testing store with OpenAI embeddings...")
    record_id = await knowledge_store.store(
        content=test_text,
        scenario="test_openai_embeddings",
        category="insurance_classification",
        source="test"
    )

    if record_id:
        print(f"✅ Stored learning with OpenAI embedding: {record_id}")
    else:
        print("❌ Failed to store learning")
        return

    # Test searching with real embeddings
    print("\n🔍 Testing search with OpenAI embeddings...")
    await asyncio.sleep(2)  # Wait for indexing

    search_query = "home contents insurance landlord"
    results = await knowledge_store.search(
        query=search_query,
        top_k=5,
        min_score=0.0
    )

    print(f"✅ Search returned {len(results)} results")
    if results:
        print("\nTop results:")
        for i, result in enumerate(results[:3], 1):
            print(f"  {i}. Score: {result['score']:.3f}")
            print(f"     Scenario: {result['scenario']}")
            print(f"     Content: {result['content'][:100]}...")

    # Cleanup
    if record_id and results:
        print(f"\n🗑️ Cleaning up test record...")
        deleted = await knowledge_store.delete(results[0]['id'])
        if deleted:
            print("✅ Test record deleted")

    print("\n" + "=" * 50)
    print("🎉 OpenAI Embeddings Integration Test Complete!")
    print("=" * 50)
    print("\nSemantic search is now enabled!")
    print("Your feedback will be more accurately matched.")

if __name__ == "__main__":
    asyncio.run(test_embeddings())