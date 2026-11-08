"""PoC-5 on kind: `McpGatewayTools` against the real LiteLLM MCP gateway, with the chassis's own
virtual key (plan sections 2.6 and 2.7; written in T17, run in T21).

Exit criterion 3 (every internal service refuses without the chassis's credential; with it the
same call works) for the MCP gateway, and the checklist item "the real `ToolPort` adapter passes
the same suite as the fake tools". H07 (a call with no valid key gets no tool) and H08 (a tool
not on the key's allow-list is not listed and is refused). Each refusal has its allowed control:
`glossary_lookup` with the chassis's key.

Marked `network` and skipped unless `KIND_DRILLS=1` (`poc_harness.drills`). The cluster task sets:

- `POC05_GATEWAY_MCP_URL`: the gateway's MCP URL as the test process reaches it (for example a
  port-forward to `litellm:4000`, path `/mcp/`).
- `POC05_CHASSIS_VIRTUAL_KEY`: the chassis's virtual key, exported from the seed step's Secret
  into the environment, never on a command line.
- `POC05_GATEWAY_AUTH_HEADER` (optional): the header the gateway reads the key from, if not
  `Authorization` (the plan's fallback is `x-litellm-api-key`).

`deploy/kind/poc05/run.sh with-gateway CMD...` sets both for CMD (a port-forward to LiteLLM
and the key from its Secret, never in argv or output).

The gateway's real behavior to record on the first run (plan section 2.6, spike items): which of
`unknown_tool` or `tool_denied` an unlisted tool gives when the call reaches it (pinned
`tool_denied` in `test_unlisted_probe_is_refused_with_its_control`; since CH-14 the adapter
refuses an unlisted name first, so the test sends the call past it on purpose), and that
`_meta.idempotency_key` reaches the tool server (the write cases of the contract show it: the
same key twice is one note).
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

import pytest
from chassis.adapters.mcp.gateway import McpGatewayTools
from chassis.ports.tool import ToolDefinition, ToolError
from chassis_contracts.tool import DENIED_CODES, KnownCall, ToolPortContract
from poc_harness.drills import KIND_SANDBOX, required_env

pytestmark = KIND_SANDBOX

URL_VAR = "POC05_GATEWAY_MCP_URL"
KEY_VAR = "POC05_CHASSIS_VIRTUAL_KEY"
HEADER_VAR = "POC05_GATEWAY_AUTH_HEADER"
# The gateway lists a tool as `<server>-<tool>` (bring-up note, item 3), so these are its names.
ALLOWED = KnownCall("fake_tools-glossary_lookup", {"term": "SLM"})
PROBE = "fake_tools-unlisted_probe"
PROBE_MARKER = "UNLISTED-PROBE-MARKER-7f3a"  # `fake_mcp_server.PROBE_MARKER`
NOT_A_KEY = "sk-poc05-not-a-real-key-0000"


def _listed(name: str) -> ToolDefinition:
    """A read-only definition for `name`, put in an adapter's cache so a call really goes out."""
    return ToolDefinition(name=name, description=name, read_only=True)


def _settings() -> tuple[str, str, str]:
    url, key = required_env(URL_VAR, KEY_VAR)  # the cluster task exports them
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
        return KnownCall("fake_tools-note_write", {"text": "poc05 kind contract"})

    @pytest.fixture
    def denied_name(self) -> str:
        return PROBE


async def test_unlisted_probe_is_refused_with_its_control(
    chassis_tools: McpGatewayTools,
) -> None:
    """H08: the allowed call works with the chassis's key (the control); the probe is not listed
    and its call is refused, and its marker never comes back. Two layers: the adapter refuses an
    unlisted name before any request (CH-14), and the gateway's allow-list, the hard limit
    (ADR-001 item 5), refuses the same call when it does go out."""
    ok = await chassis_tools.call(ALLOWED.name, ALLOWED.arguments)
    assert ok.is_error is False
    assert "small language model" in str(ok.content).lower()

    assert PROBE not in [d.name for d in chassis_tools.list_tools()]
    with pytest.raises(ToolError) as info:
        await chassis_tools.call(PROBE, {})
    # CH-14: not listed, so the adapter answers `unknown_tool` before any request (was the
    # gateway's `tool_denied`, pinned on kind 2026-10-08).
    assert info.value.code == "unknown_tool"
    assert info.value.retryable is False
    assert PROBE_MARKER not in str(info.value)

    # The gateway itself: list the probe in the adapter's cache, so `tools/call` really reaches
    # the gateway with the chassis's key, and the gateway's allow-list refuses it.
    chassis_tools._tools = {**chassis_tools._tools, PROBE: _listed(PROBE)}
    with pytest.raises(ToolError) as at_gateway:
        await chassis_tools.call(PROBE, {})
    assert at_gateway.value.code == "tool_denied"  # the gateway's own, pinned on kind 2026-10-08
    assert at_gateway.value.retryable is False
    assert PROBE_MARKER not in str(at_gateway.value)


async def test_a_key_the_gateway_did_not_issue_gets_no_tool(
    chassis_tools: McpGatewayTools,
) -> None:
    """H07: with a key the gateway did not issue, nothing is listed and the allowed call is
    refused; the same call with the chassis's key works (the control). The adapter refuses the
    unlisted name first (CH-14); then the same call is sent to the gateway with the stranger's key,
    and the gateway refuses it (ADR-001 item 5)."""
    ok = await chassis_tools.call(ALLOWED.name, ALLOWED.arguments)
    assert ok.is_error is False

    stranger = await _gateway(NOT_A_KEY)
    try:
        assert list(stranger.list_tools()) == []
        with pytest.raises(ToolError) as info:
            await stranger.call(ALLOWED.name, ALLOWED.arguments)
        assert info.value.code in DENIED_CODES
        assert NOT_A_KEY not in str(info.value)

        # The gateway itself: list the allowed tool for the stranger, so the call goes out.
        (listed,) = [d for d in chassis_tools.list_tools() if d.name == ALLOWED.name]
        stranger._tools = {ALLOWED.name: listed}
        with pytest.raises(ToolError) as at_gateway:
            await stranger.call(ALLOWED.name, ALLOWED.arguments)
        assert at_gateway.value.code == "tool_denied"  # 401 or 403 from the gateway
        assert NOT_A_KEY not in str(at_gateway.value)
    finally:
        await stranger.aclose()
