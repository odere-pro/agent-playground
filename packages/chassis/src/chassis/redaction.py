"""The chassis's own PII redaction: regex only (PoC-7, backlog 022 H-6).

It covers emails, phone numbers, and ids (card numbers, IBANs, US social security numbers) in the
chassis's logs and in the events it publishes. Named-entity redaction (names, places) runs in
LiteLLM's guardrails and in the OTel Collector log pipeline, never here: a named-entity model adds
about 0.5 to 1 GiB per replica and breaks ADR-001's memory estimate. Standard library only.

Each match becomes `<ENTITY>`, the form Presidio's anonymizer uses by default, so a record reads
the same whichever layer redacted it. suggested: the entity names and the patterns.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("EMAIL_ADDRESS", re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")),
    ("IBAN_CODE", re.compile(r"\b[A-Z]{2}\d{2}(?: ?[A-Z0-9]{4}){2,7}(?: ?[A-Z0-9]{1,4})?\b")),
    ("CREDIT_CARD", re.compile(r"\b\d{4}(?:[ -]?\d{4}){2}[ -]?\d{1,7}\b")),
    ("US_SSN", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    (
        "PHONE_NUMBER",
        re.compile(r"(?<![\w-])\+?\d{1,3}?[ .-]?\(?\d{2,4}\)?(?:[ .-]?\d{2,4}){2,3}(?![\w-])"),
    ),
)
"""Ordered: an IBAN or a card number is replaced before the phone pattern could take part of it."""


def redact(text: str) -> str:
    """`text` with every match replaced by `<ENTITY>`."""
    for entity, pattern in PATTERNS:
        text = pattern.sub(f"<{entity}>", text)
    return text


def redact_value(value: Any) -> Any:
    """`redact` applied to every string inside `value` (dicts, lists, and tuples, deeply)."""
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, Mapping):
        return {k: redact_value(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return type(value)(redact_value(v) for v in value)
    return value
