"""R5b-12: `deploy/kind/lib.sh` holds the one `kctl`, `log`, and `die`, and each kind script keeps
its own context (offline part of exit criterion 1).

Given each script, when `lib.sh` is sourced, then `kctl` pins its context and `die` exits 1 with
the script's prefix. The tests run the real top of each script, up to its `source` line, in real
bash with a fake `kubectl` first on PATH. No cluster, no socket, no key.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
KIND = ROOT / "deploy/kind"
LIB = KIND / "lib.sh"
BASH = shutil.which("bash") or "bash"
SOURCE_LINE = re.compile(r'^source "\$HERE/(?:\.\./)*lib\.sh"$', re.M)
OWN_NAMES = re.compile(r"^[ \t]*(?:CLUSTER|CONTEXT|LOG_PREFIX)=.*$", re.M)
LITERAL_NAME = re.compile(r"^(?:CONTEXT=kind-(?:poc0\d|\$CLUSTER)|LOG_PREFIX=[a-z0-9-]+)$")
DIRECT_KUBECTL = re.compile(r"(?<![\w-])kubectl(\s|$)")
KCTL = 'kctl() { kubectl --context "$CONTEXT" "$@"; }'
FAKE_KUBECTL = '#!/bin/sh\nprintf \'%s\\n\' "$@" >"$KUBECTL_ARGV"\n'

SCRIPTS = (
    "run.sh",
    "poc05/run.sh",
    "poc05/install-gvisor.sh",
    "poc05/platform/seed.sh",
    "poc06/seed.sh",
    "poc06/hosted.sh",
    "poc06/run.sh",
)
PINNED = [
    ("run.sh", "kind-poc04", "kind-poc04"),
    ("poc05/run.sh", "kind-poc05", "kind-poc05"),
    ("poc05/install-gvisor.sh", "kind-poc05", "install-gvisor"),
    ("poc05/platform/seed.sh", "kind-poc05", "seed"),
    ("poc06/seed.sh", "kind-poc05", "seed-poc06"),
    ("poc06/hosted.sh", "kind-poc05", "hosted-poc06"),
    ("poc06/run.sh", "kind-poc05", "kind-poc06"),
]
"""What each script printed and pinned before the move, byte for byte."""


def _own_values(script: str) -> tuple[str, str, str]:
    """The script, and the context and prefix its own assignment lines give in an empty env."""
    lines = "\n".join(OWN_NAMES.findall((KIND / script).read_text()))
    show = 'printf "%s\\n%s\\n" "${CONTEXT-}" "${LOG_PREFIX-}"'
    out = subprocess.run(
        [BASH, "-c", f"{lines}\n{show}"], env={}, capture_output=True, text=True, check=True
    )
    context, prefix = out.stdout.splitlines()
    return script, context, prefix


CASES = [_own_values(script) for script in SCRIPTS]


def _top(script: str) -> str:
    """The script's real text, from the shebang through its `source` line."""
    text = (KIND / script).read_text()
    source = SOURCE_LINE.search(text)
    assert source, f"{script} does not source lib.sh"
    return text[: source.end()]


def _code(path: Path) -> list[str]:
    return [ln for ln in path.read_text().splitlines() if not ln.lstrip().startswith("#")]


def _bash(
    code: str,
    tmp_path: Path,
    *,
    script: str = "lib-user",
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run `code` with `$0` set to the script's path, from a directory that is not the repo's."""
    stubs = tmp_path / "bin"
    stubs.mkdir(exist_ok=True)
    fake = stubs / "kubectl"
    fake.write_text(FAKE_KUBECTL)
    fake.chmod(0o755)
    return subprocess.run(
        [BASH, "-c", code, str(KIND / script)],
        cwd=tmp_path,
        env={
            "PATH": f"{stubs}:/usr/bin:/bin",
            "KUBECTL_ARGV": str(tmp_path / "argv"),
            **(env or {}),
        },
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def _argv(tmp_path: Path) -> list[str]:
    return (tmp_path / "argv").read_text().splitlines()


def test_scripts_keep_their_context_and_prefix_when_read_from_their_lines() -> None:
    """R5b-12: each script's own lines give the context and the log prefix it had before."""
    assert CASES == PINNED


def test_script_list_names_every_script_under_deploy_kind() -> None:
    """R5b-12: every `.sh` under deploy/kind but lib.sh itself is covered here."""
    found = {str(p.relative_to(KIND)) for p in KIND.rglob("*.sh")}

    assert found == {*SCRIPTS, "lib.sh"}


@pytest.mark.parametrize(("script", "context", "prefix"), CASES)
def test_kctl_pins_the_script_context_when_lib_is_sourced(
    tmp_path: Path, script: str, context: str, prefix: str
) -> None:
    """R5b-12: `kctl get pods` passes `--context <the script's context>` first."""
    code = f"{_top(script)}\nkctl get pods\n"

    done = _bash(code, tmp_path, script=script)

    assert done.returncode == 0, done.stderr
    assert _argv(tmp_path) == ["--context", context, "get", "pods"]


@pytest.mark.parametrize(("script", "context", "prefix"), CASES)
def test_die_exits_1_with_the_script_prefix_when_lib_is_sourced(
    tmp_path: Path, script: str, context: str, prefix: str
) -> None:
    """R5b-12: `die` prints `[<prefix>] msg` to stderr, nothing to stdout, and exits 1."""
    code = f"{_top(script)}\ndie 'no such thing'\necho not-reached\n"

    done = _bash(code, tmp_path, script=script)

    assert done.returncode == 1
    assert done.stderr == f"[{prefix}] no such thing\n"
    assert done.stdout == ""


@pytest.mark.parametrize(("script", "context", "prefix"), CASES)
def test_log_prints_the_script_prefix_to_stderr_when_lib_is_sourced(
    tmp_path: Path, script: str, context: str, prefix: str
) -> None:
    """R5b-12: `log` joins its arguments after `[<prefix>] ` on stderr and does not stop."""
    code = f"{_top(script)}\nlog one '100%s' three\necho reached\n"

    done = _bash(code, tmp_path, script=script)

    assert done.stderr == f"[{prefix}] one 100%s three\n"
    assert done.stdout == "reached\n"


@pytest.mark.parametrize(("script", "context", "prefix"), CASES)
def test_kctl_keeps_the_script_context_when_the_caller_exports_another(
    tmp_path: Path, script: str, context: str, prefix: str
) -> None:
    """R5b-12: `CONTEXT=evil` in the caller's environment does not reach `kctl`."""
    code = f"{_top(script)}\nkctl get pods\n"
    evil = {"CONTEXT": "evil", "CLUSTER": "evil", "HERE": "/nonexistent"}

    done = _bash(code, tmp_path, script=script, env=evil)

    assert done.returncode == 0, done.stderr
    assert _argv(tmp_path)[:2] == ["--context", context]


@pytest.mark.parametrize(("script", "context", "prefix"), CASES)
def test_log_keeps_the_script_prefix_when_the_caller_exports_another(
    tmp_path: Path, script: str, context: str, prefix: str
) -> None:
    """R5b-12: `LOG_PREFIX=evil` in the caller's environment does not reach `log`."""
    code = f"{_top(script)}\nlog hello\n"

    done = _bash(code, tmp_path, script=script, env={"LOG_PREFIX": "evil"})

    assert done.stderr == f"[{prefix}] hello\n"


@pytest.mark.parametrize(
    "setup",
    ["LOG_PREFIX=p", "CONTEXT=c", "CONTEXT=; LOG_PREFIX=p", "CONTEXT=c; LOG_PREFIX="],
    ids=["context-unset", "prefix-unset", "context-empty", "prefix-empty"],
)
def test_lib_refuses_to_load_when_a_name_is_missing(tmp_path: Path, setup: str) -> None:
    """R5b-12: a sourcing mistake stops the script at the `source` line."""
    code = f"set -euo pipefail\n{setup}\nsource {LIB}\necho loaded\n"

    done = _bash(code, tmp_path)

    assert done.returncode != 0
    assert "loaded" not in done.stdout
    assert "before sourcing" in done.stderr


def test_lib_loads_when_context_and_prefix_are_set(tmp_path: Path) -> None:
    """R5b-12 control: with both names set, the same source goes through."""
    code = f"set -euo pipefail\nCONTEXT=c\nLOG_PREFIX=p\nsource {LIB}\necho loaded\n"

    done = _bash(code, tmp_path)

    assert done.returncode == 0, done.stderr
    assert done.stdout == "loaded\n"


@pytest.mark.parametrize("script", SCRIPTS)
def test_script_assigns_its_names_above_the_source_line(script: str) -> None:
    """R5b-12: strict mode, then `CONTEXT`, `LOG_PREFIX`, and `HERE`, then the `source`."""
    lines = _top(script).splitlines()
    names = ("set -euo pipefail", "CONTEXT=", "LOG_PREFIX=", "HERE=")

    first = [next(i for i, ln in enumerate(lines) if ln.startswith(n)) for n in names]

    assert first[0] < first[1] and max(first) < len(lines) - 1


@pytest.mark.parametrize("script", SCRIPTS)
def test_script_takes_no_default_for_its_names_from_the_environment(script: str) -> None:
    """R5b-12: each script assigns its names unconditionally, so the caller's value is dropped."""
    text = (KIND / script).read_text()

    defaults = re.findall(r"\$\{(?:CONTEXT|CLUSTER|LOG_PREFIX|HERE)[:=?+-]", text)

    assert defaults == []


@pytest.mark.parametrize("script", SCRIPTS)
def test_script_builds_its_context_and_prefix_from_literals(script: str) -> None:
    """R5b-12: no other variable can feed `CONTEXT` or `LOG_PREFIX`, indented or not."""
    lines = [ln for ln in OWN_NAMES.findall(_top(script)) if "CLUSTER=" not in ln]

    assert [ln for ln in lines if not LITERAL_NAME.match(ln)] == []
    assert len(lines) == 2


@pytest.mark.parametrize("script", SCRIPTS)
def test_script_calls_kubectl_only_through_kctl(script: str) -> None:
    """R5b-12: no script has a kubectl call of its own, and none defines `kctl`, `log`, or `die`."""
    code = _code(KIND / script)

    direct = [ln for ln in code if DIRECT_KUBECTL.search(ln) and "for tool in " not in ln]
    helpers = [ln for ln in code if re.match(r"\s*(kctl|log|die)\(\)", ln)]

    assert direct == []
    assert helpers == []


def test_lib_has_the_one_kubectl_call() -> None:
    """R5b-12: the only kubectl call in lib.sh is the `kctl` line with the pinned context."""
    calls = [ln for ln in _code(LIB) if DIRECT_KUBECTL.search(ln)]

    assert calls == [KCTL]


def test_lib_reads_no_default_and_never_traces() -> None:
    """R5b-12: lib.sh sets no name, exports nothing, and has no `set` at all."""
    body = "\n".join(_code(LIB))

    assert not re.search(r"^\s*(CONTEXT|CLUSTER|LOG_PREFIX)=", body, re.M)
    assert not re.search(r"\$\{\w+:?[-=+]", body)
    assert not re.search(r"^\s*(set|export)\s", body, re.M)
    assert "xtrace" not in body and "BASH_XTRACEFD" not in body
