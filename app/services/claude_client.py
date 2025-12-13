"""Claude AI client for document analysis."""

import asyncio
import base64
import io
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx
from anthropic import APIError, AsyncAnthropic, RateLimitError
from PIL import Image

from app.config import settings
from app.schemas.documents import DocumentClassification, DocumentSummary
from app.services.prompts import (
    COMPLETENESS_REVIEW_PROMPT,
    DOCUMENT_CLASSIFICATION_PROMPT,
    TRANSACTION_FLAGGING_RULES,
)

logger = logging.getLogger(__name__)


class ClaudeClient:
    """Client for interacting with Claude AI."""

    def __init__(self, api_key: str = None, model: str = None):
        """Initialize Claude client with Claude Opus 4.5."""
        self.client = AsyncAnthropic(
            api_key=api_key or settings.ANTHROPIC_API_KEY,
            timeout=httpx.Timeout(120.0, connect=10.0),
        )
        # Use Claude Opus 4.5 for superior document analysis
        self.model = model or settings.CLAUDE_MODEL  # claude-opus-4-5-20251101
        self.max_retries = 3

    async def _call_with_retry(self, create_func):
        """Call Claude API with retry logic and exponential backoff."""
        last_error = None

        for attempt in range(self.max_retries):
            try:
                response = await create_func()

                # Log token usage for monitoring
                if hasattr(response, "usage"):
                    logger.info(
                        f"Claude API call: {response.usage.input_tokens} input, "
                        f"{response.usage.output_tokens} output tokens"
                    )

                return response

            except RateLimitError as e:
                last_error = e
                wait_time = 2**attempt  # 1, 2, 4 seconds
                logger.warning(
                    f"Rate limited, waiting {wait_time}s (attempt {attempt + 1}/{self.max_retries})"
                )
                await asyncio.sleep(wait_time)

            except APIError as e:
                last_error = e
                if attempt < self.max_retries - 1:
                    logger.warning(
                        f"API error, retrying (attempt {attempt + 1}/{self.max_retries}): {e}"
                    )
                    await asyncio.sleep(1)
                else:
                    raise

        raise last_error

    async def analyze_document(
        self,
        document_content: Optional[str],
        image_data: Optional[List[Tuple[bytes, str]]],
        context: Dict[str, Any],
        transaction_learnings: Optional[List[Dict[str, Any]]] = None,
    ) -> DocumentClassification:
        """
        Analyze a single document for classification and extraction.

        Args:
            document_content: Text content of document (if available)
            image_data: List of (image_bytes, media_type) tuples
            context: Additional context (client info, property details)
            transaction_learnings: List of past learnings from Pinecone to apply

        Returns:
            DocumentClassification with analysis results
        """
        try:
            # Build message content
            content = self._build_message_content(document_content, image_data)

            # Add context to prompt
            system_prompt = DOCUMENT_CLASSIFICATION_PROMPT.format(
                client_name=context.get("client_name", ""),
                property_address=context.get("property_address", ""),
                tax_year=context.get("tax_year", ""),
                property_type=context.get("property_type", ""),
            )

            # Include transaction flagging rules for all documents
            # (Claude will apply them when it identifies financial documents)
            include_transaction_analysis = context.get("include_transaction_analysis", True)
            if include_transaction_analysis:
                system_prompt = system_prompt + "\n\n" + TRANSACTION_FLAGGING_RULES

            # Inject transaction learnings from past feedback
            if transaction_learnings:
                learnings_context = self._format_transaction_learnings(transaction_learnings)
                if learnings_context:
                    system_prompt = system_prompt + "\n\n" + learnings_context

            # Call Claude API with retry logic
            response = await self._call_with_retry(
                lambda: self.client.messages.create(
                    model=self.model,
                    max_tokens=4096,
                    temperature=0.1,  # Lower temperature for more deterministic classification
                    system=system_prompt,
                    messages=[{"role": "user", "content": content}],
                )
            )

            # Parse JSON response
            response_text = response.content[0].text

            # Clean up the response text (remove markdown code blocks if present)
            if "```json" in response_text:
                response_text = response_text.split("```json")[1].split("```")[0].strip()
            elif "```" in response_text:
                response_text = response_text.split("```")[1].split("```")[0].strip()

            classification_data = json.loads(response_text)

            return DocumentClassification(**classification_data)

        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse Claude response as JSON: {e}")
            logger.error(f"Response was: {response_text if 'response_text' in locals() else 'N/A'}")
            # Return a fallback classification
            return DocumentClassification(
                document_type="other",
                confidence=0.0,
                reasoning="Failed to parse classification response",
                flags=["classification_error"],
                key_details={},
            )
        except Exception as e:
            logger.error(f"Error analyzing document: {e}")
            raise

    async def review_all_documents(
        self, documents: List[DocumentSummary], context: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Final review of all documents for completeness.

        Args:
            documents: List of document summaries
            context: Tax return context

        Returns:
            Complete review results
        """
        try:
            # Prepare documents summary for review
            docs_text = self._format_documents_for_review(documents)

            # Build prompt with context
            prompt = f"""
Property Details:
- Address: {context.get('property_address', '')}
- Tax Year: {context.get('tax_year', '')}
- Property Type: {context.get('property_type', '')}
- GST Registered: {context.get('gst_registered', False)}
- Year of Ownership: {context.get('year_of_ownership', 1)}

Documents Submitted ({len(documents)} total):
{docs_text}

Please review these documents for completeness and provide your assessment.
"""

            # Call Claude API with retry logic
            response = await self._call_with_retry(
                lambda: self.client.messages.create(
                    model=self.model,
                    max_tokens=4096,
                    temperature=0.1,  # Lower temperature for consistent reviews
                    system=COMPLETENESS_REVIEW_PROMPT,
                    messages=[{"role": "user", "content": prompt}],
                )
            )

            # Parse JSON response
            response_text = response.content[0].text

            # Clean up the response text
            if "```json" in response_text:
                response_text = response_text.split("```json")[1].split("```")[0].strip()
            elif "```" in response_text:
                response_text = response_text.split("```")[1].split("```")[0].strip()

            return json.loads(response_text)

        except Exception as e:
            logger.error(f"Error reviewing documents: {e}")
            raise

    def _build_message_content(
        self, text: Optional[str], images: Optional[List[Tuple[bytes, str]]]
    ) -> List[Dict[str, Any]]:
        """Build message content for Claude API."""
        content = []

        # Add images first if available
        if images:
            for img_data, media_type in images:
                # Convert image to base64
                base64_data = base64.b64encode(img_data).decode()

                content.append(
                    {
                        "type": "image",
                        "source": {"type": "base64", "media_type": media_type, "data": base64_data},
                    }
                )

        # Add text content if available
        if text:
            content.append({"type": "text", "text": f"Document text content:\n{text}"})
        elif not images:
            # If no content at all, add placeholder
            content.append({"type": "text", "text": "No document content available for analysis."})

        return content

    def _format_documents_for_review(self, documents: List[DocumentSummary]) -> str:
        """Format documents for review prompt."""
        lines = []

        for i, doc in enumerate(documents, 1):
            lines.append(f"{i}. {doc.filename}")
            lines.append(f"   Type: {doc.document_type}")

            if doc.key_details:
                lines.append("   Key Details:")
                for key, value in doc.key_details.items():
                    lines.append(f"   - {key}: {value}")

            if doc.flags:
                lines.append(f"   Flags: {', '.join(doc.flags)}")

            lines.append("")  # Empty line between documents

        return "\n".join(lines)

    def _format_transaction_learnings(self, learnings: List[Dict[str, Any]]) -> str:
        """
        Format transaction learnings for injection into Claude prompt.

        Args:
            learnings: List of learnings from Pinecone search

        Returns:
            Formatted string to append to system prompt
        """
        if not learnings:
            return ""

        # Filter for transaction-related learnings
        transaction_learnings = [
            learning
            for learning in learnings
            if learning.get("category") == "transaction_analysis"
            or learning.get("scenario")
            in ["legitimate_rental_vendor", "flagged_transaction_pattern"]
            or "transaction" in learning.get("content", "").lower()
        ]

        if not transaction_learnings:
            return ""

        context = """
IMPORTANT - APPLY THESE LEARNINGS FROM PAST FEEDBACK:

The following transactions/vendors have been reviewed previously. Apply this knowledge when flagging transactions:

"""
        for i, learning in enumerate(transaction_learnings, 1):
            content = learning.get("content", "")
            scenario = learning.get("scenario", "unknown")
            score = learning.get("score", 0)

            # Only include reasonably relevant learnings
            if score >= 0.3 or scenario in [
                "legitimate_rental_vendor",
                "flagged_transaction_pattern",
            ]:
                context += f"{i}. {content}\n\n"

        context += """
When you encounter similar transactions, apply these learnings:
- If a transaction matches a "legitimate" learning, do NOT flag it
- If a transaction matches a "flagged" learning, continue to flag it
- Use these patterns to make better decisions about similar transactions
"""
        return context

    async def prepare_image_data(self, image_paths: List[str]) -> List[Tuple[bytes, str]]:
        """
        Prepare image data for Claude API with improved format handling.

        Args:
            image_paths: List of image file paths

        Returns:
            List of (image_bytes, media_type) tuples
        """
        image_data = []

        for image_path in image_paths:
            try:
                path = Path(image_path)

                # Determine initial media type
                suffix = path.suffix.lower()
                media_type_map = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}
                media_type = media_type_map.get(suffix, "image/png")

                # Read and potentially resize image
                with Image.open(path) as img:
                    # Convert to RGB if necessary (handles RGBA, P mode, etc.)
                    if img.mode in ("RGBA", "P", "LA"):
                        # Convert to RGB for JPEG format
                        img = img.convert("RGB")
                        output_format = "JPEG"
                        media_type = "image/jpeg"
                    else:
                        output_format = img.format or "PNG"

                    # Resize if too large (Claude's recommended max: 1568x1568)
                    max_size = (1568, 1568)
                    if img.size[0] > max_size[0] or img.size[1] > max_size[1]:
                        img.thumbnail(max_size, Image.Resampling.LANCZOS)
                        logger.info(f"Resized image {image_path} from {img.size} to fit {max_size}")

                    # Convert to bytes
                    img_byte_arr = io.BytesIO()
                    save_format = (
                        "JPEG" if output_format == "JPEG" or media_type == "image/jpeg" else "PNG"
                    )

                    # Save with appropriate quality
                    if save_format == "JPEG":
                        img.save(img_byte_arr, format=save_format, quality=85, optimize=True)
                    else:
                        img.save(img_byte_arr, format=save_format, optimize=True)

                    img_bytes = img_byte_arr.getvalue()

                    # Check file size (Claude limit is ~5MB per image)
                    if len(img_bytes) > 5 * 1024 * 1024:
                        logger.warning(
                            f"Image {image_path} is {len(img_bytes) / 1024 / 1024:.1f}MB, may be too large"
                        )

                image_data.append((img_bytes, media_type))
                logger.debug(
                    f"Prepared image {image_path}: {media_type}, {len(img_bytes) / 1024:.1f}KB"
                )

            except Exception as e:
                logger.error(f"Error preparing image {image_path}: {e}")
                continue

        return image_data
