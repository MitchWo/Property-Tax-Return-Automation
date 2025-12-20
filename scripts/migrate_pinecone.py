"""
LEGACY MIGRATION SCRIPT - Migration completed.

This script was used to migrate records from the old phase1-feedback index (1024-dim Llama)
to the skill-learnings index (1536-dim OpenAI). Migration is now complete and all systems
use the skill-learnings index exclusively.

Original purpose:
1. Fetches all records from source index
2. Re-embeds the content using OpenAI text-embedding-3-small
3. Upserts to skill-learnings index preserving namespaces and metadata
"""

import os
import time
from dotenv import load_dotenv
from pinecone import Pinecone
from openai import OpenAI

load_dotenv()

# Initialize clients
pc = Pinecone(api_key=os.getenv('PINECONE_API_KEY'))
openai_client = OpenAI(api_key=os.getenv('OPENAI_API_KEY'))

# Source and target indexes
source_index = pc.Index(host=os.getenv('PINECONE_INDEX_HOST'))
target_index = pc.Index(host=os.getenv('PINECONE_OPENAI_INDEX_HOST'))

# Namespaces to migrate
NAMESPACES = [
    'common-errors',
    'document-review',
    'workbook-structure',
    'pnl-mapping',
    'gst-rules',
    'tax-rules',
    'transaction-coding'
]


def get_openai_embedding(text: str) -> list[float]:
    """Generate OpenAI embedding for text."""
    response = openai_client.embeddings.create(
        model="text-embedding-3-small",
        input=text,
        dimensions=1536
    )
    return response.data[0].embedding


def fetch_all_vectors_from_namespace(namespace: str) -> list[dict]:
    """Fetch all vectors from a namespace using list + fetch."""
    vectors = []

    # Use list to get all vector IDs
    try:
        all_ids = []
        for ids_batch in source_index.list(namespace=namespace):
            all_ids.extend(ids_batch)

        if not all_ids:
            print(f"  No vectors found in {namespace}")
            return vectors

        # Fetch vectors in batches of 100
        batch_size = 100
        for i in range(0, len(all_ids), batch_size):
            batch_ids = all_ids[i:i+batch_size]
            fetch_response = source_index.fetch(ids=batch_ids, namespace=namespace)

            for vec_id, vec_data in fetch_response.vectors.items():
                vectors.append({
                    'id': vec_id,
                    'metadata': vec_data.metadata or {}
                })

        return vectors

    except Exception as e:
        print(f"  Error listing vectors: {e}")
        # Fallback: use query with zero vector to get samples
        # This is less reliable but works as backup
        results = source_index.query(
            vector=[0.0] * 1024,
            top_k=10000,
            namespace=namespace,
            include_metadata=True
        )
        for match in results.matches:
            vectors.append({
                'id': match.id,
                'metadata': match.metadata or {}
            })
        return vectors


def migrate_namespace(namespace: str) -> tuple[int, int]:
    """Migrate all vectors in a namespace. Returns (success_count, error_count)."""
    print(f"\n{'='*50}")
    print(f"Migrating namespace: {namespace}")
    print('='*50)

    # Fetch all vectors
    vectors = fetch_all_vectors_from_namespace(namespace)
    print(f"  Found {len(vectors)} vectors to migrate")

    if not vectors:
        return 0, 0

    success_count = 0
    error_count = 0
    batch_to_upsert = []

    for i, vec in enumerate(vectors):
        vec_id = vec['id']
        metadata = vec['metadata']

        # Get content for embedding
        content = metadata.get('content', '')
        if not content:
            print(f"  WARNING: No content for {vec_id}, skipping")
            error_count += 1
            continue

        try:
            # Generate new OpenAI embedding
            embedding = get_openai_embedding(content)

            batch_to_upsert.append({
                'id': vec_id,
                'values': embedding,
                'metadata': metadata
            })

            # Upsert in batches of 50
            if len(batch_to_upsert) >= 50:
                target_index.upsert(vectors=batch_to_upsert, namespace=namespace)
                success_count += len(batch_to_upsert)
                print(f"  Upserted batch: {success_count}/{len(vectors)}")
                batch_to_upsert = []
                time.sleep(0.1)  # Rate limiting

        except Exception as e:
            print(f"  ERROR processing {vec_id}: {e}")
            error_count += 1

    # Upsert remaining vectors
    if batch_to_upsert:
        target_index.upsert(vectors=batch_to_upsert, namespace=namespace)
        success_count += len(batch_to_upsert)
        print(f"  Upserted final batch: {success_count}/{len(vectors)}")

    print(f"  Completed: {success_count} success, {error_count} errors")
    return success_count, error_count


def verify_migration():
    """Verify migration completeness."""
    print("\n" + "="*50)
    print("VERIFICATION")
    print("="*50)

    source_stats = source_index.describe_index_stats()
    target_stats = target_index.describe_index_stats()

    print(f"\nSource (phase1-feedback): {source_stats.total_vector_count} vectors")
    print(f"Target (skill-learnings): {target_stats.total_vector_count} vectors")

    print("\nPer-namespace comparison:")
    print(f"{'Namespace':<25} {'Source':<10} {'Target':<10} {'Status'}")
    print("-" * 60)

    all_match = True
    for ns in NAMESPACES:
        source_count = source_stats.namespaces.get(ns, type('', (), {'vector_count': 0})()).vector_count if ns in source_stats.namespaces else 0
        target_count = target_stats.namespaces.get(ns, type('', (), {'vector_count': 0})()).vector_count if ns in target_stats.namespaces else 0

        # Handle the namespace stats properly
        if hasattr(source_stats.namespaces.get(ns), 'vector_count'):
            source_count = source_stats.namespaces[ns].vector_count
        elif ns in source_stats.namespaces:
            source_count = source_stats.namespaces[ns].vector_count
        else:
            source_count = 0

        if hasattr(target_stats.namespaces.get(ns), 'vector_count'):
            target_count = target_stats.namespaces[ns].vector_count
        elif ns in target_stats.namespaces:
            target_count = target_stats.namespaces[ns].vector_count
        else:
            target_count = 0

        status = "✓" if source_count == target_count else "✗ MISMATCH"
        if source_count != target_count:
            all_match = False
        print(f"{ns:<25} {source_count:<10} {target_count:<10} {status}")

    return all_match


def main():
    print("="*50)
    print("PINECONE MIGRATION: phase1-feedback → skill-learnings")
    print("="*50)
    print(f"Source: {os.getenv('PINECONE_INDEX_HOST')}")
    print(f"Target: {os.getenv('PINECONE_OPENAI_INDEX_HOST')}")
    print(f"Embedding model: text-embedding-3-small (1536 dims)")

    total_success = 0
    total_errors = 0

    for namespace in NAMESPACES:
        success, errors = migrate_namespace(namespace)
        total_success += success
        total_errors += errors

    print("\n" + "="*50)
    print("MIGRATION COMPLETE")
    print("="*50)
    print(f"Total migrated: {total_success}")
    print(f"Total errors: {total_errors}")

    # Wait for indexing
    print("\nWaiting 5s for indexing...")
    time.sleep(5)

    # Verify
    all_match = verify_migration()

    if all_match:
        print("\n✓ Migration verified successfully!")
    else:
        print("\n✗ Migration has discrepancies - please review")


if __name__ == "__main__":
    main()
