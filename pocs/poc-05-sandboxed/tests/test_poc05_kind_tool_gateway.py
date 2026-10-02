"""PoC-5 on kind: `McpGatewayTools` against the real LiteLLM MCP gateway, with the chassis's own
virtual key (plan sections 2.6 and 2.7; written in T17, run in T21).

Exit criterion 3 (every internal service refuses without the chassis's credential; with it the
same call works) for the MCP gateway, and the checklist item "the real `ToolPort` adapter passes
the same suite as the fake tools". H07 (a call with no valid key gets no tool) and H08 (a tool
not on the key's allow-list is not listed and is refused). Each refusal has its allowed control:
`glossary_lookup` with the chassis's key.

Marked `network` and skipped unless `POC05_KIND=1` (`poc05_conftest.py`). The cluster task sets:

- `POC05_GATEWAY_MCP_URL`: the gateway's MCP URL as the test process reaches it (for example a
  port-forward to `litellm:4000`, path `/mcp/`).
- `POC05_CHASSIS_VIRTUAL_KEY`: the chassis's virtual key, exported from the seed step's Secret
  into the environment, never on a command line.
- `POC05_GATEWAY_AUTH_HEADER` (optional): the header the gateway reads the key from, if not
  `Authorization` (the plan's fallback is `x-litellm-api-key`).

The gateway's real behavior to record on the first run (plan section 2.6, spike items): which of
`unknown_tool` or `tool_denied` an unlisted tool gives (then pin it in
`test_unlisted_probe_is_refused_with_its_control`), and that `_meta.idempotency_key` reaches the
tool server (the write cases of the contract show it: the same key twice is one note).
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

import pytest
from chassis.adapters.mcp.gateway import McpGatewayTools
from chassis.ports.tool import ToolError
from chassis_contracts.tool import DENIED_CODES, KnownCall, ToolPortContract

URL_VAR = "POC05_GATEWAY_MCP_URL"
KEY_VAR = "POC05_CHASSIS_VIRTUAL_KEY"
HEADER_VAR = "POC05_GATEWAY_AUTH_HEADER"
ALLOWED = KnownCall("glossary_lookup", {"term": "SLM"})
PROBE = "unlisted_probe"
PROBE_MARKER = "UNLISTED-PROBE-MARKER-7f3a"  # `fake_mcp_server.PROBE_MARKER`
NOT_A_KEY = "sk-poc05-not-a-real-key-0000"


def _settings() -> tuple[str, str, str]:
    url, key = os.environ.get(URL_VAR), os.environ.get(KEY_VAR)
    if not url or not key:
        pytest.skip(f"set {URL_VAR} and {KEY_VAR} (the cluster task exports them)")
    return url, key, os.environ.get(HEADER_VAR) or "Authorization"


async def _gateway(key: str | None = None) -> McpGatewayTools:
    url, chassis_key, header = _settings()
    port = McpGatewayTools(url, key or chassis_key, auth_header=header)
    await port.refresh()
    return port


@pytest.fixture
async def chassis_tools() -> AsyncIterator[McpGatewayTools]:
    port = await _gateway()
    try:
        yield port
    finally:
        await port.aclose()


class TestPoc05KindGatewayContract(ToolPortContract):
    """The same suite the fake tools pass, through the real gateway with the chassis's key."""

    @pytest.fixture
    def tool_port(self, chassis_tools: McpGatewayTools) -> McpGatewayTools:
        return chassis_tools

    @pytest.fixture
    def known_call(self) -> KnownCall:
        return ALLOWED

    @pytest.fixture
    def write_call(self) -> KnownCall:
        return KnownCall("note_write", {"text": "poc05 kind contract"})

    @pytest.fixture
    def denied_name(self) -> str:
        return PROBE


async def test_unlisted_probe_is_refused_with_its_control(
    chassis_tools: McpGatewayTools,
) -> None:
    """H08: the allowed call works with the chassis's key (the control); the probe is not listed
    and its call is refused, and its marker never comes back."""
    ok = await chassis_tools.call(ALLOWED.name, ALLOWED.arguments)
    assert ok.is_error is False
    assert "small language model" in str(ok.content).lower()

    assert PROBE not in [d.name for d in chassis_tools.list_tools()]
    with pytest.raises(ToolError) as info:
        await chassis_tools.call(PROBE, {})
    assert info.value.code in DENIED_CODES
    assert info.value.retryable is False
    assert PROBE_MARKER not in str(info.value)


async def test_a_key_the_gateway_did_not_issue_gets_no_tool(
    chassis_tools: McpGatewayTools,
) -> None:
    """H07: with a key the gateway did not issue, nothing is listed and the allowed call is
    refused; the same call with the chassis's key works (the control)."""
    ok = await chassis_tools.call(ALLOWED.name, ALLOWED.arguments)
    assert ok.is_error is False

    stranger = await _gateway(NOT_A_KEY)
    try:
        assert list(stranger.list_tools()) == []
        with pytest.raises(ToolError) as info:
            await stranger.call(ALLOWED.name, ALLOWED.arguments)
        assert info.value.code in DENIED_CODES
        assert NOT_A_KEY not in str(info.value)
    finally:
        await stranger.aclose()
