"""The `kind` and `stack` drill markers: each gates on one variable named for what it turns on,
not for a PoC (R0-14). The PoC-numbered names still work for one release, with a warning."""

from __future__ import annotations

import pytest
from poc_harness import drills, plugin

pytest_plugins = ("pytester",)

KIND_DRILL = """
import pytest

pytestmark = pytest.mark.kind

def test_drill():
    pass
"""
STACK_DRILL = """
import pytest

pytestmark = pytest.mark.stack

def test_drill():
    pass
"""
OLD_NAMES = {
    "POC04_KIND": "KIND_DRILLS",
    "POC05_KIND": "KIND_DRILLS",
    "POC06_KIND": "KIND_DRILLS",
    "POC04_STACK": "COMPOSE_STACK",
    "POC07_STACK": "COMPOSE_STACK",
    "POC08_STACK": "COMPOSE_STACK",
    "POC08_LITELLM": "COMPOSE_STACK",
}


@pytest.fixture(autouse=True)
def _no_gate_set(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (*OLD_NAMES, plugin.KIND_VARIABLE, plugin.STACK_VARIABLE):
        monkeypatch.delenv(name, raising=False)


@pytest.mark.parametrize(
    ("source", "variable"),
    [
        pytest.param(KIND_DRILL, "KIND_DRILLS", id="kind"),
        pytest.param(STACK_DRILL, "COMPOSE_STACK", id="stack"),
    ],
)
def test_a_drill_marker_skips_without_its_variable_and_runs_with_it(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch, source: str, variable: str
) -> None:
    """Given `kind` tests in any PoC, when `KIND_DRILLS=1` is set, then they run, and without it
    they skip. The same for `stack` and `COMPOSE_STACK`."""
    pytester.makepyfile(source)

    off = pytester.runpytest_inprocess("-rs")
    monkeypatch.setenv(variable, "1")
    on = pytester.runpytest_inprocess()

    off.assert_outcomes(skipped=1)
    off.stdout.fnmatch_lines([f"*{variable}=1*"])
    on.assert_outcomes(passed=1)


@pytest.mark.parametrize("source", [KIND_DRILL, STACK_DRILL], ids=["kind", "stack"])
def test_a_drill_marker_is_network(pytester: pytest.Pytester, source: str) -> None:
    pytester.makepyfile(source)

    pytester.runpytest_inprocess("-m", "not network").assert_outcomes(deselected=1)


def test_the_kind_marker_takes_the_context_and_the_hint(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PATH", "")
    monkeypatch.setenv(plugin.KIND_VARIABLE, "1")
    pytester.makepyfile(
        """
        import pytest

        pytestmark = pytest.mark.kind(context="kind-nowhere", hint="make kind-nowhere ARGS=up")

        def test_drill():
            pass
        """
    )

    result = pytester.runpytest_inprocess()

    result.assert_outcomes(errors=1)
    result.stdout.fnmatch_lines(["*KIND_DRILLS=1*kind-nowhere*make kind-nowhere ARGS=up*"])


@pytest.mark.parametrize(("old", "new"), sorted(OLD_NAMES.items()))
def test_an_old_name_still_turns_its_gate_on_with_a_deprecation_warning(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch, old: str, new: str
) -> None:
    monkeypatch.setenv(old, "1")
    pytester.makepyfile(KIND_DRILL if new == plugin.KIND_VARIABLE else STACK_DRILL)

    result = pytester.runpytest_inprocess()

    result.assert_outcomes(passed=1)
    result.stdout.fnmatch_lines([f"*DeprecationWarning*{old}*{new}=1*"])
    assert drills.gate_is_on(new)


def test_the_deprecated_names_are_exactly_the_old_gates() -> None:
    assert plugin.DEPRECATED_NAMES == OLD_NAMES


def test_gate_is_on_reads_only_1() -> None:
    assert not drills.gate_is_on(plugin.STACK_VARIABLE)


def test_the_drill_markers_are_registered_outside_the_root_config(
    pytester: pytest.Pytester,
) -> None:
    pytester.makepyfile(KIND_DRILL + "\n" + STACK_DRILL.replace("test_drill", "test_stack"))

    result = pytester.runpytest_subprocess("--strict-markers", "--collect-only")

    assert result.ret == pytest.ExitCode.OK, result.stdout.str()
