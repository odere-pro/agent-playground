"""`McpGatewayTools.from_env` refuses a gateway URL that would send the key in cleartext.

Review R5a-25: a non-`https` `LITELLM_MCP_URL` is allowed only to a loopback host or an
in-cluster name, so `Bearer <LITELLM_API_KEY>` never crosses an outside network in plain http.
The refusal names the variable, never the URL or the key. Profile `cloud` requires `https`
always; that check is in `chassis.profiles` (`test_profiles_cloud_transport.py`).
"""

from __future__ import annotations

import subprocess
import sys

import pytest
from chassis.adapters.mcp.gateway import API_KEY_VAR, URL_VAR, McpGatewayTools

KEY = "sk-gateway-url-test-key"


@pytest.mark.parametrize(
    "url",
    [
        "https://gw.example.com/mcp",
        "HTTPS://gw.example.com/mcp",
        "http://127.0.0.1:4000/mcp",
        "http://127.8.9.10/mcp",
        "http://localhost:4000/mcp",
        "http://[::1]:4000/mcp",
        "http://gateway/mcp/",
        "http://litellm.platform.svc:4000/mcp/",
        "http://litellm.poc05-platform.svc.cluster.local:4000/mcp/",
        "http://LiteLLM.Platform.SVC.cluster.local:4000/mcp/",
    ],
)
def test_https_loopback_and_in_cluster_urls_are_accepted(
    url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(URL_VAR, url)
    monkeypatch.setenv(API_KEY_VAR, KEY)
    assert McpGatewayTools.from_env().url == url


@pytest.mark.parametrize(
    "url",
    [
        "http://gw.example.com/mcp",
        "http://10.0.0.5:4000/mcp",
        "http://[fe80::1]:4000/mcp",
        "http://[2001:db8::1]/mcp",
        "http://167772165/mcp",
        "http://litellm.svc.example.com/mcp",
        "http:///mcp",
        "ftp://gateway/mcp",
        "ws://localhost/mcp",
        "gw.example.com/mcp",
    ],
)
def test_a_cleartext_url_to_an_outside_host_is_refused_naming_the_variable(
    url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(URL_VAR, url)
    monkeypatch.setenv(API_KEY_VAR, KEY)
    with pytest.raises(ValueError, match=URL_VAR) as err:
        McpGatewayTools.from_env()
    message = str(err.value)
    assert url not in message
    assert "example.com" not in message
    assert KEY not in message


def test_a_malformed_url_is_refused_naming_only_the_variable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Review LOW 10: `urlsplit`'s own error would echo part of the URL."""
    monkeypatch.setenv(URL_VAR, "http://[s3cr3t-host-value]/mcp")
    monkeypatch.setenv(API_KEY_VAR, KEY)
    with pytest.raises(ValueError, match=URL_VAR) as err:
        McpGatewayTools.from_env()
    assert "s3cr3t" not in str(err.value)
    assert err.value.__cause__ is None and err.value.__suppress_context__


@pytest.mark.parametrize("module", ["chassis.adapters.mcp.gateway", "chassis.adapters.oidc"])
def test_the_loopback_check_loads_no_a2a_sdk(module: str) -> None:
    """Review LOW 13: `is_loopback_host` lives in `chassis.adapters.loopback`, so the gateway
    and OIDC adapters do not load the A2A lane or a2a-sdk to use it."""
    code = (
        f"import sys, {module}; "
        "print(any(n == 'a2a' or n.startswith(('a2a.', 'chassis.adapters.a2a')) "
        "for n in sys.modules))"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "False"
