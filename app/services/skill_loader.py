"""Skill loader service for loading domain knowledge and prompts."""
import logging
from pathlib import Path
from typing import Dict, Optional

logger = logging.getLogger(__name__)


class SkillLoader:
    """Load and manage skills for NZ rental returns processing."""

    def __init__(self):
        """Initialize skill loader."""
        self.skills_dir = Path(__file__).parent.parent / "skills"
        self._cache: Dict[str, str] = {}

    def get_skill_path(self, skill_name: str) -> Path:
        """Get the path to a skill directory."""
        return self.skills_dir / skill_name

    def load_skill_md(self, skill_name: str = "nz_rental_returns") -> str:
        """
        Load the SKILL.md file for domain knowledge.

        Args:
            skill_name: Name of the skill to load

        Returns:
            Content of SKILL.md file
        """
        cache_key = f"{skill_name}_skill_md"

        if cache_key in self._cache:
            return self._cache[cache_key]

        skill_path = self.get_skill_path(skill_name) / "SKILL.md"

        try:
            content = skill_path.read_text(encoding="utf-8")
            self._cache[cache_key] = content
            logger.info(f"Loaded SKILL.md from {skill_path}")
            return content
        except FileNotFoundError:
            logger.warning(f"SKILL.md not found: {skill_path}")
            return ""

    def load_prompt(
        self,
        prompt_name: str,
        skill_name: str = "nz_rental_returns"
    ) -> Optional[str]:
        """
        Load a prompt template.

        Args:
            prompt_name: Name of the prompt module (e.g., 'transaction_extraction')
            skill_name: Name of the skill

        Returns:
            Prompt template string or None
        """
        cache_key = f"{skill_name}_{prompt_name}"

        if cache_key in self._cache:
            return self._cache[cache_key]

        # Try to import the prompt module
        try:
            module_path = f"app.skills.{skill_name}.prompts.{prompt_name}"
            module = __import__(module_path, fromlist=[""])

            # Get the main prompt constant (uppercase ending with _PROMPT)
            for attr_name in dir(module):
                if attr_name.endswith("_PROMPT"):
                    prompt = getattr(module, attr_name)
                    self._cache[cache_key] = prompt
                    logger.info(f"Loaded prompt {attr_name} from {module_path}")
                    return prompt

            logger.warning(f"No prompt constant found in {module_path}")
            return None

        except ImportError as e:
            logger.warning(f"Could not import prompt module {prompt_name}: {e}")
            return None

    def get_bank_statement_prompt(self, context: Dict) -> str:
        """
        Get formatted bank statement extraction prompt.

        Args:
            context: Dictionary with property_address, tax_year, etc.

        Returns:
            Formatted prompt string
        """
        from app.skills.nz_rental_returns.prompts.transaction_extraction import (
            BANK_STATEMENT_EXTRACTION_PROMPT,
        )

        # Calculate tax year dates
        tax_year = context.get("tax_year", "FY25")
        year = int(tax_year[2:]) + 2000
        tax_year_start = f"{year - 1}-04-01"
        tax_year_end = f"{year}-03-31"

        return BANK_STATEMENT_EXTRACTION_PROMPT.format(
            property_address=context.get("property_address", ""),
            tax_year=tax_year,
            tax_year_start=tax_year_start,
            tax_year_end=tax_year_end,
            property_type=context.get("property_type", "existing"),
            year_of_ownership=context.get("year_of_ownership", 1)
        )

    def get_settlement_prompt(self, context: Dict) -> str:
        """
        Get formatted settlement statement extraction prompt.

        Args:
            context: Dictionary with property_address, tax_year, etc.

        Returns:
            Formatted prompt string
        """
        from app.skills.nz_rental_returns.prompts.settlement_extraction import (
            SETTLEMENT_STATEMENT_EXTRACTION_PROMPT,
        )

        return SETTLEMENT_STATEMENT_EXTRACTION_PROMPT.format(
            property_address=context.get("property_address", ""),
            tax_year=context.get("tax_year", "FY25")
        )

    def get_pm_statement_prompt(self, context: Dict) -> str:
        """
        Get formatted PM statement extraction prompt.

        Args:
            context: Dictionary with property_address, tax_year, etc.

        Returns:
            Formatted prompt string
        """
        from app.skills.nz_rental_returns.prompts.pm_statement_extraction import (
            PM_STATEMENT_EXTRACTION_PROMPT,
        )

        # Calculate tax year dates
        tax_year = context.get("tax_year", "FY25")
        year = int(tax_year[2:]) + 2000
        tax_year_start = f"{year - 1}-04-01"
        tax_year_end = f"{year}-03-31"

        return PM_STATEMENT_EXTRACTION_PROMPT.format(
            property_address=context.get("property_address", ""),
            tax_year=tax_year,
            tax_year_start=tax_year_start,
            tax_year_end=tax_year_end
        )

    def get_domain_context(self) -> str:
        """
        Get combined domain knowledge for Claude prompts.

        Returns:
            Combined SKILL.md content for context injection
        """
        skill_md = self.load_skill_md()

        if skill_md:
            return f"""
## Domain Knowledge (NZ Rental Property Tax Returns)

{skill_md}

---
"""
        return ""

    def clear_cache(self):
        """Clear the cached content."""
        self._cache.clear()
        logger.info("Skill loader cache cleared")


# Singleton instance
_skill_loader: Optional[SkillLoader] = None


def get_skill_loader() -> SkillLoader:
    """Get or create the singleton skill loader."""
    global _skill_loader

    if _skill_loader is None:
        _skill_loader = SkillLoader()

    return _skill_loader