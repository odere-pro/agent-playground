"""The chassis's own PII and secret redaction: regex only (PoC-7, backlog 022 H-6).

It covers secrets, emails, phone numbers, and ids (card numbers, IBANs, US social security numbers)
in the chassis's logs and in the events it publishes. Named-entity redaction (names, places) runs
in LiteLLM's guardrails and in the OTel Collector log pipeline, never here: a named-entity model
adds about 0.5 to 1 GiB per replica and breaks ADR-001's memory estimate. Standard library only.

Each match becomes `<ENTITY>`, the form Presidio's anonymizer uses by default, so a record reads
the same whichever layer redacted it. suggested: the entity names and the patterns.

`SECRET` runs first, so a key is masked whole before the phone pattern could take its digits. It
covers the shapes in `SECRET_PREFIXES` (Bearer and Basic credentials, vendor keys, JWTs, PEM
private keys), then the value of a `password=`-style assignment (`SECRET_ASSIGNMENT`) and the
password in a URL (`SECRET_USERINFO`); the key, the user, and the host stay as text.

`redact_error_text` is separate: it strips credentials (a configured key, any `sk-` shape, and
LiteLLM's 401 echo) from upstream error text, for every adapter that passes such text on.

A card match is `<CREDIT_CARD>` only when its digits pass the Luhn check. A phone match needs a
leading `+` or a separator (space, dot, dash, or parentheses), so a bare digit run such as an order
id stays, and so does a bare run that failed the Luhn check. A phone match is also kept when the
whole match is a valid ISO date (`2026-10-29`), an IPv4 address (four groups of 0 to 255), or an
x.y.z version (three groups of 1 or 2 digits). Any other dotted or dashed run (`612.345.678`,
`0171-12-34`) is still masked. Every pattern stays linear on long input: the email pattern starts
only where a local part can start, and each secret shape starts only at a word edge.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

_LOCAL = r"[A-Za-z0-9._%+-]"
_EMAIL_BODY = _LOCAL + r"+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"
_EMAIL_AT_RESUME = re.compile(_EMAIL_BODY)
"""The unanchored email, tried only where the last match ended, as `re.sub` used to."""

_EDGE = r"(?<![A-Za-z0-9])"
_TOKEN = r"(?:(?=[A-Za-z]*[0-9._~+/-])[A-Za-z0-9._~+/-]{8,}|[A-Za-z]{16,})"
SECRET_PREFIXES: tuple[tuple[str, str], ...] = (
    # `%20`: the URL-encoded space of `OTEL_EXPORTER_OTLP_HEADERS` (`Authorization=Bearer%20x`).
    ("bearer", r"\b(?i:bearer)(?:[ \t]*:[ \t]*|[ \t]+|%20)" + _TOKEN + "=*"),
    (
        "basic",
        r"\b(?i:basic)[ \t]+(?:(?=[A-Za-z]*[0-9+/=])[A-Za-z0-9+/]{8,}|[A-Za-z]{16,})={0,2}",
    ),
    ("openai", _EDGE + r"sk-[A-Za-z0-9_-]{8,}"),
    ("github", _EDGE + r"(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})"),
    ("slack", _EDGE + r"xox[bpars]-[A-Za-z0-9-]{10,}"),
    ("gitlab", _EDGE + r"glpat-[A-Za-z0-9_-]{20,}"),
    ("aws", _EDGE + r"(?:AKIA|ASIA)[A-Z0-9]{16}(?![A-Za-z0-9])"),
    ("google", _EDGE + r"AIza[A-Za-z0-9_-]{35}"),
    ("jwt", _EDGE + r"eyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{2,}\.[A-Za-z0-9_-]{2,}"),
    (
        "pem",
        r"-----BEGIN [A-Z0-9 ]{0,40}PRIVATE KEY-----"
        r"(?:(?:[^-]|-(?!----))*-----END [A-Z0-9 ]{0,40}PRIVATE KEY-----"
        r"|(?:\r?\n[A-Za-z0-9+/=]+(?=\r?\n|$))*)",
    ),
)
"""Each credential shape as (kind, regex source), the one list of them. `sk-` covers OpenAI and
LiteLLM keys, `sk-proj-`, and `sk-ant-`. A Bearer or Basic credential needs a digit or a symbol,
or 16 letters, so `Bearer authentication` stays. A PEM key is masked through the next END line
(any label); with no END line, the header and the whole base64 lines right after it. Each shape
starts at a fixed prefix and a word edge, and has a minimum length, so ordinary words stay.

The guardrail server's secret scan (`guardrail_server/secret_scan.py`, a hand copy, since the
guardrail server never imports `chassis`) and the chassis fake guardrail (`fakes/guardrail.py`)
do not match this list yet; review items R7-14 and R7-19 align them in another change.
suggested: the kinds, the prefixes, and the minimum lengths."""

_SECRET_KEY = (
    r"(?i:password|passwd|pwd|secret|token(?!s|[_-]?(?:type|count|usage|limit|id)(?![A-Za-z0-9]))"
    r"|credentials?|(?:api|access|private|master)[_-]?key)"
)
SECRET_ASSIGNMENT = re.compile(
    _EDGE + _SECRET_KEY + r"[A-Za-z0-9_-]{0,40}[\"']?[ \t]*[=:][ \t]*"
    r"(?P<q>[\"'])?(?P<value>(?(q)[^\"'\n]+|[^\s\"',;&(){}\[\]<>][^\s\"',;&)}\]>]*))"
)
"""An assignment to a key that holds a secret word, any case, with `=` or `:` and optional
quotes: `password`, `passwd`, `pwd`, `secret`, `token`, `credential(s)`, and `api`, `access`,
`private`, or `master` with `key`, then up to 40 more key characters (`db_password: 'x'`,
`"api_key": "x"`, `aws_secret_access_key = x`, `LITELLM_MASTER_KEY=x`). Token counts and fields
(`max_tokens`, `token_type`, `token_usage`) are not secrets. Only a non-empty `value` is
replaced. A quoted value runs to the next quote. An unquoted value stops at a space, a quote,
`,;&`, or a closing bracket, and never starts with a bracket. suggested: the key names."""

SECRET_USERINFO = re.compile(
    r"(?<![A-Za-z0-9+.-])[A-Za-z][A-Za-z0-9+.-]*://[^\s:/@]*:(?P<value>[^\s/]+)@"
)
"""The password in a URL's userinfo: the part after the user name's `:` and before the `@`, with or
without a user name. Only `value` is replaced; it runs to the last `@` before the path."""

_SECRET = re.compile("|".join(f"(?:{source})" for _, source in SECRET_PREFIXES))
_SECRET_START = re.compile(
    r"(?i:bearer|basic)|sk-|gh[pousr]_|github_pat_|xox|glpat-|AKIA|ASIA|AIza|eyJ|-----BEGIN "
)
"""The literal each `SECRET_PREFIXES` shape starts with. The full alternation is tried only when
one is present: it costs about 3x the other patterns on a long text, at every position. Add the
start literal here with every new shape."""

PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("SECRET", _SECRET),
    ("EMAIL_ADDRESS", re.compile(rf"(?<!{_LOCAL}){_EMAIL_BODY}")),
    ("IBAN_CODE", re.compile(r"\b[A-Z]{2}\d{2}(?: ?[A-Z0-9]{4}){2,7}(?: ?[A-Z0-9]{1,4})?\b")),
    ("CREDIT_CARD", re.compile(r"\b\d{4}(?:[ -]?\d{4}){2}[ -]?\d{1,7}\b")),
    ("US_SSN", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    (
        "PHONE_NUMBER",
        re.compile(r"(?<![\w-])\+?\d{1,3}?[ .-]?\(?\d{2,4}\)?(?:[ .-]?\d{2,4}){2,3}(?![\w-])"),
    ),
)
"""Ordered: a secret is replaced first, then an IBAN or a card number before the phone pattern
could take part of it. `SECRET` also replaces the value of a `SECRET_ASSIGNMENT` and of a
`SECRET_USERINFO`, after the shapes, so `token: Bearer x...` loses the whole token."""

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
    if text.isdigit() or _NOT_A_PHONE.fullmatch(text):
        return text
    return "<PHONE_NUMBER>"


def _passes_luhn(digits: str) -> bool:
    """The Luhn checksum every card number carries: double every second digit from the right."""
    total = 0
    for index, char in enumerate(reversed(digits)):
        value = int(char) * (2 if index % 2 else 1)
        total += value - 9 if value > 9 else value
    return total % 10 == 0


def _card_or_kept(match: re.Match[str]) -> str:
    text = match.group(0)
    digits = text.replace(" ", "").replace("-", "")
    return "<CREDIT_CARD>" if _passes_luhn(digits) else text


def _masked_value(match: re.Match[str]) -> str:
    """The match with only its `value` group replaced; the key, separators, and quotes stay."""
    text, start = match.group(0), match.start()
    return text[: match.start("value") - start] + "<SECRET>" + text[match.end("value") - start :]


def _redact_secrets(text: str, pattern: re.Pattern[str]) -> str:
    if _SECRET_START.search(text):
        text = pattern.sub("<SECRET>", text)
    text = SECRET_ASSIGNMENT.sub(_masked_value, text)
    return SECRET_USERINFO.sub(_masked_value, text)


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
    if entity == "SECRET":
        return _redact_secrets(text, pattern)
    if entity == "CREDIT_CARD":
        return pattern.sub(_card_or_kept, text)
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


KEY_SHAPE = re.compile(r"sk-[A-Za-z0-9_-]{8,}")
"""A LiteLLM or OpenAI style key."""
# LiteLLM's 401 echo: `Received API Key = sk-...abcd, Key Hash (Token) = <hash>`. The value runs
# to the next space, comma, or semicolon; a trailing period stays as text.
_LITELLM_ECHO = re.compile(r"(Received API Key = |Key Hash \(Token\) = )[^\s,;]*[^\s,;.]")


def redact_error_text(text: str, api_key: str | None) -> str:
    """Strip the configured key, anything shaped like one, and LiteLLM's 401 echo of the key
    suffix and hash. Upstream error text travels into events, proxy bodies, logs, and `/v1/run`.
    """
    if api_key:
        text = text.replace(api_key, "[redacted]")
    text = _LITELLM_ECHO.sub(r"\1[redacted]", text)
    return KEY_SHAPE.sub("[redacted]", text)
