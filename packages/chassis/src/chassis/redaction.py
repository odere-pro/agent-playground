"""The chassis's own PII redaction: regex only (PoC-7, backlog 022 H-6).

It covers emails, phone numbers, and ids (card numbers, IBANs, US social security numbers) in the
chassis's logs and in the events it publishes. Named-entity redaction (names, places) runs in
LiteLLM's guardrails and in the OTel Collector log pipeline, never here: a named-entity model adds
about 0.5 to 1 GiB per replica and breaks ADR-001's memory estimate. Standard library only.

Each match becomes `<ENTITY>`, the form Presidio's anonymizer uses by default, so a record reads
the same whichever layer redacted it. suggested: the entity names and the patterns.

A phone match is kept as it is only when the whole match is a valid ISO date (`2026-10-29`), an
IPv4 address (four groups of 0 to 255), or an x.y.z version (three groups of 1 or 2 digits). Any
other dotted or dashed run (`612.345.678`, `0171-12-34`) is still masked. The email pattern starts
only where a local part can start, so a long run stays linear.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

_LOCAL = r"[A-Za-z0-9._%+-]"
_EMAIL_BODY = _LOCAL + r"+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"
_EMAIL_AT_RESUME = re.compile(_EMAIL_BODY)
"""The unanchored email, tried only where the last match ended, as `re.sub` used to."""

PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("EMAIL_ADDRESS", re.compile(rf"(?<!{_LOCAL}){_EMAIL_BODY}")),
    ("IBAN_CODE", re.compile(r"\b[A-Z]{2}\d{2}(?: ?[A-Z0-9]{4}){2,7}(?: ?[A-Z0-9]{1,4})?\b")),
    ("CREDIT_CARD", re.compile(r"\b\d{4}(?:[ -]?\d{4}){2}[ -]?\d{1,7}\b")),
    ("US_SSN", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    (
        "PHONE_NUMBER",
        re.compile(r"(?<![\w-])\+?\d{1,3}?[ .-]?\(?\d{2,4}\)?(?:[ .-]?\d{2,4}){2,3}(?![\w-])"),
    ),
)
"""Ordered: an IBAN or a card number is replaced before the phone pattern could take part of it."""

_ISO_DATE = r"\d{4}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01])"
_IPV4_GROUP = r"(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)"
_IPV4 = rf"{_IPV4_GROUP}(?:\.{_IPV4_GROUP}){{3}}"
_VERSION = r"\d{1,2}(?:\.\d{1,2}){2}"
_NOT_A_PHONE = re.compile(rf"{_ISO_DATE}|{_IPV4}|{_VERSION}")
"""Exactly one of: an ISO date with a valid month and day, an IPv4 address (four groups of 0 to
255, no leading zero), or a version of three groups of 1 or 2 digits. Used with `fullmatch` only;
it never widens beyond these shapes, so a phone the plain pattern masks stays masked."""


def _phone_or_kept(match: re.Match[str]) -> str:
    text = match.group(0)
    return text if _NOT_A_PHONE.fullmatch(text) else "<PHONE_NUMBER>"


def _redact_emails(text: str, pattern: re.Pattern[str]) -> str:
    """Same matches as an unanchored `sub`, in linear time: try the resume point, then search."""
    parts: list[str] = []
    pos = 0
    while (m := _EMAIL_AT_RESUME.match(text, pos) or pattern.search(text, pos)) is not None:
        parts += (text[pos : m.start()], "<EMAIL_ADDRESS>")
        pos = m.end()
    parts.append(text[pos:])
    return "".join(parts)


def _redact_one(text: str, entity: str, pattern: re.Pattern[str]) -> str:
    if entity == "EMAIL_ADDRESS":
        return _redact_emails(text, pattern)
    if entity == "PHONE_NUMBER":
        return pattern.sub(_phone_or_kept, text)
    return pattern.sub(f"<{entity}>", text)


def redact(text: str) -> str:
    """`text` with every match replaced by `<ENTITY>`."""
    for entity, pattern in PATTERNS:
        text = _redact_one(text, entity, pattern)
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
