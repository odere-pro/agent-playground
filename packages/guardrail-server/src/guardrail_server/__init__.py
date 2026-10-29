"""The guardrail server (PoC-7, backlog 022 H-6): Llama Prompt Guard 2 behind LiteLLM's generic
guardrail API, plus a secret detector, and a stub of LiteLLM's apply_guardrail API for offline
tests. It never imports the chassis: it sits behind LiteLLM (`make lint` checks it).
"""

from __future__ import annotations

from guardrail_server.app import GENERIC_PATH, THRESHOLD, create_app
from guardrail_server.classifier import (
    INJECTION_PATTERNS,
    PROMPT_GUARD_MODEL,
    Classifier,
    PatternClassifier,
    PromptGuardClassifier,
)

__all__ = [
    "GENERIC_PATH",
    "INJECTION_PATTERNS",
    "PROMPT_GUARD_MODEL",
    "THRESHOLD",
    "Classifier",
    "PatternClassifier",
    "PromptGuardClassifier",
    "create_app",
]
