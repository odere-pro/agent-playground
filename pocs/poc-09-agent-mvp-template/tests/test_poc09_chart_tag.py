"""Backlog 024 CH-3: the chassis tag is set once, in the library chart (ADR-001 item 9).

Two services include `deploy/helm/agent-lib`: `example-agent` and `other-agent` (the same chart
under a second release, with its own workload image). The library's tag is bumped in a copy of
the charts (`agent-lib.defaults` in `_values.tpl`), and both services are rendered again:

- A bump in the library alone changes the chassis image in every service's pod template, and
  nothing else in the pod: no workload image changes, so no service image is rebuilt. A changed
  pod template is what makes a Deployment roll out; the rollout itself on a cluster is not run here.
- A service holds the previous tag with one value, `chassis.image.tag`, while the other service
  takes the new one (056 CH-7, render half). The chart also sets `CHASSIS_IMAGE_TAG` from that
  value, so the comparisons strip it with the image.

Skipped, with the reason, when `helm` is not on PATH.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest
import yaml
from chart_render import NAMESPACE, NEEDS_HELM, Charts, Doc, make_charts, pod_of, values_file

pytestmark = NEEDS_HELM

REPOSITORY = "kind.local/agent-platform/chassis"
CURRENT_TAG = "poc09"
NEW_TAG = "poc10"
TAG_LINE = f"    tag: {CURRENT_TAG}\n"
SUPPORTED_LINE = f"    supportedTags: [{CURRENT_TAG}, poc08]\n"
TAG_ENV = "CHASSIS_IMAGE_TAG"
SERVICES = {
    "example-agent": "kind.local/agent-platform/echo-python:poc09",
    "other-agent": "kind.local/agent-platform/echo-other:poc09",
}


def _bump_library(charts: Charts) -> None:
    """A chassis release: the tag and its supported list change in the library chart only."""
    path = charts.example.parent / "agent-lib/templates/_values.tpl"
    text = path.read_text(encoding="utf-8")
    assert text.count(TAG_LINE) == 1 and text.count(SUPPORTED_LINE) == 1, "the tag moved"
    text = text.replace(TAG_LINE, f"    tag: {NEW_TAG}\n")
    text = text.replace(SUPPORTED_LINE, f"    supportedTags: [{NEW_TAG}, {CURRENT_TAG}]\n")
    path.write_text(text, encoding="utf-8")
    for chart in (charts.example, charts.admission):
        charts.helm("dependency", "update", "--skip-refresh", str(chart))


@pytest.fixture(scope="module")
def released(tmp_path_factory: pytest.TempPathFactory) -> tuple[Charts, Charts]:
    before = make_charts(tmp_path_factory.mktemp("helm-before"))
    after = make_charts(tmp_path_factory.mktemp("helm-after"))
    _bump_library(after)
    return before, after


def _render(charts: Charts, release: str, tmp_path: Path, extra: dict[str, Any]) -> Doc:
    values: dict[str, Any] = {"workload": {"image": SERVICES[release]}, **extra}
    path = values_file(tmp_path / release, values)
    proc = charts.render(charts.example, release, NAMESPACE, path)
    docs = [d for d in yaml.safe_load_all(proc.stdout) if d]
    return pod_of(docs, release)


def _image(pod: Doc, name: str) -> str:
    every = [*pod.get("initContainers", []), *pod.get("containers", [])]
    (container,) = [c for c in every if c["name"] == name]
    image: str = container["image"]
    return image


def _chassis(pod: Doc) -> Doc:
    every = [*pod.get("initContainers", []), *pod.get("containers", [])]
    (container,) = [c for c in every if c["name"] == "chassis"]
    return container


def _tag_env(pod: Doc) -> str:
    """The tag the chassis reports in `/manifest` (056 CH-7), set from the same value."""
    (value,) = [e["value"] for e in _chassis(pod).get("env", []) if e["name"] == TAG_ENV]
    return str(value)


def _without_chassis_image(pod: Doc) -> Doc:
    """The pod with the chassis tag removed: the image and the env var set from that one value."""
    stripped = copy.deepcopy(pod)
    container = _chassis(stripped)
    container.pop("image")
    container["env"] = [e for e in container.get("env", []) if e["name"] != TAG_ENV]
    return stripped


def test_a_library_tag_bump_reaches_every_service_and_rebuilds_no_service_image(
    released: tuple[Charts, Charts], tmp_path: Path
) -> None:
    before, after = released
    for release, workload_image in SERVICES.items():
        old = _render(before, release, tmp_path / "before", {})
        new = _render(after, release, tmp_path / "after", {})
        assert _image(old, "chassis") == f"{REPOSITORY}:{CURRENT_TAG}"
        assert _image(new, "chassis") == f"{REPOSITORY}:{NEW_TAG}", release
        # The workload image is the service's own and does not move with the chassis.
        assert _image(old, "workload") == _image(new, "workload") == workload_image
        # Only the chassis image differs, so the pod template changes and the Deployment rolls.
        assert old != new
        assert _without_chassis_image(old) == _without_chassis_image(new), release


def test_a_service_pins_the_previous_chassis_tag_with_one_value(
    released: tuple[Charts, Charts], tmp_path: Path
) -> None:
    _, after = released
    pin = {"chassis": {"image": {"tag": CURRENT_TAG}}}
    pinned = _render(after, "example-agent", tmp_path / "pinned", pin)
    moved = _render(after, "other-agent", tmp_path / "moved", {})
    assert _image(pinned, "chassis") == f"{REPOSITORY}:{CURRENT_TAG}"
    assert _image(moved, "chassis") == f"{REPOSITORY}:{NEW_TAG}"
    assert (_tag_env(pinned), _tag_env(moved)) == (CURRENT_TAG, NEW_TAG), "each reports its tag"
    # The pin is the one value that differs from the unpinned render of the same service.
    unpinned = _render(after, "example-agent", tmp_path / "unpinned", {})
    assert _without_chassis_image(pinned) == _without_chassis_image(unpinned)
