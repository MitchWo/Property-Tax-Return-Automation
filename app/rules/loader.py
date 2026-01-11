"""YAML rules loader for categorization patterns."""
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

logger = logging.getLogger(__name__)

# Cache for loaded rules
_rules_cache: Dict[str, Any] = {}
_bank_parsers_cache: Dict[str, Any] = {}


def get_rules_path() -> Path:
    """Get the path to the rules directory."""
    return Path(__file__).parent


def load_categorization_rules(force_reload: bool = False) -> Dict[str, Any]:
    """
    Load categorization rules from YAML file.

    Args:
        force_reload: Force reload from disk even if cached

    Returns:
        Dictionary containing all categorization rules
    """
    global _rules_cache

    if _rules_cache and not force_reload:
        return _rules_cache

    rules_path = get_rules_path() / "categorization.yaml"

    try:
        with open(rules_path, "r", encoding="utf-8") as f:
            _rules_cache = yaml.safe_load(f)
            logger.info(f"Loaded categorization rules from {rules_path}")
            return _rules_cache
    except FileNotFoundError:
        logger.error(f"Categorization rules not found: {rules_path}")
        return {}
    except yaml.YAMLError as e:
        logger.error(f"Error parsing categorization rules: {e}")
        return {}


def load_bank_parsers(force_reload: bool = False) -> Dict[str, Any]:
    """
    Load bank parser configurations from YAML file.

    Args:
        force_reload: Force reload from disk even if cached

    Returns:
        Dictionary containing all bank parser configs
    """
    global _bank_parsers_cache

    if _bank_parsers_cache and not force_reload:
        return _bank_parsers_cache

    parsers_path = get_rules_path() / "bank_parsers.yaml"

    try:
        with open(parsers_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
            _bank_parsers_cache = data.get("banks", {})
            logger.info(f"Loaded bank parsers from {parsers_path}")
            return _bank_parsers_cache
    except FileNotFoundError:
        logger.error(f"Bank parsers not found: {parsers_path}")
        return {}
    except yaml.YAMLError as e:
        logger.error(f"Error parsing bank parsers: {e}")
        return {}


def get_bank_parser(bank_identifier: str) -> Optional[Dict[str, Any]]:
    """
    Get parser configuration for a specific bank.

    Args:
        bank_identifier: Bank name or identifier to look up

    Returns:
        Bank parser configuration or None if not found
    """
    parsers = load_bank_parsers()

    # Normalize identifier for comparison
    identifier_lower = bank_identifier.lower()

    # Check each bank's identifiers
    for bank_key, config in parsers.items():
        identifiers = config.get("identifiers", [])
        if any(ident.lower() in identifier_lower for ident in identifiers):
            return config
        if bank_key.lower() in identifier_lower:
            return config

    # Return generic parser as fallback
    return parsers.get("generic")


class PatternMatcher:
    """Match transaction descriptions against categorization rules."""

    def __init__(self):
        """Initialize pattern matcher with loaded rules."""
        self.rules = load_categorization_rules()
        self._compiled_patterns: List[Tuple[re.Pattern, Dict]] = []
        self._compile_patterns()

    def _compile_patterns(self):
        """Pre-compile regex patterns for performance."""
        patterns = self.rules.get("patterns", [])

        for pattern_config in patterns:
            try:
                compiled = re.compile(pattern_config["pattern"], re.IGNORECASE)
                self._compiled_patterns.append((compiled, pattern_config))
            except re.error as e:
                logger.error(f"Invalid regex pattern: {pattern_config['pattern']} - {e}")

    def match_payee(self, other_party: str) -> Optional[Dict[str, Any]]:
        """
        Match other_party against exact payee rules.

        Args:
            other_party: The other party / payee name

        Returns:
            Match result with category and confidence, or None
        """
        if not other_party:
            return None

        payees = self.rules.get("payees", {})

        # Normalize for comparison
        other_party_upper = other_party.strip().upper()

        for payee_name, config in payees.items():
            if payee_name.upper() == other_party_upper:
                return {
                    "category": config["category"],
                    "confidence": config.get("confidence", 0.95),
                    "source": "yaml_payee",
                    "matched_payee": payee_name,
                    "flag_for_review": config.get("flag_for_review", False),
                    "review_reason": config.get("review_reason"),
                    "notes": config.get("notes")
                }

        return None

    def match_pattern(
        self,
        description: str,
        transaction_type: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Match description against regex patterns.

        Args:
            description: Transaction description
            transaction_type: 'income' or 'expense' (for filtering)

        Returns:
            Match result with category and confidence, or None
        """
        if not description:
            return None

        best_match = None
        best_confidence = 0.0

        for compiled_pattern, config in self._compiled_patterns:
            if compiled_pattern.search(description):
                # Check if transaction type requirement is met
                require = config.get("require", {})
                required_type = require.get("transaction_type")

                if required_type and transaction_type:
                    if required_type != transaction_type:
                        continue  # Skip this pattern

                confidence = config.get("confidence", 0.80)

                if confidence > best_confidence:
                    best_confidence = confidence
                    best_match = {
                        "category": config["category"],
                        "confidence": confidence,
                        "source": "yaml_pattern",
                        "matched_pattern": config["pattern"],
                        "flag_for_review": config.get("flag_for_review", False),
                        "review_reason": config.get("review_reason"),
                        "notes": config.get("notes")
                    }

        return best_match

    def match_keyword(
        self,
        description: str,
        transaction_type: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Match description against keyword rules (lowest priority).

        Args:
            description: Transaction description
            transaction_type: 'income' or 'expense' (for filtering)

        Returns:
            Match result with category and confidence, or None
        """
        if not description:
            return None

        keywords = self.rules.get("keywords", {})
        description_lower = description.lower()

        best_match = None
        best_confidence = 0.0

        for keyword, config in keywords.items():
            if keyword.lower() in description_lower:
                # Check transaction type requirement
                require = config.get("require", {})
                required_type = require.get("transaction_type")

                if required_type and transaction_type:
                    if required_type != transaction_type:
                        continue

                confidence = config.get("confidence", 0.50)

                if confidence > best_confidence:
                    best_confidence = confidence
                    best_match = {
                        "category": config["category"],
                        "confidence": confidence,
                        "source": "yaml_keyword",
                        "matched_keyword": keyword,
                        "flag_for_review": config.get("flag_for_review", False),
                        "review_reason": config.get("review_reason")
                    }

        return best_match

    def match(
        self,
        description: str,
        other_party: Optional[str] = None,
        amount: Optional[float] = None,
        transaction_type: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Full matching pipeline: payee -> pattern -> keyword.

        Args:
            description: Transaction description
            other_party: The other party / payee name
            amount: Transaction amount (for amount rules)
            transaction_type: 'income' or 'expense'

        Returns:
            Best match result, or None if no match
        """
        # 1. Try exact payee match (highest confidence)
        if other_party:
            match = self.match_payee(other_party)
            if match:
                return self._apply_amount_rules(match, amount)

        # 2. Try regex pattern match
        match = self.match_pattern(description, transaction_type)
        if match:
            return self._apply_amount_rules(match, amount)

        # 3. Try keyword match (lowest confidence)
        match = self.match_keyword(description, transaction_type)
        if match:
            return self._apply_amount_rules(match, amount)

        return None

    def _apply_amount_rules(
        self,
        match: Dict[str, Any],
        amount: Optional[float] = None
    ) -> Dict[str, Any]:
        """Apply amount-based rules to flag unusual transactions."""
        if amount is None:
            return match

        amount_rules = self.rules.get("amount_rules", {})
        category = match["category"]

        if category in amount_rules:
            rule = amount_rules[category]
            typical_range = rule.get("typical_range", [0, float("inf")])

            abs_amount = abs(amount)
            if abs_amount < typical_range[0] or abs_amount > typical_range[1]:
                if rule.get("flag_if_outside", False):
                    match["flag_for_review"] = True
                    match["review_reason"] = rule.get(
                        "review_reason",
                        f"Amount ${abs_amount:.2f} outside typical range"
                    )

        return match


# Singleton instance
_pattern_matcher: Optional[PatternMatcher] = None


def get_pattern_matcher() -> PatternMatcher:
    """Get or create the singleton pattern matcher."""
    global _pattern_matcher

    if _pattern_matcher is None:
        _pattern_matcher = PatternMatcher()

    return _pattern_matcher


def reload_rules():
    """Force reload of all rules (useful after editing YAML files)."""
    global _pattern_matcher, _rules_cache, _bank_parsers_cache

    _rules_cache = {}
    _bank_parsers_cache = {}
    _pattern_matcher = None

    # Reload
    load_categorization_rules(force_reload=True)
    load_bank_parsers(force_reload=True)

    logger.info("Rules reloaded")