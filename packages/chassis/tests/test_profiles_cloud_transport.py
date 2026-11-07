"""The `cloud` profile refuses plaintext stores, a plaintext tool gateway, and `auth: none`.

Review R4-30 (S3 and Valkey without TLS), R5a-25 (the MCP gateway without https), and the R2
note (`auth: none` in `cloud`). Each check runs in `build_ports` before any adapter is built, so
nothing connects, and each message names the variable, never its value.
"""

from __future__ import annotations

import pytest
from chassis import profiles
from chassis.adapters.litellm.client import BASE_URL_VAR as LITELLM_CLIENT_BASE_URL_VAR
from chassis.adapters.mcp.gateway import URL_VAR as GATEWAY_URL_VAR
from chassis.adapters.oidc import JWKS_URL_VAR as OIDC_ADAPTER_JWKS_URL_VAR
from chassis.adapters.valkey.state import URL_VAR as VALKEY_URL_VAR
from chassis.profiles import (
    ALLOW_PLAINTEXT_STORES_VAR,
    LITELLM_BASE_URL_VAR,
    LITELLM_MCP_URL_VAR,
    OIDC_JWKS_URL_VAR,
    S3_SECURE_VAR,
    AdapterNotAllowed,
    AdapterSpec,
    build_ports,
)
from chassis.profiles import (
    VALKEY_URL_VAR as PROFILES_VALKEY_URL_VAR,
)

TRANSPORT_ENV = (
    S3_SECURE_VAR,
    PROFILES_VALKEY_URL_VAR,
    LITELLM_MCP_URL_VAR,
    LITELLM_BASE_URL_VAR,
    OIDC_JWKS_URL_VAR,
    ALLOW_PLAINTEXT_STORES_VAR,
)


class _Reached(Exception):
    """Raised by the `resolve` spy: `build_ports` got past its checks to the first adapter."""


@pytest.fixture
def resolved(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Clear the transport variables and replace `resolve` with a spy, so a test can tell a
    refusal (no adapter built, nothing connects) from passing the checks."""
    for name in TRANSPORT_ENV:
        monkeypatch.delenv(name, raising=False)
    calls: list[str] = []

    def spy(port: str, adapter: str, **kw: object) -> object:
        calls.append(port)
        raise _Reached(port)

    monkeypatch.setattr(profiles, "resolve", spy)
    return calls


def _refused(profile: profiles.Profile, spec: AdapterSpec | None = None) -> str:
    with pytest.raises(AdapterNotAllowed) as err:
        build_ports(profile, spec)
    return str(err.value)


def test_the_variable_names_match_the_adapters() -> None:
    """profiles.py keeps its own copy of each name, so it never imports a store SDK."""
    assert PROFILES_VALKEY_URL_VAR == VALKEY_URL_VAR
    assert LITELLM_MCP_URL_VAR == GATEWAY_URL_VAR
    assert S3_SECURE_VAR == "CONFIG_S3_SECURE"
    assert LITELLM_BASE_URL_VAR == LITELLM_CLIENT_BASE_URL_VAR
    assert OIDC_JWKS_URL_VAR == OIDC_ADAPTER_JWKS_URL_VAR


@pytest.mark.parametrize("config", ["s3", "minio"])
@pytest.mark.parametrize("value", ["false", "0", "no", " OFF "])
def test_cloud_refuses_s3_without_tls_and_does_not_connect(
    config: str, value: str, resolved: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """R4-30 spec: given profile `cloud` and `CONFIG_S3_SECURE=false`, when `build_ports` runs,
    then it raises with the variable name and does not connect."""
    monkeypatch.setenv(S3_SECURE_VAR, value)
    message = _refused("cloud", AdapterSpec(config=config))
    assert S3_SECURE_VAR in message
    assert ALLOW_PLAINTEXT_STORES_VAR in message
    assert value.strip() not in message
    assert resolved == []


@pytest.mark.parametrize(
    "url", ["redis://valkey:6379/0", "valkey://valkey:6379/0", "REDIS://valkey.example.com:6379"]
)
def test_cloud_refuses_valkey_without_tls_and_does_not_connect(
    url: str, resolved: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(PROFILES_VALKEY_URL_VAR, url)
    message = _refused("cloud")
    assert PROFILES_VALKEY_URL_VAR in message
    assert "valkey:6379" not in message and "example.com" not in message
    assert resolved == []


@pytest.mark.parametrize(
    "url", ["rediss://valkey:6380/0", "valkeys://valkey:6380/0", "unix:///run/valkey.sock"]
)
def test_cloud_accepts_valkey_over_tls_or_a_unix_socket(
    url: str, resolved: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(PROFILES_VALKEY_URL_VAR, url)
    monkeypatch.setenv(S3_SECURE_VAR, "true")
    with pytest.raises(_Reached):
        build_ports("cloud")


def test_the_override_lets_plaintext_stores_through_in_cloud(
    resolved: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(S3_SECURE_VAR, "false")
    monkeypatch.setenv(PROFILES_VALKEY_URL_VAR, "redis://valkey:6379/0")
    monkeypatch.setenv(ALLOW_PLAINTEXT_STORES_VAR, "1")
    with pytest.raises(_Reached):
        build_ports("cloud")


def test_an_override_other_than_1_does_not_count(
    resolved: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(S3_SECURE_VAR, "false")
    monkeypatch.setenv(ALLOW_PLAINTEXT_STORES_VAR, "yes")
    assert S3_SECURE_VAR in _refused("cloud")


def test_local_allows_plaintext_stores_and_gateway(
    resolved: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(S3_SECURE_VAR, "false")
    monkeypatch.setenv(PROFILES_VALKEY_URL_VAR, "redis://valkey:6379/0")
    monkeypatch.setenv(LITELLM_MCP_URL_VAR, "http://gw.example.com/mcp")
    with pytest.raises(_Reached):
        build_ports("local")


@pytest.mark.parametrize(
    "url",
    [
        "http://gw.example.com/mcp",
        "http://litellm.platform.svc.cluster.local:4000/mcp/",
        "http://127.0.0.1:4000/mcp",
    ],
)
def test_cloud_refuses_a_gateway_url_that_is_not_https(
    url: str, resolved: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """R5a-25 spec: given `LITELLM_MCP_URL=http://gw.example.com/mcp` in profile `cloud`, when
    the gateway tools are built, then construction fails and names the variable. In `cloud` an
    in-cluster or loopback name is no exemption, and the store override does not apply."""
    monkeypatch.setenv(LITELLM_MCP_URL_VAR, url)
    monkeypatch.setenv(ALLOW_PLAINTEXT_STORES_VAR, "1")
    message = _refused("cloud")
    assert LITELLM_MCP_URL_VAR in message
    assert "example.com" not in message and "svc.cluster" not in message
    assert "127.0.0.1" not in message
    assert resolved == []


def test_cloud_accepts_an_https_gateway_url(
    resolved: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(LITELLM_MCP_URL_VAR, "https://gw.example.com/mcp")
    with pytest.raises(_Reached):
        build_ports("cloud")


def test_cloud_refuses_auth_none(resolved: list[str]) -> None:
    """R2 note: `auth: none` in `cloud` would run `/v1/run` as the anonymous principal."""
    message = _refused("cloud", AdapterSpec(auth="none"))
    assert message.startswith("auth: 'none'")
    assert resolved == []


@pytest.mark.parametrize("profile", ["fake", "local"])
def test_auth_none_is_still_the_default_outside_cloud(profile: profiles.Profile) -> None:
    assert profiles.merge_adapters(profile).auth == "none"


@pytest.mark.parametrize(
    "spec",
    [AdapterSpec(), AdapterSpec(guardrails="none"), AdapterSpec(model="vllm")],
    ids=["model-and-guardrails", "model", "guardrails"],
)
@pytest.mark.parametrize("url", ["http://litellm.platform.svc:4000/v1", "http://127.0.0.1:4000"])
def test_cloud_refuses_a_litellm_base_url_that_is_not_https(
    spec: AdapterSpec, url: str, resolved: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review HIGH 3: the model and the guardrails adapters send `LITELLM_API_KEY` to
    `LITELLM_BASE_URL`, so `cloud` needs `https` there too, with no exemption."""
    monkeypatch.setenv(LITELLM_BASE_URL_VAR, url)
    monkeypatch.setenv(ALLOW_PLAINTEXT_STORES_VAR, "1")
    message = _refused("cloud", spec)
    assert LITELLM_BASE_URL_VAR in message
    assert "platform.svc" not in message and "127.0.0.1" not in message
    assert resolved == []


def test_cloud_accepts_an_https_litellm_base_url(
    resolved: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(LITELLM_BASE_URL_VAR, "https://litellm.example.com/v1")
    with pytest.raises(_Reached):
        build_ports("cloud")


@pytest.mark.parametrize(
    "query",
    [
        "ssl_cert_reqs=none",
        "ssl_cert_reqs=optional",
        "SSL_CERT_REQS=None",
        "ssl_cert_reqs=required&ssl_check_hostname=false",
        "ssl_check_hostname=False",
    ],
)
def test_cloud_refuses_a_valkey_url_that_weakens_tls(
    query: str, resolved: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review MEDIUM 9: `rediss://` with the certificate or host name check off is no TLS."""
    monkeypatch.setenv(PROFILES_VALKEY_URL_VAR, f"rediss://valkey.example.com:6380/0?{query}")
    message = _refused("cloud")
    assert PROFILES_VALKEY_URL_VAR in message
    assert "example.com" not in message and query not in message
    assert resolved == []


@pytest.mark.parametrize(
    "query", ["ssl_cert_reqs=required", "SSL_CERT_REQS=Required&ssl_check_hostname=true", ""]
)
def test_cloud_accepts_a_valkey_url_that_keeps_the_checks(
    query: str, resolved: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(PROFILES_VALKEY_URL_VAR, f"rediss://valkey:6380/0?{query}")
    with pytest.raises(_Reached):
        build_ports("cloud")


@pytest.mark.parametrize("url", ["http://127.0.0.1:8080/jwks", "http://idp.example.com/jwks"])
def test_cloud_refuses_a_jwks_url_that_is_not_https(
    url: str, resolved: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review MEDIUM 9: in `cloud` the JWKS is `https` only; the adapter's loopback exemption is
    for a local fake IdP."""
    monkeypatch.setenv(OIDC_JWKS_URL_VAR, url)
    message = _refused("cloud")
    assert OIDC_JWKS_URL_VAR in message
    assert "127.0.0.1" not in message and "example.com" not in message
    assert resolved == []


def test_cloud_accepts_an_https_jwks_url(
    resolved: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(OIDC_JWKS_URL_VAR, "https://idp.example.com/jwks")
    with pytest.raises(_Reached):
        build_ports("cloud")


MALFORMED = "http://[s3cr3t-host-value]/x"
"""`urlsplit` raises on it, and its own message would echo the bracketed part."""


@pytest.mark.parametrize(
    "var",
    [LITELLM_MCP_URL_VAR, LITELLM_BASE_URL_VAR, OIDC_JWKS_URL_VAR, PROFILES_VALKEY_URL_VAR],
)
def test_a_malformed_url_is_refused_naming_only_the_variable(
    var: str, resolved: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review LOW 10."""
    monkeypatch.setenv(var, MALFORMED)
    with pytest.raises(AdapterNotAllowed) as err:
        build_ports("cloud")
    assert var in str(err.value)
    assert "s3cr3t" not in str(err.value)
    assert err.value.__cause__ is None and err.value.__suppress_context__
    assert resolved == []


def test_the_override_is_logged_naming_the_variable(
    resolved: list[str], monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Review LOW 12: a plaintext store let through in `cloud` leaves a warning."""
    monkeypatch.setenv(S3_SECURE_VAR, "false")
    monkeypatch.setenv(PROFILES_VALKEY_URL_VAR, "redis://valkey-host:6379/0")
    monkeypatch.setenv(ALLOW_PLAINTEXT_STORES_VAR, "1")
    with caplog.at_level("WARNING", logger="chassis.profiles"), pytest.raises(_Reached):
        build_ports("cloud")
    text = "\n".join(r.getMessage() for r in caplog.records if r.levelname == "WARNING")
    assert ALLOW_PLAINTEXT_STORES_VAR in text
    assert S3_SECURE_VAR in text and PROFILES_VALKEY_URL_VAR in text
    assert "valkey-host" not in text


def test_the_override_logs_nothing_when_the_stores_use_tls(
    resolved: list[str], monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv(ALLOW_PLAINTEXT_STORES_VAR, "1")
    with caplog.at_level("WARNING", logger="chassis.profiles"), pytest.raises(_Reached):
        build_ports("cloud")
    assert not [r for r in caplog.records if r.name == "chassis.profiles"]
