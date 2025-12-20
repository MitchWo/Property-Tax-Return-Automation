"""Knowledge store service for RAG using Pinecone with OpenAI embeddings."""

import asyncio
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

import httpx

from app.config import settings
from app.models.db_models import local_now
from app.services.phase2_feedback_learning.embeddings import embeddings_service

logger = logging.getLogger(__name__)

# All available Pinecone namespaces (8 total)
ALL_NAMESPACES = [
    "skill_learnings",      # Domain knowledge, teachings, and general patterns
    "common-errors",        # Common error patterns and corrections
    "document-review",      # Document classification learnings
    "workbook-structure",   # Workbook/spreadsheet structure knowledge
    "pnl-mapping",          # P&L row mapping knowledge
    "gst-rules",            # GST rules and treatment
    "tax-rules",            # Tax deductibility and treatment rules
    "transaction-coding",   # Transaction categorization patterns
]


class KnowledgeStore:
    """Store and retrieve learnings from Pinecone vector database."""

    def __init__(self):
        """Initialize knowledge store."""
        self.api_key = settings.PINECONE_API_KEY
        self.index_host = settings.PINECONE_INDEX_HOST
        self.namespace = settings.PINECONE_NAMESPACE
        self.api_version = "2024-10"

        if not self.api_key or not self.index_host:
            logger.warning("Pinecone not configured - knowledge store disabled")
            self.enabled = False
        else:
            self.enabled = True

        # Check if OpenAI embeddings are available
        self.use_openai_embeddings = embeddings_service.enabled
        if self.use_openai_embeddings:
            logger.info("Using OpenAI embeddings for semantic search")
        else:
            logger.info("Using random vectors (OpenAI not configured)")

    async def search(
        self, query: str, top_k: int = None, min_score: float = None, namespace: str = None
    ) -> List[Dict[str, Any]]:
        """
        Search for relevant learnings.

        Args:
            query: Search query text
            top_k: Number of results to return
            min_score: Minimum relevance score threshold
            namespace: Pinecone namespace to search (defaults to configured namespace)

        Returns:
            List of relevant learnings with scores
        """
        if not self.enabled:
            return []

        top_k = top_k or settings.KNOWLEDGE_TOP_K
        min_score = min_score or settings.KNOWLEDGE_RELEVANCE_THRESHOLD
        namespace = namespace or self.namespace

        try:
            # Generate query embedding
            if self.use_openai_embeddings:
                query_vector = await embeddings_service.embed_text(query)
                if not query_vector:
                    logger.warning("Failed to generate query embedding, using random vector")
                    import random

                    query_vector = [random.random() for _ in range(1024)]
            else:
                # Use random vector as fallback
                import random

                query_vector = [random.random() for _ in range(1024)]

            url = f"https://{self.index_host}/query"

            payload = {
                "namespace": namespace,
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
                        }
                    )

            logger.info(f"Knowledge search returned {len(learnings)} relevant results")
            return learnings

        except Exception as e:
            logger.error(f"Error searching knowledge base: {e}")
            return []

    async def _search_with_embedding(
        self,
        query_vector: List[float],
        top_k: int = None,
        min_score: float = None,
        namespace: str = None
    ) -> List[Dict[str, Any]]:
        """
        Search using a pre-computed embedding vector.

        This avoids regenerating embeddings when searching multiple namespaces.
        """
        if not self.enabled:
            return []

        top_k = top_k or settings.KNOWLEDGE_TOP_K
        min_score = min_score or settings.KNOWLEDGE_RELEVANCE_THRESHOLD
        namespace = namespace or self.namespace

        try:
            url = f"https://{self.index_host}/query"

            payload = {
                "namespace": namespace,
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
                        }
                    )

            return learnings

        except Exception as e:
            logger.error(f"Error searching with embedding in namespace '{namespace}': {e}")
            return []

    async def store(
        self, content: str, scenario: str, category: str, source: str = "user_feedback",
        namespace: str = None
    ) -> Optional[str]:
        """
        Store a new learning with OpenAI embedding.

        Args:
            content: The learning content
            scenario: Scenario identifier (snake_case)
            category: Category for organization
            source: Source of learning ("user_feedback" or "agent_learning")
            namespace: Pinecone namespace to store in (defaults to self.namespace)
                       Common namespaces: "document-review", "transaction-coding"

        Returns:
            ID of stored record, or None if failed
        """
        if not self.enabled:
            logger.warning("Cannot store learning - Pinecone not configured")
            return None

        try:
            record_id = f"{source}_{int(datetime.now().timestamp() * 1000)}"

            # Prepare metadata
            metadata = {
                "content": content[:1000],  # Truncate if too long
                "scenario": scenario.lower().replace(" ", "_"),
                "category": category.lower().replace(" ", "_"),
                "created_at": local_now().isoformat(),
                "source": source,
                "record_id": record_id,
            }

            # Generate embedding
            if self.use_openai_embeddings:
                vector = await embeddings_service.embed_text(content)
                if not vector:
                    logger.warning("Failed to generate embedding, using random vector")
                    import random

                    vector = [random.random() for _ in range(1024)]
            else:
                # Use random vector as fallback
                import random

                vector = [random.random() for _ in range(1024)]

            # Create a simple hash for the ID
            import hashlib

            vector_id = hashlib.md5(f"{content}_{datetime.now().isoformat()}".encode()).hexdigest()

            url = f"https://{self.index_host}/vectors/upsert"

            payload = {
                "vectors": [{"id": vector_id, "values": vector, "metadata": metadata}],
                "namespace": namespace or self.namespace,
            }

            headers = {"Api-Key": self.api_key, "Content-Type": "application/json"}

            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(url, json=payload, headers=headers)
                response.raise_for_status()

            logger.info(f"Stored learning: {record_id} ({scenario}/{category})")
            return record_id

        except Exception as e:
            logger.error(f"Error storing learning: {e}")
            return None

    async def list_learnings(self, limit: int = 100) -> List[Dict[str, Any]]:
        """
        List all learnings from the namespace.

        Uses Pinecone list and fetch APIs to get all vectors with metadata.
        """
        if not self.enabled:
            return []

        try:
            headers = {"Api-Key": self.api_key, "X-Pinecone-API-Version": self.api_version}

            # Step 1: List all vector IDs in the namespace
            all_ids = []
            pagination_token = None

            async with httpx.AsyncClient(timeout=30.0) as client:
                while True:
                    list_url = f"https://{self.index_host}/vectors/list"
                    params = {"namespace": self.namespace, "limit": 100}
                    if pagination_token:
                        params["paginationToken"] = pagination_token

                    response = await client.get(list_url, params=params, headers=headers)
                    response.raise_for_status()
                    result = response.json()

                    vectors = result.get("vectors", [])
                    for v in vectors:
                        all_ids.append(v.get("id"))

                    # Check for pagination
                    pagination = result.get("pagination", {})
                    pagination_token = pagination.get("next") if pagination else None

                    if not pagination_token or len(all_ids) >= limit:
                        break

            logger.info(f"Listed {len(all_ids)} vector IDs from namespace")

            if not all_ids:
                return []

            # Step 2: Fetch vectors with metadata in batches (max 100 per request)
            all_learnings = []
            batch_size = 100

            async with httpx.AsyncClient(timeout=30.0) as client:
                for i in range(0, len(all_ids), batch_size):
                    batch_ids = all_ids[i : i + batch_size]

                    # Build fetch URL with multiple ids query params
                    fetch_url = f"https://{self.index_host}/vectors/fetch"
                    params = [("namespace", self.namespace)]
                    for vid in batch_ids:
                        params.append(("ids", vid))

                    response = await client.get(fetch_url, params=params, headers=headers)
                    response.raise_for_status()
                    result = response.json()

                    vectors = result.get("vectors", {})
                    for vid, vdata in vectors.items():
                        metadata = vdata.get("metadata", {})
                        all_learnings.append(
                            {
                                "id": vid,
                                "score": 1.0,  # No score when fetching directly
                                "content": metadata.get("content", ""),
                                "scenario": metadata.get("scenario", ""),
                                "category": metadata.get("category", ""),
                                "created_at": metadata.get("created_at", ""),
                            }
                        )

            # Sort by created_at descending
            all_learnings.sort(key=lambda x: x.get("created_at", ""), reverse=True)

            logger.info(f"Fetched {len(all_learnings)} learnings with metadata")
            return all_learnings[:limit]

        except Exception as e:
            logger.error(f"Error listing learnings: {e}")
            return []

    async def delete(self, record_id: str) -> bool:
        """Delete a learning by ID."""
        if not self.enabled:
            return False

        try:
            url = f"https://{self.index_host}/vectors/delete"

            headers = {"Api-Key": self.api_key, "Content-Type": "application/json"}

            payload = {"ids": [record_id], "namespace": self.namespace}

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

    async def store_transaction_learning(
        self,
        vendor_name: str,
        transaction_description: str,
        amount: float,
        is_legitimate: bool,
        document_type: str = "bank_statement",
        notes: str = "",
    ) -> Optional[str]:
        """
        Store a transaction pattern learning.

        Args:
            vendor_name: Name of the vendor/payee
            transaction_description: Description of the transaction
            amount: Transaction amount
            is_legitimate: Whether this is a legitimate rental expense
            document_type: Type of document the transaction appeared in
            notes: Additional notes about why this is/isn't legitimate

        Returns:
            ID of stored record, or None if failed
        """
        if is_legitimate:
            content = (
                f"Transaction to '{vendor_name}' (${amount:.2f}) in {document_type} "
                f"is a LEGITIMATE rental property expense. Description: '{transaction_description}'. "
                f"Do NOT flag similar transactions to this vendor in future analyses. "
                f"{notes}"
            )
            scenario = "legitimate_rental_vendor"
        else:
            content = (
                f"Transaction to '{vendor_name}' (${amount:.2f}) in {document_type} "
                f"is NOT a legitimate rental expense and should be flagged. "
                f"Description: '{transaction_description}'. {notes}"
            )
            scenario = "flagged_transaction_pattern"

        return await self.store(
            content=content,
            scenario=scenario,
            category="transaction_analysis",
            source="user_feedback",
            namespace="transaction-coding",
        )

    async def search_transaction_patterns(
        self, vendor_name: str = "", transaction_description: str = "", top_k: int = 5
    ) -> List[Dict[str, Any]]:
        """
        Search for relevant transaction patterns.

        Args:
            vendor_name: Vendor name to search for
            transaction_description: Transaction description
            top_k: Number of results to return

        Returns:
            List of relevant transaction patterns
        """
        query = f"transaction {vendor_name} {transaction_description} rental property expense"
        return await self.search(
            query=query,
            top_k=top_k,
            min_score=0.5,  # Higher threshold for transaction patterns
            namespace="transaction-coding"  # Search the correct namespace for transaction patterns
        )

    async def search_all_namespaces(
        self,
        query: str,
        top_k: int = 5,
        min_score: float = 0.3,
        namespaces: Optional[List[str]] = None
    ) -> Dict[str, List[Dict[str, Any]]]:
        """
        Search across multiple namespaces for comprehensive knowledge retrieval.

        OPTIMIZED: Generates embedding ONCE and searches all namespaces in PARALLEL.

        All 8 namespaces available:
        - skill_learnings: General domain knowledge and teachings
        - common-errors: Common error patterns and corrections
        - document-review: Document classification learnings
        - workbook-structure: Workbook/spreadsheet structure knowledge
        - pnl-mapping: P&L row mapping knowledge
        - gst-rules: GST rules and treatment
        - tax-rules: Tax deductibility and treatment rules
        - transaction-coding: Transaction categorization patterns

        Args:
            query: Search query text
            top_k: Number of results per namespace
            min_score: Minimum relevance score threshold
            namespaces: Optional list of namespaces to search (defaults to ALL_NAMESPACES)

        Returns:
            Dict mapping namespace names to their search results
        """
        if not self.enabled:
            return {}

        # Default to all knowledge namespaces (all 8)
        if namespaces is None:
            namespaces = ALL_NAMESPACES

        # Generate embedding ONCE (uses cache if available)
        if self.use_openai_embeddings:
            query_vector = await embeddings_service.embed_text(query)
            if not query_vector:
                logger.warning("Failed to generate query embedding for multi-namespace search")
                return {}
        else:
            import random
            query_vector = [random.random() for _ in range(1024)]

        # Search all namespaces in PARALLEL using the same embedding
        async def search_namespace(ns: str) -> tuple[str, List[Dict[str, Any]]]:
            try:
                ns_results = await self._search_with_embedding(
                    query_vector=query_vector,
                    top_k=top_k,
                    min_score=min_score,
                    namespace=ns
                )
                return (ns, ns_results)
            except Exception as e:
                logger.warning(f"Failed to search namespace '{ns}': {e}")
                return (ns, [])

        # Execute all searches in parallel
        search_tasks = [search_namespace(ns) for ns in namespaces]
        search_results = await asyncio.gather(*search_tasks)

        # Collect results
        results = {}
        for ns, ns_results in search_results:
            if ns_results:
                results[ns] = ns_results
                logger.debug(f"Found {len(ns_results)} results in '{ns}' namespace")

        total_results = sum(len(r) for r in results.values())
        logger.info(f"Multi-namespace search returned {total_results} total results across {len(results)} namespaces (parallel, single embedding)")
        return results

    async def search_for_categorization(
        self,
        description: str,
        other_party: Optional[str] = None,
        top_k: int = 10
    ) -> List[Dict[str, Any]]:
        """
        Search all relevant namespaces for transaction categorization context.

        Combines results from (6 namespaces):
        - skill_learnings: Tax rules, teachings, and general patterns
        - transaction-coding: Specific transaction categorization learnings
        - tax-rules: Tax treatment rules and deductibility guidance
        - gst-rules: GST treatment and rules
        - pnl-mapping: P&L row mapping for categories
        - common-errors: Common categorization errors to avoid

        Args:
            description: Transaction description
            other_party: Transaction other party/vendor
            top_k: Total number of results to return

        Returns:
            Combined list of relevant learnings, sorted by score
        """
        query_parts = [description]
        if other_party:
            query_parts.append(other_party)
        query = f"transaction categorization {' '.join(query_parts)} rental property expense"

        # Search all relevant namespaces for categorization (6 namespaces)
        categorization_namespaces = [
            "skill_learnings",
            "transaction-coding",
            "tax-rules",
            "gst-rules",
            "pnl-mapping",
            "common-errors"
        ]
        all_results = await self.search_all_namespaces(
            query=query,
            top_k=max(2, top_k // len(categorization_namespaces)),  # Divide among namespaces
            min_score=0.35,
            namespaces=categorization_namespaces
        )

        # Combine and sort by score
        combined = []
        for namespace, results in all_results.items():
            for r in results:
                r["source_namespace"] = namespace
                combined.append(r)

        # Sort by score descending
        combined.sort(key=lambda x: x.get("score", 0), reverse=True)

        return combined[:top_k]

    async def search_tax_rules(
        self,
        query: str,
        property_type: Optional[str] = None,
        tax_year: Optional[str] = None,
        category: Optional[str] = None,
        top_k: int = 10
    ) -> List[Dict[str, Any]]:
        """
        Search tax-related namespaces for tax treatment guidance.

        OPTIMIZED: Single embedding, parallel searches across 3 namespaces.

        Searches across (3 namespaces):
        - tax-rules: Tax deductibility and treatment rules
        - gst-rules: GST treatment and rules
        - skill_learnings: Domain knowledge with tax guidance

        This covers:
        - Interest deductibility rules
        - Expense treatment rules
        - GST rules and treatment
        - Property type specific rules
        - Tax year specific rules

        Args:
            query: Search query (e.g., "interest deductibility new build")
            property_type: Optional filter by property type (new_build, existing)
            tax_year: Optional filter by tax year (e.g., "FY25")
            category: Optional filter by expense category
            top_k: Number of results to return

        Returns:
            List of relevant tax rules with scores
        """
        if not self.enabled:
            return []

        # Build comprehensive query
        query_parts = [query, "NZ rental property tax rule treatment"]
        if property_type:
            query_parts.append(property_type)
        if tax_year:
            query_parts.append(tax_year)
        if category:
            query_parts.append(category)

        full_query = " ".join(query_parts)

        # Generate embedding ONCE
        if self.use_openai_embeddings:
            query_vector = await embeddings_service.embed_text(full_query)
            if not query_vector:
                logger.warning("Failed to generate embedding for tax rules search")
                return []
        else:
            import random
            query_vector = [random.random() for _ in range(1024)]

        # Search all 3 namespaces in PARALLEL
        async def search_ns(ns: str, k: int, min_s: float) -> tuple[str, List[Dict[str, Any]]]:
            results = await self._search_with_embedding(
                query_vector=query_vector,
                top_k=k,
                min_score=min_s,
                namespace=ns
            )
            return (ns, results)

        search_tasks = [
            search_ns("tax-rules", top_k, 0.4),
            search_ns("gst-rules", top_k // 2, 0.4),
            search_ns("skill_learnings", top_k // 2, 0.5),
        ]
        search_results = await asyncio.gather(*search_tasks)

        # Collect and mark results
        results = []
        for ns, ns_results in search_results:
            for r in ns_results:
                r["source_namespace"] = ns
                # Filter skill_learnings to only tax-related content
                if ns == "skill_learnings":
                    content = r.get("content", "").lower()
                    if not any(term in content for term in ["deductible", "deductibility", "tax", "percentage", "treatment", "gst"]):
                        continue
                results.append(r)

        # Sort by score
        results.sort(key=lambda x: x.get("score", 0), reverse=True)

        return results[:top_k]

    async def get_tax_treatment_context(
        self,
        category_code: str,
        property_type: str,
        tax_year: str
    ) -> str:
        """
        Get formatted tax treatment context for a transaction category.

        Args:
            category_code: Transaction category code
            property_type: Property type (new_build, existing)
            tax_year: Tax year (e.g., "FY25")

        Returns:
            Formatted context string for tax rule application
        """
        results = await self.search_tax_rules(
            query=f"{category_code} treatment deductibility",
            property_type=property_type,
            tax_year=tax_year,
            category=category_code,
            top_k=5
        )

        if not results:
            return ""

        context_parts = [f"Tax treatment guidance for '{category_code}':"]

        for result in results:
            content = result.get("content", "")
            score = result.get("score", 0)
            source = result.get("source_namespace", "tax-rules")

            if content:
                context_parts.append(f"- [{source}] (relevance: {score:.0%}) {content[:200]}")

        return "\n".join(context_parts)

    async def search_gst_rules(
        self,
        query: str,
        category: Optional[str] = None,
        top_k: int = 10
    ) -> List[Dict[str, Any]]:
        """
        Search the gst-rules namespace for GST treatment guidance.

        Args:
            query: Search query (e.g., "rental income GST treatment")
            category: Optional expense/income category
            top_k: Number of results to return

        Returns:
            List of relevant GST rules with scores
        """
        if not self.enabled:
            return []

        query_parts = [query, "NZ GST treatment rental property"]
        if category:
            query_parts.append(category)

        full_query = " ".join(query_parts)

        results = await self.search(
            query=full_query,
            top_k=top_k,
            min_score=0.4,
            namespace="gst-rules"
        )

        for r in results:
            r["source_namespace"] = "gst-rules"

        return results

    async def search_pnl_mapping(
        self,
        category_code: str,
        description: Optional[str] = None,
        top_k: int = 5
    ) -> List[Dict[str, Any]]:
        """
        Search the pnl-mapping namespace for P&L row mapping guidance.

        Args:
            category_code: Transaction category code
            description: Optional transaction description for context
            top_k: Number of results to return

        Returns:
            List of relevant P&L mapping guidance
        """
        if not self.enabled:
            return []

        query_parts = [category_code, "P&L row mapping rental property expense"]
        if description:
            query_parts.append(description)

        full_query = " ".join(query_parts)

        results = await self.search(
            query=full_query,
            top_k=top_k,
            min_score=0.4,
            namespace="pnl-mapping"
        )

        for r in results:
            r["source_namespace"] = "pnl-mapping"

        return results

    async def search_common_errors(
        self,
        context: str,
        error_type: Optional[str] = None,
        top_k: int = 5
    ) -> List[Dict[str, Any]]:
        """
        Search the common-errors namespace for known error patterns.

        Args:
            context: Context of the operation (e.g., "categorizing insurance expense")
            error_type: Optional error type filter
            top_k: Number of results to return

        Returns:
            List of relevant error patterns to avoid
        """
        if not self.enabled:
            return []

        query_parts = [context, "common error mistake avoid rental property NZ tax"]
        if error_type:
            query_parts.append(error_type)

        full_query = " ".join(query_parts)

        results = await self.search(
            query=full_query,
            top_k=top_k,
            min_score=0.35,
            namespace="common-errors"
        )

        for r in results:
            r["source_namespace"] = "common-errors"

        return results

    async def search_workbook_structure(
        self,
        query: str,
        document_type: Optional[str] = None,
        top_k: int = 5
    ) -> List[Dict[str, Any]]:
        """
        Search the workbook-structure namespace for spreadsheet/workbook knowledge.

        Args:
            query: Search query (e.g., "bank statement column mapping")
            document_type: Optional document type filter
            top_k: Number of results to return

        Returns:
            List of relevant workbook structure guidance
        """
        if not self.enabled:
            return []

        query_parts = [query, "workbook spreadsheet structure rental property"]
        if document_type:
            query_parts.append(document_type)

        full_query = " ".join(query_parts)

        results = await self.search(
            query=full_query,
            top_k=top_k,
            min_score=0.35,
            namespace="workbook-structure"
        )

        for r in results:
            r["source_namespace"] = "workbook-structure"

        return results

    async def search_for_document_processing(
        self,
        document_type: Optional[str] = None,
        context: Optional[str] = None,
        top_k: int = 10
    ) -> List[Dict[str, Any]]:
        """
        Search all relevant namespaces for document processing context.

        Combines results from (5 namespaces):
        - skill_learnings: Domain knowledge and tax rules
        - document-review: Document classification patterns
        - common-errors: Common document processing errors to avoid
        - workbook-structure: Workbook/spreadsheet structure knowledge
        - tax-rules: Tax rules relevant to document validation

        Args:
            document_type: Type of document being processed
            context: Additional context (e.g., client name, property address)
            top_k: Total number of results to return

        Returns:
            Combined list of relevant learnings, sorted by score
        """
        query_parts = ["document classification rental property NZ tax"]
        if document_type:
            query_parts.append(document_type)
        if context:
            query_parts.append(context)
        query = " ".join(query_parts)

        # Search relevant namespaces for document processing (5 namespaces)
        document_namespaces = [
            "skill_learnings",
            "document-review",
            "common-errors",
            "workbook-structure",
            "tax-rules"
        ]
        all_results = await self.search_all_namespaces(
            query=query,
            top_k=max(2, top_k // len(document_namespaces)),
            min_score=0.3,
            namespaces=document_namespaces
        )

        # Combine and sort by score
        combined = []
        for namespace, results in all_results.items():
            for r in results:
                r["source_namespace"] = namespace
                combined.append(r)

        combined.sort(key=lambda x: x.get("score", 0), reverse=True)

        return combined[:top_k]

    async def search_similar(
        self,
        embedding: List[float],
        filter: Optional[Dict[str, Any]] = None,
        top_k: int = 5,
        namespace: str = "skill_learnings"
    ) -> List[Dict[str, Any]]:
        """
        Search for similar vectors using an embedding.

        Args:
            embedding: The query embedding vector
            filter: Optional metadata filter
            top_k: Number of results to return
            namespace: Pinecone namespace to search in

        Returns:
            List of similar items with scores and metadata
        """
        if not self.enabled:
            return []

        try:
            url = f"https://{self.index_host}/query"

            payload = {
                "namespace": namespace,
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

            # Format results to match expected structure
            results = []
            for match in matches:
                results.append({
                    "id": match.get("id"),
                    "score": match.get("score", 0),
                    "metadata": match.get("metadata", {})
                })

            return results

        except Exception as e:
            logger.error(f"Error searching similar vectors: {e}")
            return []

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

    async def store_learning(
        self,
        learning_id: str,
        embedding: List[float],
        metadata: Dict[str, Any]
    ) -> str:
        """
        Store a skill learning in Pinecone.

        Args:
            learning_id: Unique ID for the learning
            embedding: Embedding vector
            metadata: Learning metadata

        Returns:
            Pinecone vector ID
        """
        if not self.enabled:
            logger.warning("Cannot store learning - Pinecone not configured")
            return None

        try:
            # Create vector ID
            vector_id = f"learning_{learning_id}"

            url = f"https://{self.index_host}/vectors/upsert"

            payload = {
                "vectors": [{
                    "id": vector_id,
                    "values": embedding,
                    "metadata": metadata
                }],
                "namespace": "skill_learnings"
            }

            headers = {
                "Api-Key": self.api_key,
                "Content-Type": "application/json",
                "X-Pinecone-API-Version": self.api_version
            }

            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(url, json=payload, headers=headers)
                response.raise_for_status()

            logger.info(f"Stored learning in Pinecone: {vector_id}")
            return vector_id

        except Exception as e:
            logger.error(f"Error storing learning in Pinecone: {e}")
            raise

    # Property to expose for legacy code that expects pinecone_index
    @property
    def pinecone_index(self):
        """Legacy property for compatibility."""
        return self

    async def upsert(self, vectors: list, namespace: str = "skill_learnings"):
        """
        Upsert vectors to Pinecone (compatibility method).

        Args:
            vectors: List of (id, values, metadata) tuples
            namespace: Pinecone namespace

        Returns:
            None
        """
        if not self.enabled:
            logger.warning("Cannot upsert - Pinecone not configured")
            return

        try:
            url = f"https://{self.index_host}/vectors/upsert"

            # Format vectors for Pinecone API
            formatted_vectors = []
            for vector_tuple in vectors:
                if len(vector_tuple) == 3:
                    vector_id, values, metadata = vector_tuple
                    formatted_vectors.append({
                        "id": vector_id,
                        "values": values,
                        "metadata": metadata
                    })

            payload = {
                "vectors": formatted_vectors,
                "namespace": namespace
            }

            headers = {
                "Api-Key": self.api_key,
                "Content-Type": "application/json",
                "X-Pinecone-API-Version": self.api_version
            }

            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(url, json=payload, headers=headers)
                response.raise_for_status()

            logger.info(f"Upserted {len(formatted_vectors)} vectors to {namespace}")

        except Exception as e:
            logger.error(f"Error upserting vectors: {e}")
            raise


# Singleton instance
knowledge_store = KnowledgeStore()
