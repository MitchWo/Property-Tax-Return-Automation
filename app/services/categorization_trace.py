"""Helper for building categorization trace during transaction processing."""
from datetime import datetime
from typing import Any, Dict


class CategorizationTrace:
    """Builds a trace of categorization decisions for a transaction."""

    def __init__(self):
        self.trace = {
            "layers": {
                "context": {"matched": False},  # Document context layer (highest priority)
                "yaml": {"matched": False},
                "learned": {"matched": False},
                "fuzzy": {"matched": False},
                "rag": {"matched": False, "learnings_found": 0},
                "claude": None
            },
            "decision": None,
            "decided_by": None,
            "timestamp": datetime.utcnow().isoformat() + "Z"
        }

    def record_context_match(self, matched: bool, category: str = None,
                             confidence: float = 0, reason: str = None):
        """Record document context matching result (e.g., loan account, owner name)."""
        self.trace["layers"]["context"] = {
            "matched": matched,
            "category": category,
            "confidence": confidence,
            "reason": reason
        }
        if matched and not self.trace["decision"]:
            self.trace["decision"] = category
            self.trace["decided_by"] = "document_context"

    def record_yaml_match(self, matched: bool, category: str = None,
                          confidence: float = 0, pattern: str = None):
        """Record YAML pattern matching result."""
        self.trace["layers"]["yaml"] = {
            "matched": matched,
            "category": category,
            "confidence": confidence,
            "pattern": pattern
        }
        if matched and not self.trace["decision"]:
            self.trace["decision"] = category
            self.trace["decided_by"] = "yaml_pattern"

    def record_learned_match(self, matched: bool, category: str = None,
                             confidence: float = 0, times_used: int = 0):
        """Record learned pattern matching result."""
        self.trace["layers"]["learned"] = {
            "matched": matched,
            "category": category,
            "confidence": confidence,
            "times_used": times_used
        }
        if matched and not self.trace["decision"]:
            self.trace["decision"] = category
            self.trace["decided_by"] = "learned_pattern"

    def record_rag_match(
        self,
        matched: bool,
        category: str = None,
        confidence: float = 0,
        learning_title: str = None,
        learnings_found: int = 0
    ):
        """Record RAG learning search result."""
        self.trace["layers"]["rag"] = {
            "matched": matched,
            "category": category,
            "confidence": confidence,
            "learning_title": learning_title,
            "learnings_found": learnings_found
        }
        if matched and not self.trace["decision"]:
            self.trace["decision"] = category
            self.trace["decided_by"] = "rag_learning"

    def record_claude_result(self, category: str, confidence: float,
                             reasoning: str = None):
        """Record Claude AI categorization result."""
        self.trace["layers"]["claude"] = {
            "category": category,
            "confidence": confidence,
            "reasoning": reasoning
        }
        if not self.trace["decision"]:
            self.trace["decision"] = category
            self.trace["decided_by"] = "claude_ai"

    def record_manual(self, category: str):
        """Record manual categorization."""
        self.trace["decision"] = category
        self.trace["decided_by"] = "manual"

    def to_dict(self) -> Dict[str, Any]:
        """Return the trace as a dictionary for storage."""
        return self.trace