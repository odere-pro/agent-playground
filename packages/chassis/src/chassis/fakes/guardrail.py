"""KeywordGuardrails: the `GuardrailPort` fake. Patterns stand in for Prompt Guard 2 and the
regex redaction stands in for Presidio, so tests see the same verdict shapes as the real stack.

Rules (suggested): injection patterns block at `input` and `tool_output`, checked per chunk of
`split_for_classifier`, as the real classifier is; a secret-looking value blocks at `output` and
`tool_output`; PII is masked at every stage. Records each call.
"""

from __future__ import annotations

import re

from chassis.ports.guardrail import (
    PII,
    PROMPT_INJECTION,
    GuardrailError,
    Stage,
    Verdict,
    split_for_classifier,
)
from chassis.redaction import redact

SECRET = "secret"

INJECTION_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.I)
    for p in (
        r"\b(ignore|disregard|forget)\b.{0,40}\b(previous|prior|above|earlier|all)\b.{0,20}"
        r"\b(instructions?|rules?|prompts?|messages?)\b",
        r"\b(reveal|print|show|repeat|leak)\b.{0,40}\b(system prompt|instructions|api key|"
        r"secret|password|token)s?\b",
        r"\byou are now\b",
        r"\bnew instructions?\s*:",
        r"\b(send|post|upload|exfiltrate)\b.{0,60}\bhttps?://",
    )
)
SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
)


class KeywordGuardrails:
    name = "fake"

    def __init__(self) -> None:
        self.calls: list[tuple[str, Stage]] = []
        self.unavailable = False

    async def check(self, text: str, stage: Stage) -> Verdict:
        self.calls.append((text, stage))
        if self.unavailable:
            raise GuardrailError("guardrail_unavailable", "scripted outage", retryable=True)
        if stage in ("input", "tool_output") and any(
            p.search(chunk) for chunk in split_for_classifier(text) for p in INJECTION_PATTERNS
        ):
            return Verdict(allowed=False, text=text, reasons=(PROMPT_INJECTION,))
        if stage in ("output", "tool_output") and any(p.search(text) for p in SECRET_PATTERNS):
            return Verdict(allowed=False, text=text, reasons=(SECRET,))
        masked = redact(text)
        return Verdict(allowed=True, text=masked, reasons=(PII,) if masked != text else ())
