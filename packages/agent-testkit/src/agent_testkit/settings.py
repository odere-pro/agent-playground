"""What the kit reads before it starts anything: the agent and where the chassis comes from.

`[tool.agent-testkit]` in the `pyproject.toml` at pytest's rootdir:

- `handle` (required): `module:attribute`, the workload's `handle`.
- `name` (optional): the agent card name and `agent.name` in the chassis config; default: the
  module part of `handle`.
- `chassis` (optional): where the chassis comes from, the same forms as `AGENT_TESTKIT_CHASSIS`.
- `platform_path` (optional): a checkout of the platform monorepo, relative to the rootdir.

The chassis source, first match wins: `$AGENT_TESTKIT_CHASSIS`, `chassis`, `platform_path`, then
the platform checkout this package sits in (found by walking up from this file). Its forms:

- a platform checkout (a directory with `uv.lock` and `packages/chassis/pyproject.toml`): the
  chassis runs in a fresh environment built from that lock, `uv run --isolated --package chassis
  --frozen --offline`. No resolution, so it works offline once the wheels are in uv's cache.
- `@current-python`: the interpreter running pytest. The monorepo shortcut only, where `chassis`
  is installed next to the workload; never use it in an agent's own repo.
- anything else (a path to the chassis package, a requirement like `chassis==0.1.0`, a git URL):
  `uv tool run --offline --from <it>`. That resolves, so it needs uv's cache warm: run the same
  command once without `--offline` (the README says how).
"""

from __future__ import annotations

import os
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

CHASSIS_ENV = "AGENT_TESTKIT_CHASSIS"
"""The environment variable that names the chassis source."""
CURRENT_PYTHON = "@current-python"
"""The `AGENT_TESTKIT_CHASSIS` value for the monorepo shortcut: the chassis in this interpreter."""
TOOL_KEY = "agent-testkit"
"""The `[tool.<key>]` table in `pyproject.toml`."""


class SettingsError(RuntimeError):
    """The kit's settings are missing or wrong; the message says what to set."""


@dataclass(frozen=True)
class AgentRef:
    """The agent under test: its `handle` path and its name."""

    handle: str
    name: str

    @classmethod
    def of(cls, value: AgentRef | str) -> AgentRef:
        """An `AgentRef`, or a bare `module:attribute` whose name is the module part."""
        if isinstance(value, AgentRef):
            return value
        return cls(handle=value, name=_default_name(value))


def _default_name(handle: str) -> str:
    module, sep, attr = handle.partition(":")
    if not sep or not module or not attr:
        raise SettingsError(f"handle must be 'module:attribute', got {handle!r}")
    return module.split(".")[0].replace("_", "-")


@dataclass(frozen=True)
class ChassisSource:
    """How to start a Python that can import the chassis: `command` then the script and its args."""

    command: tuple[str, ...]
    description: str
    isolated: bool
    """False only for `@current-python`: the chassis shares the workload's interpreter."""


def read_table(rootdir: Path) -> dict[str, Any]:
    """`[tool.agent-testkit]` from `rootdir/pyproject.toml`, or `{}` when there is none."""
    path = rootdir / "pyproject.toml"
    if not path.is_file():
        return {}
    with path.open("rb") as file:
        data = tomllib.load(file)
    table = data.get("tool", {}).get(TOOL_KEY, {})
    if not isinstance(table, dict):
        raise SettingsError(f"[tool.{TOOL_KEY}] in {path} must be a table")
    return table


def agent_ref(rootdir: Path) -> AgentRef:
    """The agent named in `[tool.agent-testkit]`; a `SettingsError` that says what to add if not."""
    table = read_table(rootdir)
    handle = table.get("handle")
    if not isinstance(handle, str) or not handle:
        raise SettingsError(
            f'set [tool.{TOOL_KEY}] handle = "my_agent:handle" in {rootdir / "pyproject.toml"}, '
            "or override the agent_handle_ref fixture"
        )
    name = table.get("name")
    if name is not None and (not isinstance(name, str) or not name):
        raise SettingsError(f"[tool.{TOOL_KEY}] name must be a non-empty string")
    return AgentRef(handle=handle, name=name or _default_name(handle))


def is_platform_checkout(path: Path) -> bool:
    return (path / "uv.lock").is_file() and (path / "packages/chassis/pyproject.toml").is_file()


def _find_checkout(start: Path) -> Path | None:
    for candidate in (start, *start.parents):
        if is_platform_checkout(candidate):
            return candidate
    return None


def _uv() -> str:
    """The uv binary: `$UV` (set by `uv run`), else `uv` on the PATH."""
    return os.environ.get("UV") or "uv"


def source_for(value: str, base: Path) -> ChassisSource:
    """The `ChassisSource` for one `AGENT_TESTKIT_CHASSIS` value; a relative path is from `base`."""
    if value == CURRENT_PYTHON:
        return ChassisSource(
            (sys.executable,), "the current interpreter (monorepo shortcut)", False
        )
    path = (base / value).resolve() if not Path(value).is_absolute() else Path(value)
    if path.is_dir() and is_platform_checkout(path):
        command: tuple[str, ...] = (
            _uv(),
            "run",
            "--project",
            str(path),
            "--package",
            "chassis",
            "--isolated",
            "--no-dev",
            "--frozen",
            "--offline",
            "python",
        )
        return ChassisSource(command, f"an isolated env from {path / 'uv.lock'}", True)
    spec = str(path) if path.exists() else value
    command = (_uv(), "tool", "run", "--offline", "--from", spec, "python")
    return ChassisSource(command, f"a uv tool env from {spec}", True)


def chassis_source(rootdir: Path) -> ChassisSource:
    """Where the chassis comes from, by the order in the module docstring."""
    env = os.environ.get(CHASSIS_ENV)
    if env:
        return source_for(env, rootdir)
    table = read_table(rootdir)
    for key in ("chassis", "platform_path"):
        value = table.get(key)
        if isinstance(value, str) and value:
            return source_for(value, rootdir)
    found = _find_checkout(Path(__file__).resolve().parent)
    if found is not None:
        return source_for(str(found), rootdir)
    raise SettingsError(
        f"cannot find the chassis: set {CHASSIS_ENV} to a platform checkout, a chassis "
        f"package path, or a uv --from spec, or set [tool.{TOOL_KEY}] chassis or platform_path"
    )
