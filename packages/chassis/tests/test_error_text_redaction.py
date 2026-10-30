"""Upstream error text loses every key before it leaves an adapter, through one function
(`chassis.redaction.redact_error_text`; review R7-20)."""

from __future__ import annotations

from types import ModuleType

import pytest
from chassis import redaction
from chassis.adapters.a2a import plain
from chassis.adapters.litellm import client, guardrails

KEY = "sk-abcdefgh1234"  # pragma: allowlist secret


@pytest.mark.parametrize(
    ("text", "api_key", "expected"),
    [
        (f"upstream said {KEY}", None, "upstream said [redacted]"),
        ("bad key shortkey1", "shortkey1", "bad key [redacted]"),  # pragma: allowlist secret
        (
            "Received API Key = sk-...abcd, Key Hash (Token) = 9f8e7d.",
            None,
            "Received API Key = [redacted], Key Hash (Token) = [redacted].",
        ),
        ("no key here", "unused-key", "no key here"),
    ],
)
def test_redact_error_text_masks_the_key_when_the_text_holds_one(
    text: str, api_key: str | None, expected: str
) -> None:
    """The configured key, any `sk-` shape, and LiteLLM's 401 echo become `[redacted]`."""
    masked = redaction.redact_error_text(text, api_key)

    assert masked == expected


@pytest.mark.parametrize("module", [client, guardrails, plain])
def test_adapter_uses_the_one_redact_error_text(module: ModuleType) -> None:
    """The LiteLLM client, the LiteLLM guardrails, and the plain A2A client share one function."""
    used = vars(module).get("redact_error_text")

    assert used is redaction.redact_error_text


def test_plain_remote_text_masks_a_key_when_the_remote_echoes_one() -> None:
    """The plain A2A client's log text masks a key shape the remote sends back."""
    text = plain.redact(f"remote failed: {KEY}")

    assert KEY not in text
    assert "[redacted]" in text
