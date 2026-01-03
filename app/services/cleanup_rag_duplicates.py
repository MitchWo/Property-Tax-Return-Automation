"""
RAG Duplicate Cleanup Script

Removes confirmed duplicate, empty, and test records from Pinecone namespaces.
Run with --dry-run to preview deletions without actually removing records.

Usage:
    poetry run python -m app.services.cleanup_rag_duplicates          # Actually delete
    poetry run python -m app.services.cleanup_rag_duplicates --dry-run # Preview only
"""

import asyncio
import argparse
import logging
import httpx

from app.services.phase2_feedback_learning.knowledge_store import knowledge_store

logger = logging.getLogger(__name__)

# ============================================================================
# RECORDS TO DELETE
# ============================================================================

# skill_learnings namespace - Exact duplicates (keep first, remove rest)
SKILL_LEARNINGS_DUPLICATES = [
    # Body Corporate duplicates (keeping learning_13ba8b44-b975-453d-bd02-d305802c9460)
    "learning_3c6ebeb9-9aea-47a2-ab1b-f14b797f127f",
    "learning_fa83f956-a13e-49ec-9c17-9649269eb1d3",
    "learning_eab777c3-2d37-4643-9b14-84d0ed3b93ae",
    "learning_8506d1c8-faa9-4c33-86db-f60dd28bad29",

    # Landlord Insurance duplicates (keeping learning_67d23f8e-3b71-494b-b13f-459aac897e32)
    "learning_67567ed2-88d5-4110-9b29-56413924b414",
    "learning_15218c6c-1592-4a9d-8d89-925316502b17",
    "learning_2188c9f5-ed0f-46d5-a58f-a4dd290d3a12",
    "learning_f0f5ee91-338c-48ab-8f01-b3952f52c97c",

    # Interest Deductibility duplicates (keeping learning_13672fea-3804-4cf8-bedb-6b7825c62e55)
    "learning_7fae9b13-b57f-497c-856d-81fb6de844b0",
    "learning_994747f1-7fda-42aa-a24a-6f1bde2e4a02",
    "learning_ab274c72-072f-41cd-a592-969d346d57ec",
    "learning_ce745c83-7a01-49a3-9dd8-fff8a6524865",

    # Test Insurance duplicate (keeping learning_15e22a12-f3a2-49fb-847b-a75dbf70dfd8)
    "learning_411ce4bd-05fb-4bd8-bf19-5f24ee29f667",
]

# skill_learnings namespace - Empty records (no content)
SKILL_LEARNINGS_EMPTY = [
    "learning_2e476524-372d-4bf5-8eb1-ab91fb8f23ec",
    "learning_6c8b7df9-3eb6-4d34-bce8-7044f8feaa6d",
    "learning_7263e4a0-c2e3-48b5-8975-ad96b2b659e2",
    "learning_10b68c41-1be0-4b1e-af3f-b04f582da0fb",
    "learning_2c7570a6-16f5-4985-aa22-49f5ca888c7c",
    "learning_2a178a4d-f12e-4016-a6c8-8e9e07349e5a",
    "learning_13c3df7c-f875-4fb2-892f-034653cc3042",
    "learning_0665a9da-de55-485a-addf-dadd69c3ef38",
    "learning_3bf056da-98e2-4da9-89d8-a7f0ee66b4f6",
    "learning_0047abc9-e881-48b1-9495-e48fd611e982",
    "learning_08c266f7-6dd0-40d2-95e4-3fcfbe2cbe06",
    "learning_75d6f339-caf8-464c-82e1-289d37e51cda",
    "learning_4e05ab46-9760-4ce4-908e-5423aa25fa39",
    "learning_70bf8f03-1fac-4dcb-8e7b-4dbda3fffdc4",
    "learning_4dc95c37-7008-4d0a-9ef6-1e14b3fa6539",
    "learning_d53cfbdc-07e9-42be-b12e-801b627954c8",
    "learning_bc1a8a5f-1136-4fb4-bd21-1703bdc568e1",
    "learning_6fca192e-9561-4b91-8cca-edc5c9fc41ce",
    "learning_bea0ef80-7aee-47c0-810d-a91e0e84ae06",
    "learning_dd91c5c3-efea-4fc6-90e4-ba92d884c7e9",
    "learning_dd73c3f6-639e-4e86-bc74-8a472bbe4284",
    "learning_c5cdd7ed-1d82-434e-bbdb-90729806c24a",
    "learning_cd45de95-3b25-4c7a-bfef-b558263ce967",
    "learning_e0f22326-f56c-4850-8ccf-272305b39d86",
    "learning_8813ce4c-ccc9-427a-9b22-871873c76d7f",
    "learning_919c7f31-1f37-453e-a9ae-dec2b66fb0d0",
    "learning_d0d460eb-220b-490f-82b6-09792a1c6363",
    "learning_82bd3983-d0b8-4b5e-869d-7818d5c4fd32",
    "learning_84390425-ad78-4e88-a46e-d3b86257c22b",
    "learning_7b258037-28e0-4e5c-af61-2b0946ae938d",
    "learning_765e1eb7-0f87-4f7b-8343-5f680c76c532",
    "learning_0c379766-bc6a-4ebe-b9ee-1adf50377c66",
    "learning_48483ae1-81eb-49ea-865e-749a11bd1cf3",
]

# skill_learnings namespace - Test records
SKILL_LEARNINGS_TEST = [
    "test_vector_1",
]

# document-review namespace - Exact duplicates
DOCUMENT_REVIEW_DUPLICATES = [
    # PAY R CHAND duplicate (keeping e63bd445915dcb4760ada12b0cd02812)
    "a24d0a1f581b869169763a03f4313056",
]

# transaction-coding namespace - Exact duplicates
TRANSACTION_CODING_DUPLICATES = [
    # FROM R R CHAND - 35 copies, keep 99b79a13c0cecf63bba6f30f9f0eb5a6, remove 34
    "4a1603bcc13091a7feeb7ceb2e24c90a",
    "65a48997330953bf8d0a06def41e8fbf",
    "5f8bb701ae3a3caf6959319da30a6699",
    "1689a193d8998560bc3435d0778e0500",
    "86df4987f8e596b464b3eaad4820ba01",
    "94c191a26321633ec171df573ee1b1a9",
    "816a4a5b94f85251c84ad0fe5e05dcc6",
    "1ecc67f73638e514dc7590c5d401997d",
    "03980e7d663d18f94b495913fea9f93c",
    "21d2893ec08a06a8d83217c5a55c2864",
    "7017ce86d1b8f74dd244d592158936bb",
    "763729234dd1ce1f790e84e909bb93b6",
    "5a568298011a79665a7a4b72711caa6b",
    "6ad55d3de730f5b14406427f76ed91b4",
    "3a4850e83c1ee4192dd49fc790349c71",
    "9928e1c0550e66edab61f89a75617b81",
    "59148ee0d8b2d047580c93f036a74f1d",
    "98b72d96e6e7fb45cfe8b015bd2f82fb",
    "3b9f92c255abc3044e21693064067902",
    "bdb9e8ffca91c085f7f4301ad06014aa",
    "e843a08d27adeed2fd9433c5032e3250",
    "a2dd6c277a494123103c002054275f36",
    "efb8c54586806cfe11c611081defe14e",
    "99c3755eb0b961e9ef845fd74b85c9c9",
    "f24b3cf39378a3e7d5249f473ba9d21e",
    "e1d9fce7fbccf8aaef95372ea17dc45f",
    "9e407c0fb860804a42e7ff9dd9fbb575",
    "b0ef2bb8c6bad247bbcacfc0a6a7b649",
    "f47ae5fa510327cdff0bede74ab64359",
    "eb2915e2d54555a7eae0481886b46ecf",
    "d413c11f468e9d67da2a8708c956cf80",
    "ef1a589057113193dadfef9a9fd401cd",
    "a032791730a7e6d97cfd04ee83b89777",
    "b547e116bbfe7812f2f453a1340ed86b",

    # TO R R CHAND - 35 copies, keep 2678fff64d5a2ff8d470b474c432dd02, remove 34
    "754afbe83bd143c729beaf4b085a8647",
    "214be2b024e464f6f0cd2d0b81369d38",
    "3f7adb966e269c4d82896daafd7652f0",
    "66463171bbf9a1406d473966d4cd7fb5",
    "16f431c32faa33e0e257d3b3e4d1f0b6",
    "7fd863fce7be31cfe9ba8009241baed2",
    "0d3872bc544bc1f44da29409481711e4",
    "48328841035dfbb507090cc01a344a7e",
    "62d910c865e77df5efd2624d48aba86b",
    "0246df2ab0ef885f41111ec4c1d0b1ca",
    "24ad482b0997cbf885731fbb08934e2d",
    "854778f60be70e4d7b57942857b21f6c",
    "7d21347fc1a40ee9e08ed34e95cb8630",
    "6114dd072e73cc3bfc1991837fbc30b2",
    "4638068a754e91ad9eb7fea59e715145",
    "2f32c7dbd73f31b4330168c4ab55a777",
    "55ce7204e38fae6d1e0042f4dfd94ee8",
    "413bfa1d1b0a3ee64232ae659d3fdf97",
    "c45002a7f31b198ef00d2e4bd6c72b09",
    "a7c99f693de4de84193598d81ba01c27",
    "e91bcecd22c4f69e3d37e61f48d30668",
    "ef60652ad0387d625400151e2aa42573",
    "f2fab4d96c8043b8278c94d0ca0618b1",
    "e9cbbff9469b4b476af8ffb17f28d1e8",
    "b9cb820309af7ce2d5e03f47255d9ccc",
    "a568f9134b8cf20f06dd706d76e067a0",
    "e03e371a47a6381c5d37735f836f0793",
    "e4addfa00f6a3469851923ca167e2483",
    "faf8d8617dbbf222326f613267cb2b07",
    "b40550c8a01af42febe6526f1cf67603",
    "b15da41f066bb122c44394c56805e6d0",
    "bd0b636440b4872eba8896f355477dda",
    "c43dbc73da6997be1bd824f422bb9839",
    "e376dca1287aa9761aaafd468bf4e0e0",

    # Bill Payment CHAND $1 - 2 copies, keep 05e61e05d93fde0151eeb3c72b1fa8fa, remove 1
    "8d5a64341a329a1786743b948c965ebd",

    # Unknown $6 - 2 copies, keep 1206616ec71d95a5372e18a2a5670bfd, remove 1
    "af89f9b363bbb86671f7bd81d899e7b0",

    # Bill Payment CHAND $900 - 2 copies, keep 83428567e28a7c415524cc25091df4a3, remove 1
    "f24fedea6ce21187cf320f1070b9cd94",
]


async def delete_records(namespace: str, record_ids: list, dry_run: bool = False) -> dict:
    """
    Delete records from a Pinecone namespace.

    Args:
        namespace: Pinecone namespace
        record_ids: List of vector IDs to delete
        dry_run: If True, only preview deletions

    Returns:
        Dict with success/failure counts
    """
    if not knowledge_store.enabled:
        print("ERROR: Pinecone not configured")
        return {"success": 0, "failed": 0}

    if not record_ids:
        return {"success": 0, "failed": 0}

    headers = {
        "Api-Key": knowledge_store.api_key,
        "Content-Type": "application/json",
        "X-Pinecone-API-Version": knowledge_store.api_version,
    }

    if dry_run:
        print(f"  [DRY RUN] Would delete {len(record_ids)} records from {namespace}")
        for rid in record_ids:
            print(f"    - {rid}")
        return {"success": len(record_ids), "failed": 0, "dry_run": True}

    # Delete in batches of 100 (Pinecone limit)
    batch_size = 100
    success = 0
    failed = 0

    async with httpx.AsyncClient(timeout=60.0) as client:
        for i in range(0, len(record_ids), batch_size):
            batch = record_ids[i:i + batch_size]

            url = f"https://{knowledge_store.index_host}/vectors/delete"
            payload = {
                "ids": batch,
                "namespace": namespace
            }

            try:
                response = await client.post(url, json=payload, headers=headers)
                response.raise_for_status()
                success += len(batch)
                print(f"  Deleted {len(batch)} records from {namespace}")
            except Exception as e:
                failed += len(batch)
                print(f"  ERROR deleting batch from {namespace}: {e}")

    return {"success": success, "failed": failed}


async def run_cleanup(dry_run: bool = False):
    """Run the full cleanup process."""

    print("\n" + "=" * 60)
    print("RAG DUPLICATE CLEANUP SCRIPT")
    print("=" * 60)

    if dry_run:
        print("\n*** DRY RUN MODE - No records will be deleted ***\n")

    if not knowledge_store.enabled:
        print("\nERROR: Pinecone is not configured.")
        return

    # Summary of what will be deleted
    total_skill_learnings = (
        len(SKILL_LEARNINGS_DUPLICATES) +
        len(SKILL_LEARNINGS_EMPTY) +
        len(SKILL_LEARNINGS_TEST)
    )
    total_document_review = len(DOCUMENT_REVIEW_DUPLICATES)
    total_transaction_coding = len(TRANSACTION_CODING_DUPLICATES)
    total = total_skill_learnings + total_document_review + total_transaction_coding

    print(f"Records to delete: {total}")
    print(f"  - skill_learnings duplicates: {len(SKILL_LEARNINGS_DUPLICATES)}")
    print(f"  - skill_learnings empty: {len(SKILL_LEARNINGS_EMPTY)}")
    print(f"  - skill_learnings test: {len(SKILL_LEARNINGS_TEST)}")
    print(f"  - document-review duplicates: {len(DOCUMENT_REVIEW_DUPLICATES)}")
    print(f"  - transaction-coding duplicates: {len(TRANSACTION_CODING_DUPLICATES)}")

    results = {}

    # Delete from skill_learnings
    print(f"\n[1/3] Cleaning skill_learnings namespace...")

    all_skill_learnings = (
        SKILL_LEARNINGS_DUPLICATES +
        SKILL_LEARNINGS_EMPTY +
        SKILL_LEARNINGS_TEST
    )
    results["skill_learnings"] = await delete_records(
        "skill_learnings",
        all_skill_learnings,
        dry_run
    )

    # Delete from document-review
    print(f"\n[2/3] Cleaning document-review namespace...")
    results["document-review"] = await delete_records(
        "document-review",
        DOCUMENT_REVIEW_DUPLICATES,
        dry_run
    )

    # Delete from transaction-coding
    print(f"\n[3/3] Cleaning transaction-coding namespace...")
    results["transaction-coding"] = await delete_records(
        "transaction-coding",
        TRANSACTION_CODING_DUPLICATES,
        dry_run
    )

    # Summary
    print("\n" + "=" * 60)
    print("CLEANUP COMPLETE" if not dry_run else "DRY RUN COMPLETE")
    print("=" * 60)

    total_success = sum(r["success"] for r in results.values())
    total_failed = sum(r["failed"] for r in results.values())

    print(f"\nResults:")
    for namespace, result in results.items():
        status = "OK" if result["failed"] == 0 else "ERRORS"
        print(f"  {namespace}: {result['success']} deleted, {result['failed']} failed [{status}]")

    print(f"\nTotal: {total_success} deleted, {total_failed} failed")

    if dry_run:
        print("\nTo actually delete these records, run without --dry-run flag")

    return results


def main():
    parser = argparse.ArgumentParser(description="Clean up duplicate RAG records")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview deletions without actually removing records"
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format='%(message)s')
    asyncio.run(run_cleanup(dry_run=args.dry_run))


if __name__ == "__main__":
    main()
