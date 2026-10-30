"""PoC-5 seams, offline: `spec.trust`, the `remote` lane's config, the uncorrelated cap, the
`ToolPort` keyword, and the PoC-5 registry entries (plan
`docs/plans/2026-10-02-poc-05-sandboxed.md`, sections 2.1, 2.5, 2.6, 2.12, 3).

Exit criterion 7 (offline part): the chassis refuses `untrusted` outside the `remote` lane at
config load, before anything starts.
"""

from __future__ import annotations

import inspect
from typing import Any

import pytest
from chassis.ports.tool import NoTools, ToolError, ToolPort
from chassis.profiles import REGISTRY, AdapterNotAvailable, resolve
from chassis.server.config import RELOADABLE, ChassisConfig, load_config
from chassis.server.config_loader import restart_required
from pydantic import ValidationError

AUTH = {"scheme": "bearer", "token_env": "REMOTE_TOKEN"}
REMOTE = {"connector": "remote", "url": "http://chassis-echo-remote:9000", "auth": AUTH}
SIDECAR = {"connector": "sidecar", "url": "http://127.0.0.1:9000"}
INPROCESS = {"connector": "inprocess", "handle": "echo_python:handle"}
CLOUD_ADAPTERS = {"model": "litellm", "config": "s3", "telemetry": "otel", "tools": "mcp"}


def config(profile: str = "fake", **spec: Any) -> dict[str, Any]:
    return {"profile": profile, "agent": {"name": "a", "version": "1"}, "spec": spec}


def refused(data: dict[str, Any]) -> str:
    with pytest.raises(ValidationError) as caught:
        load_config(data)
    return str(caught.value)


# --- spec.trust -------------------------------------------------------------------------------


@pytest.mark.parametrize("engine", [SIDECAR, INPROCESS], ids=["sidecar", "inprocess"])
def test_untrusted_outside_the_remote_lane_is_refused(engine: dict[str, Any]) -> None:
    """Criterion 7: `spec.trust: untrusted` in the sidecar or inprocess lane is refused at load."""
    message = refused(config(trust="untrusted", engine=engine))
    assert "spec.trust: untrusted needs spec.engine.connector: remote" in message


def test_untrusted_in_the_remote_lane_loads() -> None:
    """Criterion 7: the allowed control, `untrusted` in the remote lane loads."""
    loaded = load_config(config(trust="untrusted", engine=REMOTE))
    assert loaded.spec.trust == "untrusted"
    assert loaded.spec.engine.connector == "remote"


def test_trusted_in_the_remote_lane_loads() -> None:
    """Criterion 7: a trusted workload may run in the remote lane too."""
    assert load_config(config(trust="trusted", engine=REMOTE)).spec.trust == "trusted"


def test_cloud_without_trust_is_refused_naming_the_field() -> None:
    """Criterion 7: the cloud profile refuses a config with no `spec.trust`, naming the field."""
    message = refused(config("cloud", adapters=CLOUD_ADAPTERS, engine=SIDECAR))
    assert "spec.trust" in message
    assert "cloud" in message


def test_cloud_with_trust_loads() -> None:
    """Criterion 7: the allowed control, cloud with `spec.trust` set loads."""
    loaded = load_config(config("cloud", trust="trusted", adapters=CLOUD_ADAPTERS, engine=SIDECAR))
    assert loaded.spec.trust == "trusted"


@pytest.mark.parametrize("profile", ["fake", "local"])
def test_trust_defaults_to_trusted_outside_cloud(profile: str) -> None:
    """Criterion 7: outside cloud, `spec.trust` defaults to trusted."""
    assert load_config(config(profile)).spec.trust == "trusted"


def test_an_unknown_trust_value_is_refused() -> None:
    """Criterion 7: a trust value other than trusted or untrusted is refused."""
    assert "trust" in refused(config(trust="maybe"))


# --- defaults ---------------------------------------------------------------------------------


def test_defaults_load() -> None:
    """Section 3: an empty spec loads with the PoC-5 defaults."""
    loaded = load_config(config())
    assert loaded.spec.trust == "trusted"
    assert loaded.spec.engine.connector == "sidecar"
    assert loaded.spec.engine.auth is None
    assert loaded.spec.engine.probe_timeout_s == 2.0
    assert loaded.spec.limits.uncorrelated_tokens_per_minute == 20_000


def test_the_shipped_configs_still_load() -> None:
    """Section 3: every config in `packages/chassis/configs` loads as trusted."""
    from pathlib import Path

    configs = Path(__file__).resolve().parents[3] / "packages" / "chassis" / "configs"
    for path in sorted(configs.glob("*.yaml")):
        assert load_config(path).spec.trust == "trusted", path.name


# --- the remote lane --------------------------------------------------------------------------


def test_the_remote_lane_requires_auth() -> None:
    """Scope, remote connector: the remote lane refuses a config with no `spec.engine.auth`."""
    message = refused(config(engine={"connector": "remote", "url": REMOTE["url"]}))
    assert "spec.engine.auth" in message


def test_the_remote_lane_requires_a_url() -> None:
    """Scope, remote connector: the remote lane refuses a config with no `spec.engine.url`."""
    message = refused(config(engine={"connector": "remote", "auth": AUTH}))
    assert "spec.engine.url" in message


@pytest.mark.parametrize("engine", [SIDECAR, INPROCESS], ids=["sidecar", "inprocess"])
def test_auth_is_refused_outside_the_remote_lane(engine: dict[str, Any]) -> None:
    """Section 2.1: an auth block outside the remote lane is refused."""
    assert "spec.engine.auth" in refused(config(engine={**engine, "auth": AUTH}))


def test_the_auth_block_shape() -> None:
    """Section 2.1: the bearer auth block loads and maps back with both token env names."""
    loaded = load_config(
        config(engine={**REMOTE, "auth": {**AUTH, "previous_token_env": "REMOTE_TOKEN_OLD"}})
    )
    auth = loaded.spec.engine.auth
    assert auth is not None
    assert (auth.scheme, auth.token_env, auth.previous_token_env) == (
        "bearer",
        "REMOTE_TOKEN",
        "REMOTE_TOKEN_OLD",
    )
    assert loaded.spec.engine.as_mapping()["auth"] == {
        "scheme": "bearer",
        "token_env": "REMOTE_TOKEN",
        "previous_token_env": "REMOTE_TOKEN_OLD",
    }


@pytest.mark.parametrize(
    "auth",
    [
        {"scheme": "sigv4", "token_env": "REMOTE_TOKEN"},
        {"scheme": "bearer"},
        {"scheme": "bearer", "token_env": "not a name"},
        {"scheme": "bearer", "token_env": "REMOTE_TOKEN", "token": "secret"},
    ],
    ids=["scheme", "no-token-env", "bad-env-name", "inline-token"],
)
def test_a_bad_auth_block_is_refused(auth: dict[str, Any]) -> None:
    """Section 2.1: a bad scheme, a missing or bad env name, or an inline token is refused."""
    refused(config(engine={**REMOTE, "auth": auth}))


@pytest.mark.parametrize(
    "url",
    [
        "ftp://host:9000",
        "http://user:pw@host:9000",
        "http://host:9000/?q=1",
        "http://host:9000/#frag",
        "http://host",
        "http://:9000",
        "not a url",
    ],
)
def test_a_bad_remote_url_is_refused(url: str) -> None:
    """Section 2.1: a URL with a bad scheme, credentials, query, fragment, or port is refused."""
    assert "spec.engine.url" in refused(config(engine={**REMOTE, "url": url}))


@pytest.mark.parametrize(
    "url", ["http://chassis-echo-remote:9000", "https://agent.example:443/runtimes/a1/invocations"]
)
def test_a_good_remote_url_loads(url: str) -> None:
    """Section 2.1: the allowed control, an http or https remote URL with a port loads."""
    assert load_config(config(engine={**REMOTE, "url": url})).spec.engine.url == url


def test_cloud_remote_needs_https() -> None:
    """Section 2.1: cloud refuses a plain http remote URL and loads an https one."""
    message = refused(config("cloud", trust="untrusted", adapters=CLOUD_ADAPTERS, engine=REMOTE))
    assert "https" in message
    loaded = load_config(
        config(
            "cloud",
            trust="untrusted",
            adapters=CLOUD_ADAPTERS,
            engine={**REMOTE, "url": "https://remote.example:443"},
        )
    )
    assert loaded.spec.engine.connector == "remote"


def test_cloud_remote_refuses_uds() -> None:
    """Section 2.1: cloud refuses a Unix socket for the remote lane."""
    engine = {**REMOTE, "url": "https://remote.example:443", "uds": "/tmp/remote.sock"}
    message = refused(config("cloud", trust="untrusted", adapters=CLOUD_ADAPTERS, engine=engine))
    assert "spec.engine.uds" in message


def test_a_remote_uds_loads_outside_cloud() -> None:
    """Section 2.1: the allowed control, a remote Unix socket loads outside cloud."""
    loaded = load_config(config(engine={**REMOTE, "uds": "/tmp/remote.sock"}))
    assert loaded.spec.engine.uds == "/tmp/remote.sock"


@pytest.mark.parametrize("value", [0, -1.0])
def test_probe_timeout_must_be_positive(value: float) -> None:
    """Section 2.1: a zero or negative probe timeout is refused."""
    refused(config(engine={**REMOTE, "probe_timeout_s": value}))


# --- the uncorrelated cap ---------------------------------------------------------------------


def test_the_uncorrelated_cap_accepts_zero_and_refuses_negative() -> None:
    """Section 3: the uncorrelated cap takes 0 and refuses a negative value."""
    zero = load_config(config(limits={"uncorrelated_tokens_per_minute": 0}))
    assert zero.spec.limits.uncorrelated_tokens_per_minute == 0
    refused(config(limits={"uncorrelated_tokens_per_minute": -1}))


def test_the_uncorrelated_cap_reloads_and_trust_does_not() -> None:
    """Section 3: the cap reloads live; a change of `spec.trust` needs a restart."""
    old = load_config(config(engine=REMOTE))
    capped = load_config(config(engine=REMOTE, limits={"uncorrelated_tokens_per_minute": 5}))
    assert restart_required(old, capped) == []
    untrusted = load_config(config(engine=REMOTE, trust="untrusted"))
    assert restart_required(old, untrusted) == ["spec.trust"]
    assert not any(path == "spec.trust" or path.startswith("spec.trust.") for path in RELOADABLE)


def test_the_published_schema_names_the_new_fields() -> None:
    """Section 3: the JSON schema names trust, auth, the probe timeout, and the cap."""
    schema = ChassisConfig.model_json_schema()
    defs = schema["$defs"]
    assert defs["Spec"]["properties"]["trust"]["enum"] == ["trusted", "untrusted"]
    assert {"scheme", "token_env", "previous_token_env"} <= set(
        defs["EngineAuthSpec"]["properties"]
    )
    assert {"auth", "probe_timeout_s"} <= set(defs["EngineSpec"]["properties"])
    assert "uncorrelated_tokens_per_minute" in defs["LimitsSpec"]["properties"]


# --- ToolPort ---------------------------------------------------------------------------------


@pytest.mark.parametrize("call", [ToolPort.call, NoTools.call], ids=["port", "none"])
def test_tool_call_takes_an_idempotency_key_keyword(call: Any) -> None:
    """Section 2.6, ToolPort: `call` takes `idempotency_key` as a keyword with default None."""
    parameter = inspect.signature(call).parameters["idempotency_key"]
    assert parameter.kind is inspect.Parameter.KEYWORD_ONLY
    assert parameter.default is None


async def test_no_tools_still_refuses_with_a_key() -> None:
    """Section 2.6, ToolPort: `NoTools` refuses a call with a key as `unknown_tool`."""
    with pytest.raises(ToolError) as caught:
        await NoTools().call("note_write", {}, idempotency_key="tk1:abc")
    assert caught.value.code == "unknown_tool"


def test_the_tool_error_codes_are_documented() -> None:
    """Section 2.6, ToolPort: the `ToolError` docstring names every error code."""
    doc = ToolError.__doc__ or ""
    for code in (
        "unknown_tool",
        "bad_arguments",
        "idempotency_key_required",
        "tool_denied",
        "tool_unavailable",
    ):
        assert code in doc, code


# --- the registry -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("port", "adapter", "module"),
    [
        ("engine", "remote", "chassis.adapters.a2a.remote"),
        ("tools", "mcp", "chassis.adapters.mcp.gateway"),
    ],
)
def test_the_poc05_adapters_are_lazy_registry_entries(port: str, adapter: str, module: str) -> None:
    """Task T01: the remote and mcp adapters are lazy registry factories."""
    entry = REGISTRY[port][adapter]
    assert callable(entry), "a lazy factory, not a placeholder string"
    try:
        __import__(module)
    except ModuleNotFoundError:
        with pytest.raises(AdapterNotAvailable, match="arrives in PoC-5"):
            resolve(port, adapter)
