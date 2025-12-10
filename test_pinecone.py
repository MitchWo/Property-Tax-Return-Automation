#!/usr/bin/env python3
"""Test Pinecone connection."""
import asyncio
import sys
from app.services.knowledge_store import knowledge_store

async def test_pinecone():
    """Test Pinecone connection and operations."""
    print("🧪 Testing Pinecone Connection...")
    print("-" * 50)
    
    if not knowledge_store.enabled:
        print("❌ Pinecone is not configured!")
        print("\nPlease add your Pinecone credentials to the .env file:")
        print("  - PINECONE_API_KEY")
        print("  - PINECONE_INDEX_HOST")
        return False
    
    print("✅ Pinecone configuration detected")
    print(f"   Host: {knowledge_store.index_host}")
    print(f"   Namespace: {knowledge_store.namespace}")
    
    try:
        # Test storing a learning
        print("\n📝 Testing STORE operation...")
        record_id = await knowledge_store.store(
            content="Test learning: Bank statements from ASB should be classified as bank_statement",
            scenario="test_scenario",
            category="test_category",
            source="test"
        )
        if record_id:
            print(f"✅ Successfully stored test learning: {record_id}")
        else:
            print("❌ Failed to store learning")
            return False
        
        # Test searching
        print("\n🔍 Testing SEARCH operation...")
        await asyncio.sleep(2)  # Wait for indexing
        results = await knowledge_store.search(
            query="ASB bank statement classification",
            top_k=5
        )
        print(f"✅ Search returned {len(results)} results")
        
        # Test delete
        if record_id:
            print(f"\n🗑️  Testing DELETE operation...")
            deleted = await knowledge_store.delete(record_id)
            if deleted:
                print(f"✅ Successfully deleted test record")
            else:
                print("⚠️  Could not delete test record")
        
        print("\n" + "=" * 50)
        print("🎉 Pinecone connection test SUCCESSFUL!")
        print("=" * 50)
        return True
        
    except Exception as e:
        print(f"\n❌ Error during testing: {e}")
        return False

if __name__ == "__main__":
    success = asyncio.run(test_pinecone())
    sys.exit(0 if success else 1)
