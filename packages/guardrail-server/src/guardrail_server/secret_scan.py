"""The secret detector: values shaped like a credential, which must never leave in an answer or a
tool output. Regex only (suggested patterns); a match blocks, it is never masked and passed on.
The reason never holds the value.

The text is normalized before the match (`normalize`): NFKC, so full-width letters read as ASCII,
then every zero-width and format character is dropped, so `sk` + U+200B + `-` reads as `sk-`.

The vendor shapes use the widths of the chassis `redact` SECRET list. Two keep a longer minimum
here, because this check blocks where `redact` only masks: `sk-` takes 16 characters, and `bearer`
takes 20, so "Bearer authentication" in prose passes. `key_assignment` needs a value of 8 or more.

Every pattern runs in linear time: each starts only where a token can start (a word boundary, or
not after an identifier character), and no two quantifiers compete for the same characters.
"""

from __future__ import annotations

import re
import unicodedata

__all__ = ["SECRET_PATTERNS", "find_secret", "normalize"]

# Hand copy of chassis/fakes/guardrail.py;
# packages/contract-suites/tests/test_guardrail_pattern_drift.py pins the drift.
_FORMAT_CHARS = re.compile(
    "[\u00ad\u061c\u180e\u200b-\u200f\u202a-\u202e\u2060-\u2064\u2066-\u206f\ufeff"
    "\ufff9-\ufffb\U000e0001\U000e0020-\U000e007f]"
)
"""Zero-width and format (Unicode `Cf`) characters a reader does not see: the soft hyphen, the
zero-width space, joiners and marks, the bidi controls, the word joiner, the BOM, the tag
characters."""

_KEY_NAME = r"(?:password|passwd|pwd|secret|token|api[_-]?key|access[_-]?key)"
"""suggested: the credential words a key name ends with, as in the chassis `redact` SECRET list.
`token` alone, so `max_tokens` is not one."""

_EDGE = r"(?<![A-Za-z0-9])"
"""A token starts where no letter or digit is just before it (`_sk-...` included)."""
_GITHUB = r"(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})"

SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("api_key", re.compile(_EDGE + r"sk-[A-Za-z0-9_-]{16,}")),
    ("aws_access_key", re.compile(_EDGE + r"(?:AKIA|ASIA)[A-Z0-9]{16}(?![A-Za-z0-9])")),
    ("private_key", re.compile(r"-----BEGIN [A-Z0-9 ]{0,40}PRIVATE KEY-----")),
    ("github_token", re.compile(_EDGE + _GITHUB)),
    ("slack_token", re.compile(_EDGE + r"xox[bpars]-[A-Za-z0-9-]{10,}")),
    ("jwt", re.compile(_EDGE + r"eyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{2,}\.[A-Za-z0-9_-]{2,}")),
    ("gitlab_token", re.compile(_EDGE + r"glpat-[A-Za-z0-9_-]{20,}")),
    ("google_api_key", re.compile(_EDGE + r"AIza[A-Za-z0-9_-]{35}")),
    (
        "url_credentials",
        re.compile(r"\b[a-z][a-z0-9+.-]{0,31}://[^\s:/?#@]+:[^\s/?#@]+@[^\s/?#@]", re.I),
    ),
    (
        "key_assignment",
        re.compile(
            rf"(?<![A-Za-z0-9_-])(?:[A-Za-z0-9]+[_-])*{_KEY_NAME}[\"']?[ \t]*[:=][ \t]*[\"']?"
            r"(?![<*$])[^\s\"',;&]{8,}",
            re.I,
        ),
    ),
    ("bearer", re.compile(r"\bbearer(?:[ \t]*:[ \t]*|[ \t]+)[A-Za-z0-9._~+/-]{20,}=*", re.I)),
)
"""Ordered: a vendor shape is named before the generic `key_assignment` and `bearer` take it."""


def normalize(text: str) -> str:
    """`text` in NFKC with every zero-width and format character dropped."""
    if text.isascii():
        return text
    return _FORMAT_CHARS.sub("", unicodedata.normalize("NFKC", text))


_KEY_WORD = re.compile(r"pass|pwd|secret|token|key", re.I)
"""Every `_KEY_NAME` word holds one of these. `key_assignment` is tried only when one is present:
it is linear, but on a long run it is the costliest pattern (as `_SECRET_START` in the chassis)."""


def find_secret(text: str) -> str | None:
    """The kind of the first secret-shaped value in `text`, or None."""
    normalized = normalize(text)
    for kind, pattern in SECRET_PATTERNS:
        if kind == "key_assignment" and not _KEY_WORD.search(normalized):
            continue
        if pattern.search(normalized):
            return kind
    return None
