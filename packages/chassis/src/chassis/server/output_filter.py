"""Two cheap checks on the answer, in the chassis, with no port call (PoC-8, 022 H-6, ADR-008).

**Secret-shaped values** (`find_secret`). The same patterns as the guardrail server's secret guard
(`guardrail_server.secret_scan`, which the chassis never imports): an `sk-` key, an AWS access
key id, a private key block, a `*_API_KEY=` style assignment, a bearer token. suggested: the
patterns. The stream guard (`chassis.server.stream_guard`) uses them on a streamed answer, which
the `output` stage cannot hold back. A complete answer is checked by the `output` stage through
`GuardrailPort`, which carries the same patterns in both stacks.

**Image and link URLs** (`mask_urls`, `mask_answer`). A markdown image in an answer leaks data the
moment a client renders it, and default-deny egress does not cover the client (red-team case
`exfil-04-markdown-image`). So the destination of every markdown image or link (`![alt](url)`,
`[text](url)`), every reference definition (`[id]: url`), and every HTML `src=` or `href=` whose
URL is absolute and whose host is not on `spec.guardrails.output_url_allow` is removed: the
markup stays, the destination becomes empty (`![alt]()`), so nothing is fetched and the reader
sees where a link was. A relative URL (`/help`, `#part`) stays. Only `http` and `https` can be
allowed; any other scheme with a destination (`data:`, `javascript:`, `mailto:`) is removed.
Bare URLs in text are not touched: a client fetches them only on a click. Counted as
`chassis.output_url_masked` (suggested).
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from urllib.parse import urlsplit

from chassis.core.events import Delta, End, Event

__all__ = [
    "SECRET_PATTERNS",
    "URL_MASKED",
    "find_secret",
    "host_allowed",
    "mask_answer",
    "mask_urls",
]

URL_MASKED = "chassis.output_url_masked"

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
"""A copy of `guardrail_server.secret_scan.SECRET_PATTERNS`; keep the two the same."""

_DESTINATIONS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?P<pre>\]\(\s*<?)(?P<url>[^\s()<>]+)"),
    re.compile(r"(?P<pre>\]:[ \t]*<?)(?P<url>[^\s<>]+)"),
    re.compile(r"""(?P<pre>\b(?:src|href)\s*=\s*["']?)(?P<url>[^\s"'<>]+)""", re.IGNORECASE),
)
"""Markdown inline destinations, reference definitions, and HTML `src`/`href` values."""


def find_secret(text: str) -> str | None:
    """The kind of the first secret-shaped value in `text`, or None. Never the value."""
    for kind, pattern in SECRET_PATTERNS:
        if pattern.search(text):
            return kind
    return None


def host_allowed(host: str, allow: Sequence[str]) -> bool:
    """`host` matches an entry: the host itself, `*.<domain>` for its subdomains, or `*`."""
    host = host.lower().rstrip(".")
    for entry in allow:
        entry = entry.lower().strip()
        if entry == "*":
            return True
        if entry.startswith("*.") and host.endswith(entry[1:]):
            return True
        if host == entry:
            return True
    return False


def _keeps(url: str, allow: Sequence[str]) -> bool:
    """A relative URL, or an http(s) URL to an allowed host."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    if not parts.scheme and not url.startswith("//"):
        return True
    if parts.scheme.lower() not in ("http", "https", ""):
        return False
    return host_allowed(parts.hostname or "", allow)


def mask_urls(text: str, allow: Sequence[str]) -> tuple[str, int]:
    """`text` with each destination to a host not on `allow` removed, and how many."""
    count = 0

    def replace(match: re.Match[str]) -> str:
        nonlocal count
        if _keeps(match["url"], allow):
            return match[0]
        count += 1
        return match["pre"]

    for pattern in _DESTINATIONS:
        text = pattern.sub(replace, text)
    return text, count


def mask_answer(events: Sequence[Event], allow: Sequence[str]) -> tuple[list[Event] | None, int]:
    """A complete run's events with `mask_urls` applied to the answer, or None when nothing
    changed. The deltas are joined first (a link can span two), and become one delta when masked;
    `end.output.text` is masked too."""
    deltas = "".join(e.text for e in events if isinstance(e, Delta))
    masked_deltas, count = mask_urls(deltas, allow)
    out: list[Event] = []
    delta_done = False
    for event in events:
        if isinstance(event, Delta):
            if count == 0:
                out.append(event)
            elif not delta_done:
                out.append(Delta(text=masked_deltas))
                delta_done = True
            continue
        if isinstance(event, End) and event.output and isinstance(event.output.get("text"), str):
            text, n = mask_urls(event.output["text"], allow)
            if n:
                count += n
                event = event.model_copy(update={"output": {**event.output, "text": text}})
        out.append(event)
    return (out if count else None), count
