"""The drill gate: one declarative table per PoC conftest marks and skips the drill tests."""

from __future__ import annotations

from pathlib import Path

import pytest
from poc_harness import drills
from poc_harness.drills import Drill, drill_gate

HERE = Path("/repo/pocs/poc-x/tests")
STACK = Drill(
    marker="stack", variable="POC04_STACK", files="test_compose_scale.py", adds_marker=True
)
KIND = Drill(
    marker="kind",
    variable="POC05_KIND",
    context="kind-poc05",
    files="test_poc05_kind_*",
    label="a kind test",
    hint="make kind-poc05 ARGS=up",
)


class FakeItem:
    """The part of `pytest.Item` the gate reads and writes."""

    def __init__(self, path: Path, markers: tuple[str, ...] = ()) -> None:
        self.path = path
        self.markers: list[pytest.Mark] = [getattr(pytest.mark, name).mark for name in markers]

    def get_closest_marker(self, name: str) -> pytest.Mark | None:
        return next((mark for mark in self.markers if mark.name == name), None)

    def add_marker(self, marker: pytest.MarkDecorator) -> None:
        self.markers.append(marker.mark)

    def names(self) -> list[str]:
        return [mark.name for mark in self.markers]

    def skip_reason(self) -> str | None:
        skip = self.get_closest_marker("skip")
        return None if skip is None else str(skip.kwargs["reason"])


def run_gate(*items: FakeItem, drill: Drill) -> None:
    hook = drill_gate(HERE, drill)
    hook(list(items))  # type: ignore[arg-type]


def test_drill_gate_marks_and_skips_a_drill_file_when_its_variable_is_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A test in the drill's file gets the marker, `network`, and the skip with the set hint."""
    monkeypatch.delenv("POC04_STACK", raising=False)
    item = FakeItem(HERE / "test_compose_scale.py")

    run_gate(item, drill=STACK)

    assert item.names() == ["stack", "network", "skip"]
    assert item.skip_reason() == "a stack drill: set POC04_STACK=1"


def test_drill_gate_runs_a_drill_as_network_when_its_variable_is_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With the variable at 1 and no context to check, a drill is `network` and not skipped."""
    monkeypatch.setenv("POC04_STACK", "1")
    item = FakeItem(HERE / "test_other.py", ("stack",))

    run_gate(item, drill=STACK)

    assert item.names() == ["stack", "network"]


def test_drill_gate_leaves_a_test_alone_when_it_is_no_drill_or_in_another_folder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A plain test here, and a drill-named test in a subfolder, get no marker at all."""
    monkeypatch.delenv("POC04_STACK", raising=False)
    plain = FakeItem(HERE / "test_other.py")
    elsewhere = FakeItem(HERE / "sub" / "test_compose_scale.py")

    run_gate(plain, elsewhere, drill=STACK)

    assert plain.names() == []
    assert elsewhere.names() == []


def test_drill_gate_adds_only_network_when_a_file_prefix_names_a_drill(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without `adds_marker`, a test the file pattern names is `network` and skipped, not marked."""
    monkeypatch.delenv("POC05_KIND", raising=False)
    item = FakeItem(HERE / "test_poc05_kind_probe.py")

    run_gate(item, drill=KIND)

    assert item.names() == ["network", "skip"]
    assert item.skip_reason() == "a kind test: set POC05_KIND=1 (needs the kind-poc05 context)"


def test_drill_gate_skips_with_the_hint_when_the_kubectl_context_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With the variable set but no kubectl context, the skip names the context and the hint."""
    monkeypatch.setenv("POC05_KIND", "1")
    monkeypatch.setattr(drills, "kubectl_context_exists", lambda context: False)
    item = FakeItem(HERE / "test_other.py", ("kind",))

    run_gate(item, drill=KIND)

    assert (
        item.skip_reason()
        == "a kind test: no kubectl context 'kind-poc05' (make kind-poc05 ARGS=up)"
    )


def test_drill_gate_runs_a_kind_drill_when_the_variable_and_context_are_there(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With the variable set and the context known, a kind drill is `network` and not skipped."""
    monkeypatch.setenv("POC05_KIND", "1")
    monkeypatch.setattr(drills, "kubectl_context_exists", lambda context: True)
    item = FakeItem(HERE / "test_poc05_kind_probe.py")

    run_gate(item, drill=KIND)

    assert item.names() == ["network"]


def test_kubectl_context_exists_returns_false_when_kubectl_is_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No `kubectl` on PATH means no context, without running anything."""
    drills.kubectl_context_exists.cache_clear()
    monkeypatch.setattr("shutil.which", lambda name: None)

    found = drills.kubectl_context_exists("kind-poc05")

    drills.kubectl_context_exists.cache_clear()
    assert found is False
