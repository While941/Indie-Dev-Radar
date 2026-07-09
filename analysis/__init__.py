"""AI analysis layer — triage scoring + high-value rewrite."""
from __future__ import annotations

from .ai_client import AIAuthError, AIClient, ChatClient
from .digest import DigestBuilder
from .rewriter import Rewriter
from .scorer import Scorer, compute_score

__all__ = [
    "AIAuthError", "AIClient", "ChatClient", "DigestBuilder",
    "Rewriter", "Scorer", "compute_score",
]
