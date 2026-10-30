"""Render the service template with Copier, then `git init -b main` and a first commit."""

from __future__ import annotations

import os
import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import copier
from plumbum import local  # copier's own process runner

from agentctl import rules

TEMPLATE = Path(__file__).resolve().parent / "template"
"""The Copier template; `copier.yml` names the variables."""
PLATFORM_MARKER = Path("packages/workload-a2a/pyproject.toml")
"""A folder is the platform checkout when it holds this file."""
FALLBACK_IDENTITY = ("agentctl", "agentctl@localhost.invalid")
"""Author of the first commit when git has no user configured."""
GIT_LOCATION = (
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_INDEX_FILE",
    "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_COMMON_DIR",
    "GIT_PREFIX",
    "GIT_NAMESPACE",
)
"""Set by git inside a hook. Left in place, they point Copier and the first commit at the
caller's repository, not the new agent's."""


@dataclass(frozen=True)
class Answers:
    agent_name: str
    kind: str
    framework: str
    connector: str
    platform_path: Path

    def check(self) -> None:
        rules.check_name(self.agent_name)
        rules.check_choice(self.kind, rules.KINDS, "kind")
        rules.check_lane(self.framework, self.connector)
        check_platform(self.platform_path)

    def data(self) -> dict[str, str]:
        return {
            "agent_name": self.agent_name,
            "kind": self.kind,
            "framework": self.framework,
            "connector": self.connector,
            "platform_path": str(self.platform_path),
        }


def check_platform(path: Path) -> None:
    if not path.is_absolute():
        raise rules.UsageError(f"--platform-path {str(path)!r} must be an absolute path")
    if not (path / PLATFORM_MARKER).is_file():
        raise rules.UsageError(
            f"--platform-path {str(path)!r} is not an agent-platform checkout: "
            f"no packages/workload-a2a there"
        )


def find_platform(*starts: Path) -> Path | None:
    """The first checkout found walking up from each start: this file, then the working dir."""
    for start in starts:
        for folder in [start, *start.parents]:
            if (folder / PLATFORM_MARKER).is_file():
                return folder
    return None


def render(answers: Answers, dest: Path) -> Path:
    """Render into `dest/<agent_name>` and return that path. The folder must be absent or empty."""
    answers.check()
    target = dest / answers.agent_name
    if target.exists() and any(target.iterdir()):
        raise rules.UsageError(f"{target} exists and is not empty")
    with _outside_callers_repo():
        _copy(answers, target)
    return target


def _copy(answers: Answers, target: Path) -> None:
    copier.run_copy(
        str(TEMPLATE),
        str(target),
        data=answers.data(),
        defaults=True,
        quiet=True,
        unsafe=False,
        overwrite=False,
    )


@contextmanager
def _outside_callers_repo() -> Iterator[None]:
    """Copier runs in this process and calls git through plumbum, whose environment is a copy of
    `os.environ` taken at import. Both drop git's variables here and get them back after."""
    saved = {name: os.environ.pop(name) for name in GIT_LOCATION if name in os.environ}
    try:
        with local.env():
            for name in GIT_LOCATION:
                local.env.pop(name, "")
            yield
    finally:
        os.environ.update(saved)


def _env() -> dict[str, str]:
    return {k: v for k, v in os.environ.items() if k not in GIT_LOCATION}


def first_commit(path: Path, agent_name: str) -> None:
    """`git init -b main` and one commit. No remote is added."""
    _git(path, "init", "-q", "-b", "main")
    _git(path, "add", "-A")
    identity: list[str] = []
    if not _git_value(path, "user.email") or not _git_value(path, "user.name"):
        name, email = FALLBACK_IDENTITY
        identity = ["-c", f"user.name={name}", "-c", f"user.email={email}"]
    _git(path, *identity, "commit", "-q", "-m", f"chore: scaffold {agent_name} with agentctl")


def pin_to_platform(path: Path, platform: Path) -> None:
    """Pin the agent to the versions the platform tested, then write `uv.lock`.

    A fresh resolve picks the newest release of every transitive package, which the platform
    never ran and the offline cache does not hold. So the platform's `uv.lock` is exported as
    `[tool.uv] constraint-dependencies` in the agent's `pyproject.toml`, and `uv lock` runs
    before the first commit. `uv lock` inherits the environment: `UV_OFFLINE=1` keeps it offline.
    """
    pins = _uv(
        platform,
        "export",
        *("--project", str(platform)),
        *("--frozen", "--all-packages", "--all-groups", "--no-hashes"),
        *("--no-emit-workspace", "--no-header", "--no-annotate"),
    )
    lines = [line for line in pins.splitlines() if line and not line.startswith(("#", "-"))]
    body = "\n".join(f"  {_toml_string(line)}," for line in lines)
    with (path / "pyproject.toml").open("a") as pyproject:
        pyproject.write(
            "\n# The versions the platform tested, exported from its uv.lock by `agentctl new`.\n"
            "# Do not edit by hand; a newer platform comes with a new export.\n"
            f"[tool.uv]\nconstraint-dependencies = [\n{body}\n]\n"
        )
    _uv(path, "lock")


def new_agent(answers: Answers, dest: Path, *, lock: bool = True) -> Path:
    target = render(answers, dest)
    if lock:
        pin_to_platform(target, answers.platform_path)
    first_commit(target, answers.agent_name)
    return target


def _toml_string(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _uv(cwd: Path, *args: str) -> str:
    done = subprocess.run(["uv", *args], cwd=cwd, env=_env(), capture_output=True, text=True)
    if done.returncode != 0:
        raise RuntimeError(f"uv {' '.join(args)} failed: {done.stderr.strip()}")
    return done.stdout


def _git_value(path: Path, key: str) -> str:
    done = subprocess.run(
        ["git", "-C", str(path), "config", "--get", key],
        env=_env(),
        capture_output=True,
        text=True,
    )
    return done.stdout.strip()


def _git(path: Path, *args: str) -> None:
    done = subprocess.run(
        ["git", "-C", str(path), *args], env=_env(), capture_output=True, text=True
    )
    if done.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {done.stderr.strip()}")
