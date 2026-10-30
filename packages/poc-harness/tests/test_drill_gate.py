"""The `requires_env` drill gate: a drill skips while its variable is off, and with the variable
on a missing kubectl context fails the run instead of skipping it (R0-13, R5a-04)."""

from __future__ import annotations

import stat
from pathlib import Path

import pytest
from poc_harness import drills, plugin

pytest_plugins = ("pytester",)

VARIABLE = "DRILL_GATE_TEST"
CONTEXT = "kind-here"
HINT = "make kind-here ARGS=up"
PLAIN_DRILL = f"""
import pytest

pytestmark = pytest.mark.requires_env("{VARIABLE}")

def test_drill():
    pass
"""
CONTEXT_DRILL = f"""
import pytest

pytestmark = pytest.mark.requires_env("{VARIABLE}", context="{CONTEXT}", hint="{HINT}")

def test_one():
    pass

def test_two():
    pass
"""
KUBECTL = """#!/bin/sh
echo "$@" >> "{log}"
{body}
"""


def fake_kubectl(folder: Path, monkeypatch: pytest.MonkeyPatch, body: str) -> Path:
    """A `kubectl` on `PATH` that logs each call to the returned file, then runs `body`."""
    folder.mkdir()
    log = folder / "calls.log"
    script = folder / "kubectl"
    script.write_text(KUBECTL.format(log=log, body=body), encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", f"{folder}:/usr/bin:/bin")
    return log


def calls(log: Path) -> list[str]:
    return log.read_text(encoding="utf-8").splitlines() if log.exists() else []


def test_a_drill_skips_with_a_reason_naming_the_variable_when_the_variable_is_unset(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(VARIABLE, raising=False)
    pytester.makepyfile(PLAIN_DRILL)

    result = pytester.runpytest_inprocess("-rs")

    result.assert_outcomes(skipped=1)
    result.stdout.fnmatch_lines([f"*{VARIABLE}=1*"])


def test_a_variable_that_is_not_exactly_1_still_skips(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(VARIABLE, "true")
    pytester.makepyfile(PLAIN_DRILL)

    pytester.runpytest_inprocess().assert_outcomes(skipped=1)


def test_a_skip_reason_names_the_context_too(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(VARIABLE, raising=False)
    pytester.makepyfile(CONTEXT_DRILL)

    result = pytester.runpytest_inprocess("-rs")

    result.stdout.fnmatch_lines([f"*{VARIABLE}=1*{CONTEXT}*"])


def test_a_gated_drill_is_network_before_the_marker_filter_runs(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`-m network` keeps it, as a skip; `-m "not network"` deselects it. So the hook must run
    before pytest applies `-m`."""
    monkeypatch.delenv(VARIABLE, raising=False)
    pytester.makepyfile(PLAIN_DRILL)

    kept = pytester.runpytest_inprocess("-m", "network")
    dropped = pytester.runpytest_inprocess("-m", "not network")

    kept.assert_outcomes(skipped=1)
    dropped.assert_outcomes(deselected=1)


def test_a_drill_runs_when_the_variable_is_1_and_kubectl_is_never_asked_without_a_context(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    log = fake_kubectl(tmp_path / "bin", monkeypatch, "exit 1")
    monkeypatch.setenv(VARIABLE, "1")
    pytester.makepyfile(PLAIN_DRILL)

    result = pytester.runpytest_inprocess()

    result.assert_outcomes(passed=1)
    assert calls(log) == []


def test_kubectl_is_never_asked_while_the_variable_is_off(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    log = fake_kubectl(tmp_path / "bin", monkeypatch, "exit 1")
    monkeypatch.delenv(VARIABLE, raising=False)
    pytester.makepyfile(CONTEXT_DRILL)

    pytester.runpytest_inprocess().assert_outcomes(skipped=2)

    assert calls(log) == []


def test_a_context_kubectl_knows_lets_the_drills_run_and_is_asked_for_once(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    log = fake_kubectl(tmp_path / "bin", monkeypatch, f"echo {CONTEXT}")
    monkeypatch.setenv(VARIABLE, "1")
    pytester.makepyfile(CONTEXT_DRILL)

    result = pytester.runpytest_inprocess()

    result.assert_outcomes(passed=2)
    assert len(calls(log)) == 1, calls(log)


@pytest.mark.parametrize(
    "body",
    [pytest.param("exit 1", id="unknown"), pytest.param("echo kind-other", id="other-context")],
)
def test_a_context_kubectl_lacks_fails_the_run_naming_the_context_and_the_hint(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, body: str
) -> None:
    """R5a-04: an all-skip kind run exits 0, a vacuous pass. Reported in the setup phase, so the
    drill's fixtures never start; pytest counts a setup failure as `errors`."""
    fake_kubectl(tmp_path / "bin", monkeypatch, body)
    monkeypatch.setenv(VARIABLE, "1")
    pytester.makepyfile(CONTEXT_DRILL)

    result = pytester.runpytest_inprocess()

    result.assert_outcomes(errors=2)
    assert result.ret == pytest.ExitCode.TESTS_FAILED
    result.stdout.fnmatch_lines([f"*{CONTEXT}*{HINT}*"])


def test_a_missing_kubectl_fails_the_run(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setenv(VARIABLE, "1")
    pytester.makepyfile(CONTEXT_DRILL)

    pytester.runpytest_inprocess().assert_outcomes(errors=2)


def test_a_kubectl_that_hangs_times_out_and_fails_the_run(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake_kubectl(tmp_path / "bin", monkeypatch, "exec sleep 5")
    monkeypatch.setattr(plugin, "KUBECTL_TIMEOUT_S", 0.1)
    monkeypatch.setenv(VARIABLE, "1")
    pytester.makepyfile(CONTEXT_DRILL)

    pytester.runpytest_inprocess().assert_outcomes(errors=2)


def test_a_context_failure_stops_the_drill_before_its_fixtures_start(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake_kubectl(tmp_path / "bin", monkeypatch, "exit 1")
    monkeypatch.setenv(VARIABLE, "1")
    pytester.makepyfile(
        f"""
        import pytest

        pytestmark = pytest.mark.requires_env("{VARIABLE}", context="{CONTEXT}")

        @pytest.fixture
        def cluster():
            pytest.skip("a fixture must not turn the failure back into a skip")

        def test_drill(cluster):
            pass
        """
    )

    pytester.runpytest_inprocess().assert_outcomes(errors=1)


def test_the_named_marks_carry_their_variable_and_context() -> None:
    gates = {
        name: (mark.name, mark.args, mark.kwargs.get("context"))
        for name, mark in vars(drills).items()
        if name.isupper() and isinstance(mark, pytest.MarkDecorator)
    }

    assert gates == {
        "POC04_STACK": ("requires_env", ("POC04_STACK",), None),
        "POC04_KIND": ("requires_env", ("POC04_KIND",), None),
        "POC05_KIND": ("requires_env", ("POC05_KIND",), "kind-poc05"),
        "POC06_KIND": ("requires_env", ("POC06_KIND",), "kind-poc05"),
    }
