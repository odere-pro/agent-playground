"""PoC-8 (014 H-7, ADR-007 item 7): workload logs never pass through the chassis, so the OTel
Collector's log pipeline redacts them by regex. It masks the same entity set as
`chassis.redaction` (ADR-008: emails, phone numbers, card numbers, IBANs, US SSNs; R1-07:
secrets), with the same `<ENTITY>` names, on the log body and on every attribute. suggested: the
patterns.

The patterns are RE2 in the Collector. The ones here use only syntax RE2 and Python share, so this
test runs them with `re`, in the Collector's order, after undoing the OTTL string escapes (`\\` and
`\"`). Offline: no Collector runs.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml
from chassis.redaction import PATTERNS

ROOT = Path(__file__).resolve().parents[3]
COLLECTOR = ROOT / "deploy/compose/poc07/otel-collector.yaml"
_CALL = re.compile(r"^(replace_pattern|replace_all_patterns)\((.*)\)$")
_STRING = re.compile(r'"((?:[^"\\]|\\.)*)"')
_ESCAPE = re.compile(r'\\([\\"])')


def _unquote(literal: str) -> str:
    """An OTTL string literal's value: OTTL takes Go escapes, so `\\\\b` is the regex `\\b`."""
    return _ESCAPE.sub(r"\1", literal)


def _config() -> dict[str, Any]:
    data = yaml.safe_load(COLLECTOR.read_text())
    assert isinstance(data, dict)
    return data


def _statements() -> list[str]:
    config = _config()
    logs = config["service"]["pipelines"]["logs"]
    redactors = [p for p in logs["processors"] if p.startswith("transform/")]
    assert len(redactors) == 1, "one redaction transform in the logs pipeline"
    assert logs["processors"].index(redactors[0]) < logs["processors"].index("batch")
    return [
        s
        for block in config["processors"][redactors[0]]["log_statements"]
        for s in block["statements"]
    ]


def _rules() -> list[tuple[str, str, str, str]]:
    """Each statement as (function, target, pattern, replacement)."""
    rules = []
    for statement in _statements():
        match = _CALL.match(statement.strip())
        assert match, f"not a replace call: {statement}"
        function, args = match.groups()
        target = args.split(",", 1)[0].strip()
        strings = [_unquote(s) for s in _STRING.findall(args)]
        pattern, replacement = strings[-2], strings[-1]
        rules.append((function, target, pattern, replacement))
    return rules


def _redact(text: str, target: str) -> str:
    for _, on, pattern, replacement in _rules():
        if on == target:
            text = re.sub(pattern, replacement, text)
    return text


ENTITIES = sorted({entity for entity, _ in PATTERNS} | {"SECRET"})
"""The chassis's entity set. `SECRET` (R1-07) is named too, so this branch and one that has it in
`chassis.redaction` both compare the full set."""
SAMPLES = [
    ("write to ann.lee@example.com today", "write to <EMAIL_ADDRESS> today"),
    ("call +1 415 555 0100 now", "call <PHONE_NUMBER> now"),
    ("call 415-555-0100 now", "call <PHONE_NUMBER> now"),
    ("card 4111 1111 1111 1111 ok", "card <CREDIT_CARD> ok"),
    ("card 4111-1111-1111-1111 ok", "card <CREDIT_CARD> ok"),
    ("iban DE89 3704 0044 0532 0130 00 ok", "iban <IBAN_CODE> ok"),
    ("iban GB82WEST12345698765432 ok", "iban <IBAN_CODE> ok"),
    ("ssn 123-45-6789 ok", "ssn <US_SSN> ok"),
    ("no entity here, version 1.2.3", "no entity here, version 1.2.3"),
    ("key sk-" + "a1b2c3d4e5f6a7b8c9d0 ok", "key <SECRET> ok"),  # pragma: allowlist secret
    ("tok ghp_" + "A" * 36 + " ok", "tok <SECRET> ok"),  # pragma: allowlist secret
    ("tok xoxb-" + "1234567890-abcdef ok", "tok <SECRET> ok"),  # pragma: allowlist secret
    ("id AKIA" + "Q3EXAMPLE7KEY2ZZ ok", "id <SECRET> ok"),  # pragma: allowlist secret
    ("jwt eyJ" + "hbGciOi.eyJzdWIi.c2lnbmF0 ok", "jwt <SECRET> ok"),  # pragma: allowlist secret
    ("auth: Bearer " + "abcdefghij123456 ok", "auth: <SECRET> ok"),  # pragma: allowlist secret
    ("db password=" + "hunter2hunter2 ok", "db <SECRET> ok"),  # pragma: allowlist secret
    (
        'cfg {"api_key": "' + 'v4lue-with space"} ok',
        'cfg {"<SECRET>} ok',
    ),  # pragma: allowlist secret
    (
        "-----BEGIN RSA PRIVATE KEY-----\nMIIB+abc=\n"  # pragma: allowlist secret
        "-----END RSA PRIVATE KEY----- ok",
        "<SECRET> ok",
    ),  # pragma: allowlist secret
    ("disk-cleanup-job-1234 ran", "disk-cleanup-job-1234 ran"),
    (
        "h Authorization=Bearer%20" + "0123456789abcdef0123 ok",
        "h Authorization=<SECRET> ok",
    ),  # pragma: allowlist secret
    ("hdr Basic " + "dXNlcjpwYXNzd29yZA== ok", "hdr <SECRET> ok"),  # pragma: allowlist secret
    ("env access_token=" + "abc123def456 ok", "env <SECRET> ok"),  # pragma: allowlist secret
    (
        "env AWS_SECRET_ACCESS_KEY=" + "wJalrXUtnFEMI ok",
        "env <SECRET> ok",
    ),  # pragma: allowlist secret
    (
        "db postgres://app:" + "pw123456@db:5432/x ok",
        "db <SECRET>db:5432/x ok",
    ),  # pragma: allowlist secret
    ("token_count: 12 and token_type: bearer", "token_count: 12 and token_type: bearer"),
    ("uses Bearer authentication and basic auth", "uses Bearer authentication and basic auth"),
    ("max_tokens=1000 and tokens: 5", "max_tokens=1000 and tokens: 5"),
]


@pytest.mark.parametrize(("text", "expected"), SAMPLES)
@pytest.mark.parametrize("target", ["body", "attributes"])
def test_the_log_pipeline_masks_each_entity(text: str, expected: str, target: str) -> None:
    assert _redact(text, target) == expected


def test_the_log_pipeline_masks_the_chassis_entity_set_on_body_and_attributes() -> None:
    rules = _rules()
    for target in ("body", "attributes"):
        names = sorted({r[3].strip("<>") for r in rules if r[1] == target})
        assert names == ENTITIES, target
    for function, target, _, _ in rules:
        assert (function, target) in {
            ("replace_pattern", "body"),
            ("replace_all_patterns", "attributes"),
        }


def test_the_phone_rule_runs_last_on_each_target() -> None:
    """A card number or an IBAN is masked whole before the phone pattern could take part of it."""
    for target in ("body", "attributes"):
        order = [r[3] for r in _rules() if r[1] == target]
        assert order[-1] == "<PHONE_NUMBER>", order


def test_the_patterns_hold_no_dollar_the_collector_would_expand() -> None:
    for _, _, pattern, _ in _rules():
        assert "$" not in pattern.replace("$$", "")
