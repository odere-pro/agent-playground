"""The chassis config the kit generates, and the argv of each process.

The model adapter is the `local` profile's: `litellm`, pointed at the fake model server with a
dummy key (`LITELLM_BASE_URL`, `LITELLM_API_KEY` in the chassis env). Tools are the chassis's
fake tools, served on its `/mcp`. Only the engine section changes with the lane.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from typing import Any, Literal

import yaml

from bakeoff.registry import Engine, Lane, check_lane

__all__ = [
    "CHASSIS_TOKEN_ENV",
    "DUMMY_MODEL_KEY",
    "ROUTES",
    "WORKLOAD_TOKEN_ENV",
    "Hosted",
    "chassis_argv",
    "chassis_config",
    "chassis_env_vars",
    "dump_config",
    "workload_argv",
    "workload_env_vars",
]

CHASSIS_TOKEN_ENV = "BAKEOFF_REMOTE_TOKEN"
"""`spec.engine.auth.token_env`: the connector sends it; the remote listener accepts it."""
WORKLOAD_TOKEN_ENV = "BAKEOFF_WORKLOAD_TOKEN"
"""The workload's `--require-token-env`."""
DUMMY_MODEL_KEY = "bakeoff-dummy-key"  # pragma: allowlist secret
"""Not a key. The fake model server ignores it."""
_PY = sys.executable

Route = Literal["big-default", "local-small"]
ROUTES: tuple[Route, ...] = ("big-default", "local-small")


@dataclass(frozen=True)
class Hosted:
    """A real model behind a LiteLLM URL, instead of the in-process fake.

    `key` is the LiteLLM key the chassis sends, never a provider key. It stays out of `repr`.
    """

    url: str
    key: str = field(repr=False)
    route: Route = "big-default"


def chassis_config(
    engine: Engine, lane: Lane, *, workload_port: int | None, route: Route = "big-default"
) -> dict[str, Any]:
    """The `ChassisConfig` mapping for `engine` in `lane`. The trust rule is checked first.

    `route` is `spec.model.route`, the only field a model switch changes."""
    check_lane(engine, lane)
    section: dict[str, Any]
    if lane == "inprocess":
        section = {"connector": "inprocess", "handle": engine.handle}
    else:
        if workload_port is None:
            raise ValueError(f"the {lane} lane needs the workload's port")
        section = {"connector": lane, "url": f"http://127.0.0.1:{workload_port}"}
        if lane == "remote":
            section["auth"] = {"scheme": "bearer", "token_env": CHASSIS_TOKEN_ENV}
    return {
        "profile": "local",
        "agent": {"name": "echo", "version": "0.0.1"},
        "spec": {
            "trust": "trusted" if engine.trusted else "untrusted",
            "adapters": {
                "model": "litellm",
                "config": "memory",
                "telemetry": "memory",
                "tools": "fake",
                "state": "memory",
            },
            "engine": section,
            "model": {"route": route},
            "prompt": {"version": "simplifier-v1"},
        },
    }


def dump_config(config: dict[str, Any]) -> str:
    return yaml.safe_dump(config, sort_keys=False)


def chassis_argv(
    config_path: str,
    *,
    port: int,
    proxy_port: int,
    remote_ip: str | None = None,
    remote_port: int | None = None,
) -> list[str]:
    argv = [
        _PY,
        "-c",
        "from chassis.server.cli import main; main()",
        "serve",
        "--config",
        config_path,
        "--port",
        str(port),
        "--proxy-port",
        str(proxy_port),
        "--startup-wait-s",
        "60",
        "--drain-delay-s",
        "0",
        "--drain-timeout-s",
        "2",
    ]
    if remote_ip is not None and remote_port is not None:
        argv += ["--remote-proxy-host", remote_ip, "--remote-proxy-port", str(remote_port)]
    return argv


def chassis_env_vars(
    lane: Lane, *, fake_port: int, proxy_port: int, token: str, hosted: Hosted | None = None
) -> dict[str, str]:
    """The chassis process's variables beyond `PATH` and `HOME`.

    With `hosted`, the model adapter points at its URL with its LiteLLM key; `fake_port` is
    unused. No provider key is ever passed."""
    if hosted is not None:
        env = {"LITELLM_BASE_URL": hosted.url, "LITELLM_API_KEY": hosted.key}
    else:
        env = {
            "LITELLM_BASE_URL": f"http://127.0.0.1:{fake_port}/v1",
            "LITELLM_API_KEY": DUMMY_MODEL_KEY,
        }
    if lane == "inprocess":  # the handle runs in this process and calls the loopback proxy
        env["CHASSIS_MODEL_URL"] = f"http://127.0.0.1:{proxy_port}/v1"
        env["CHASSIS_TOOL_URL"] = f"http://127.0.0.1:{proxy_port}/mcp"
    if lane == "remote":
        env[CHASSIS_TOKEN_ENV] = token
    return env


def workload_argv(
    engine: Engine, lane: Lane, *, port: int, main_js: str | None = None
) -> list[str]:
    """The command that serves the engine's workload on `127.0.0.1:port`."""
    token_args = ["--require-token-env", WORKLOAD_TOKEN_ENV] if lane == "remote" else []
    if engine.runner == "node":
        if main_js is None:
            raise ValueError("a node engine needs the path of its main.js")
        return ["node", main_js, *token_args]
    if engine.handle is None:
        raise ValueError(f"{engine.name} has no handle to serve")
    return [
        _PY,
        "-c",
        "from workload_a2a.cli import main; main()",
        "serve",
        "--handle",
        engine.handle,
        "--port",
        str(port),
        "--drain-timeout-s",
        "2",
        *token_args,
    ]


def workload_env_vars(
    engine: Engine, lane: Lane, *, port: int, model_url: str, tool_url: str, token: str
) -> dict[str, str]:
    """The workload's variables beyond `PATH` and `HOME`: the allow-list, nothing else."""
    env = {"CHASSIS_MODEL_URL": model_url, "CHASSIS_TOOL_URL": tool_url}
    if engine.runner == "node":
        env.update({"HOST": "127.0.0.1", "PORT": str(port)})
    if lane == "remote":
        env["CHASSIS_API_TOKEN"] = token
        env[WORKLOAD_TOKEN_ENV] = token
    return env
