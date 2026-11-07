"""KeywordGuardrails: the `GuardrailPort` fake. Patterns stand in for Prompt Guard 2 and the
regex redaction stands in for Presidio, so tests see the same verdict shapes as the real stack.

Rules (suggested): injection patterns block at `input` and `tool_output`, checked per chunk of
`split_for_classifier`, as the real classifier is; a secret-looking value blocks at `output` and
`tool_output`, matched after NFKC with zero-width and format characters dropped; PII is masked at
every stage. Records each call.
"""

from __future__ import annotations

import re
import unicodedata

from chassis.ports.guardrail import (
    PII,
    PROMPT_INJECTION,
    GuardrailError,
    Stage,
    Verdict,
    split_for_classifier,
)
from chassis.redaction import redact

SECRET = "secret"  # pragma: allowlist secret

# Hand copy of guardrail_server/classifier.py;
# packages/contract-suites/tests/test_guardrail_pattern_drift.py pins the drift.
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
# Hand copy of guardrail_server/secret_scan.py (the patterns and `normalize`);
# packages/contract-suites/tests/test_guardrail_pattern_drift.py pins the drift.
_FORMAT_CHARS = re.compile(
    "[\u00ad\u061c\u180e\u200b-\u200f\u202a-\u202e\u2060-\u2064\u2066-\u206f\ufeff"
    "\ufff9-\ufffb\U000e0001\U000e0020-\U000e007f]"
)
_KEY_NAME = r"(?:password|passwd|pwd|secret|token|api[_-]?key|access[_-]?key)"
_EDGE = r"(?<![A-Za-z0-9])"
_GITHUB = r"(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})"
SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(_EDGE + r"sk-[A-Za-z0-9_-]{16,}"),
    re.compile(_EDGE + r"(?:AKIA|ASIA)[A-Z0-9]{16}(?![A-Za-z0-9])"),
    re.compile(r"-----BEGIN [A-Z0-9 ]{0,40}PRIVATE KEY-----"),
    re.compile(_EDGE + _GITHUB),
    re.compile(_EDGE + r"xox[bpars]-[A-Za-z0-9-]{10,}"),
    re.compile(_EDGE + r"eyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{2,}\.[A-Za-z0-9_-]{2,}"),
    re.compile(_EDGE + r"glpat-[A-Za-z0-9_-]{20,}"),
    re.compile(_EDGE + r"AIza[A-Za-z0-9_-]{35}"),
    re.compile(r"\b[a-z][a-z0-9+.-]{0,31}://[^\s:/?#@]+:[^\s/?#@]+@[^\s/?#@]", re.I),
    re.compile(
        rf"(?<![A-Za-z0-9_-])(?:[A-Za-z0-9]+[_-])*{_KEY_NAME}[\"']?[ \t]*[:=][ \t]*[\"']?"
        r"(?![<*$])[^\s\"',;&]{8,}",
        re.I,
    ),
    re.compile(r"\bbearer(?:[ \t]*:[ \t]*|[ \t]+)[A-Za-z0-9._~+/-]{20,}=*", re.I),
)


def _has_secret(text: str) -> bool:
    normalized = _normalize(text)
    return any(p.search(normalized) for p in SECRET_PATTERNS)


def _normalize(text: str) -> str:
    """`text` in NFKC with every zero-width and format character dropped."""
    if text.isascii():
        return text
    return _FORMAT_CHARS.sub("", unicodedata.normalize("NFKC", text))


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
        if stage in ("output", "tool_output") and _has_secret(text):
            return Verdict(allowed=False, text=text, reasons=(SECRET,))
        masked = redact(text)
        return Verdict(allowed=True, text=masked, reasons=(PII,) if masked != text else ())
