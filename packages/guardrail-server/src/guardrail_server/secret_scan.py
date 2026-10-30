"""The secret detector: values shaped like a credential, which must never leave in an answer or a
tool output. Regex only (suggested patterns); a match blocks, it is never masked and passed on.
The reason never holds the value.
"""

from __future__ import annotations

import re

__all__ = ["SECRET_PATTERNS", "find_secret"]

# Hand copy of chassis/fakes/guardrail.py;
# packages/contract-suites/tests/test_guardrail_pattern_drift.py pins the drift.
SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("api_key", re.compile(r"\bsk-[A-Za-z0-9_-]{16,}")),
    ("aws_access_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("private_key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    (
        "key_assignment",
        re.compile(r"\b[A-Z][A-Z0-9_]*(API_KEY|SECRET|TOKEN|PASSWORD)\s*[=:]\s*\S{8,}"),
    ),
    ("bearer", re.compile(r"\bBearer\s+[A-Za-z0-9._~+/-]{20,}=*")),
)


def find_secret(text: str) -> str | None:
    """The kind of the first secret-shaped value in `text`, or None."""
    for kind, pattern in SECRET_PATTERNS:
        if pattern.search(text):
            return kind
    return None
