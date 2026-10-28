"""The engines, the lanes, and the trust rule.

Trust rule (PoC-5, ADR-001 item 8): an `untrusted` engine runs only in the `remote` lane. The
chassis config refuses `spec.trust: untrusted` with any other connector; `lanes_for` and
`check_lane` apply the same rule before a process starts.
"""

from __future__ import annotations

import importlib.util
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

__all__ = [
    "ENGINES",
    "LANES",
    "ROOT",
    "Engine",
    "check_hosted",
    "check_lane",
    "engine_names",
    "get_engine",
    "lanes_for",
]

Lane = Literal["inprocess", "sidecar", "remote"]
LANES: tuple[Lane, ...] = ("inprocess", "sidecar", "remote")
Runner = Literal["python", "node", "kind"]

ROOT = Path(__file__).resolve().parents[4]
"""The repo root: packages/bakeoff/src/bakeoff/registry.py is four levels down."""
WORKLOADS = Path("packages/workloads")
BAKEOFF_SCRIPT = Path("packages/fake-model-server/scripts/bakeoff.yaml")
CLAUDE_SCRIPT = Path("packages/fake-model-server/scripts/bakeoff-claude.yaml")
SMOLAGENTS_SCRIPT = WORKLOADS / "echo-smolagents/tests/scripts/smolagents.yaml"
TS_MAIN = WORKLOADS / "echo-typescript/dist/src/main.js"
MESSAGES_ROUTE = "chassis.server.model_proxy_messages"


def _py(workload: str, package: str, *files: str) -> tuple[str, ...]:
    return tuple(f"{WORKLOADS}/{workload}/src/{package}/{name}" for name in files)


@dataclass(frozen=True)
class Engine:
    """One bake-off engine. Paths are relative to the repo root."""

    name: str
    runner: Runner
    trusted: bool = True
    handle: str | None = None
    """Python engines: `module:attribute`, served by `workload-a2a serve`."""
    script: Path = BAKEOFF_SCRIPT
    """The fake model server's script for this engine."""
    mapping_files: tuple[str, ...] = field(default_factory=tuple)
    """The source files counted as the engine's mapping (`bakeoff.lines`)."""
    always_skip: str | None = None

    def skip_reason(self) -> str | None:
        """Why the engine cannot run here, or None."""
        if self.always_skip:
            return self.always_skip
        if self.runner == "node" and not (ROOT / TS_MAIN).is_file():
            return "echo-typescript is not built (no dist/); run `make ts-check`"
        if self.name == "echo-claude-agent" and not messages_route_present():
            return "chassis /v1/messages not present"
        return None


def messages_route_present() -> bool:
    """True when `chassis.server.model_proxy_messages` can be found (the B1 route)."""
    try:
        return importlib.util.find_spec(MESSAGES_ROUTE) is not None
    except (ImportError, ValueError):
        return False


ENGINES: tuple[Engine, ...] = (
    Engine(
        "echo-python",
        "python",
        handle="echo_python:handle",
        mapping_files=_py("echo-python", "echo_python", "handle.py", "tools.py"),
    ),
    Engine(
        "echo-pydanticai",
        "python",
        handle="echo_pydanticai:handle",
        mapping_files=_py("echo-pydanticai", "echo_pydanticai", "mapping.py", "handle.py"),
    ),
    Engine(
        "echo-langgraph",
        "python",
        handle="echo_langgraph:handle",
        mapping_files=_py(
            "echo-langgraph", "echo_langgraph", "mapping.py", "handle.py", "tools.py"
        ),
    ),
    Engine(
        "echo-openai-agents",
        "python",
        handle="echo_openai_agents:handle",
        mapping_files=_py(
            "echo-openai-agents", "echo_openai_agents", "mapping.py", "handle.py", "tools.py"
        ),
    ),
    Engine(
        "echo-typescript",
        "node",
        mapping_files=tuple(
            f"{WORKLOADS}/echo-typescript/src/{name}"
            for name in ("a2a_server.ts", "handle.ts", "schema.ts", "tools.ts")
        ),
    ),
    Engine(
        "echo-smolagents",
        "python",
        trusted=False,
        handle="echo_smolagents:handle",
        script=SMOLAGENTS_SCRIPT,
        mapping_files=_py(
            "echo-smolagents", "echo_smolagents", "mapping.py", "handle.py", "tools.py", "agent.py"
        ),
    ),
    Engine(
        "echo-claude-agent",
        "python",
        trusted=False,
        handle="echo_claude_agent:handle",
        script=CLAUDE_SCRIPT,
        mapping_files=_py("echo-claude-agent", "echo_claude_agent", "mapping.py", "handle.py"),
    ),
    Engine(
        "kagent-adk",
        "kind",
        trusted=False,
        always_skip="runs only on kind (poc06-kind.yml)",
    ),
)


def engine_names() -> list[str]:
    return [engine.name for engine in ENGINES]


def get_engine(name: str) -> Engine:
    for engine in ENGINES:
        if engine.name == name:
            return engine
    raise KeyError(f"unknown engine {name!r}; known: {', '.join(engine_names())}")


def lanes_for(engine: Engine) -> tuple[Lane, ...]:
    """The lanes the engine may run in. Untrusted: `remote` only. Node engines have no
    `inprocess` lane (the chassis loads Python handles only). `kind` engines: `remote`."""
    if not engine.trusted or engine.runner == "kind":
        return ("remote",)
    if engine.runner == "node":
        return ("sidecar", "remote")
    return LANES


def check_lane(engine: Engine, lane: str) -> None:
    """Raise `ValueError` when `engine` may not run in `lane`."""
    if lane not in LANES:
        raise ValueError(f"unknown lane {lane!r}; known: {', '.join(LANES)}")
    if not engine.trusted and lane != "remote":
        raise ValueError(f"{engine.name} is untrusted and runs only in the remote lane, not {lane}")
    if lane not in lanes_for(engine):
        raise ValueError(f"{engine.name} does not support the {lane} lane")


def check_hosted(engine: Engine) -> None:
    """Raise `ValueError` when `engine` may not run against a real model.

    An untrusted engine executes model-written code and shell commands. With a real model that
    must happen only in a sandbox (kind on gVisor), never as a host process or on Compose.
    """
    if not engine.trusted:
        raise ValueError(
            f"{engine.name} is untrusted: it never runs against a model URL on this host "
            "(real models run it only on kind under gVisor)"
        )
