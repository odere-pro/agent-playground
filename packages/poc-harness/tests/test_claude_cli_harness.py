"""The Claude CLI stand-in reads the custom headers the way the real CLI does."""

from __future__ import annotations

from poc_harness.claude_cli import _headers

TRACE = "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01"
RUN_SECRET = "a-run-capability"  # pragma: allowlist secret (a test value, not a credential)


def test_every_custom_header_line_arrives() -> None:
    # Contract v8 D: echo-claude-agent sends two lines, traceparent and x-chassis-run-secret.
    env = {
        "ANTHROPIC_AUTH_TOKEN": "placeholder",
        "ANTHROPIC_CUSTOM_HEADERS": f"traceparent: {TRACE}\nx-chassis-run-secret: {RUN_SECRET}",
    }

    headers = _headers(env)

    assert headers["traceparent"] == TRACE
    assert headers["x-chassis-run-secret"] == RUN_SECRET


def test_a_blank_or_malformed_line_adds_no_header() -> None:
    env = {"ANTHROPIC_AUTH_TOKEN": "placeholder", "ANTHROPIC_CUSTOM_HEADERS": "\nnot-a-header\n"}

    headers = _headers(env)

    assert set(headers) == {"authorization", "anthropic-version"}
