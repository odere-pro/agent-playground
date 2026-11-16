"""Backlog 024 CH-3: the chassis tag is set once, in the library chart (ADR-001 item 9).

Two services include `deploy/helm/agent-lib`: `example-agent` and `other-agent` (the same chart
under a second release, with its own workload image). The library's tag is bumped in a copy of
the charts (`agent-lib.defaults` in `_values.tpl`), and both services are rendered again:

- A bump in the library alone changes the chassis image in every service's pod template, and
  nothing else in the pod: no workload image changes, so no service image is rebuilt. A changed
  pod template is what makes a Deployment roll out; the rollout itself on a cluster is not run here.
- A service holds the previous tag, while the other service takes the new one (056 CH-7, render
  half). The chart also sets `CHASSIS_IMAGE_TAG` from that value, so the comparisons strip it
  with the image. Where the chassis is pinned by tag (kind: `values-kind.yaml`, digest ""), the
  hold is one value, `chassis.image.tag`. Since 139 CH-19 the library's default image is pinned
  by digest, and a digest pins one tag, so on that image the hold is two values:
  `chassis.image.tag` and that tag's `chassis.image.digest`.

Skipped, with the reason, when `helm` is not on PATH.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest
import yaml
from chart_render import (
    CHASSIS_REPOSITORY,
    CURRENT_TAG,
    KIND_CHASSIS_REPOSITORY,
    NAMESPACE,
    NEEDS_HELM,
    NEW_DIGEST,
    NEW_TAG,
    Charts,
    Doc,
    bump_library,
    library_digest,
    make_charts,
    pod_of,
    values_file,
)

pytestmark = NEEDS_HELM

REPOSITORY = CHASSIS_REPOSITORY
TAG_ENV = "CHASSIS_IMAGE_TAG"
SERVICES = {
    "example-agent": "kind.local/agent-platform/echo-python:poc09",
    "other-agent": "kind.local/agent-platform/echo-other:poc09",
}


@pytest.fixture(scope="module")
def released(tmp_path_factory: pytest.TempPathFactory) -> tuple[Charts, Charts]:
    before = make_charts(tmp_path_factory.mktemp("helm-before"))
    after = make_charts(tmp_path_factory.mktemp("helm-after"))
    bump_library(after)
    return before, after


def _render(
    charts: Charts, release: str, tmp_path: Path, extra: dict[str, Any], kind: bool = False
) -> Doc:
    """`release` rendered with its workload image and `extra`; on `kind`, `values-kind.yaml`
    first, as the kind drill installs it (the chassis by tag, no digest)."""
    values: dict[str, Any] = {"workload": {"image": SERVICES[release]}, **extra}
    path = values_file(tmp_path / release, values)
    args = ["template", release, str(charts.example), "--namespace", NAMESPACE]
    if kind:
        args += ["-f", str(charts.example / "values-kind.yaml")]
    proc = charts.helm(*args, "-f", str(path))
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
    chassis: Doc = container
    return chassis


def _tag_env(pod: Doc) -> str:
    """The tag the chassis reports in `/manifest` (056 CH-7), set from the same value."""
    (value,) = [e["value"] for e in _chassis(pod).get("env", []) if e["name"] == TAG_ENV]
    return str(value)


def _without_chassis_image(pod: Doc) -> Doc:
    """The pod with the chassis image removed: the image and `CHASSIS_IMAGE_TAG`, the same tag
    the chart passes to `/manifest` (056 CH-7). The env var must match the image's tag."""
    stripped = copy.deepcopy(pod)
    for container in [*stripped.get("initContainers", []), *stripped.get("containers", [])]:
        if container["name"] != "chassis":
            continue
        image = container.pop("image")
        env = container.get("env", [])
        tags = [e for e in env if e["name"] == TAG_ENV]
        assert [e["value"] for e in tags] == [image.split("@")[0].rsplit(":", 1)[1]], (image, tags)
        container["env"] = [e for e in env if e["name"] != TAG_ENV]
    return stripped


def test_a_library_tag_bump_reaches_every_service_and_rebuilds_no_service_image(
    released: tuple[Charts, Charts], tmp_path: Path
) -> None:
    before, after = released
    for release, workload_image in SERVICES.items():
        old = _render(before, release, tmp_path / "before", {})
        new = _render(after, release, tmp_path / "after", {})
        assert _image(old, "chassis") == f"{REPOSITORY}:{CURRENT_TAG}@{library_digest(before)}"
        assert _image(new, "chassis") == f"{REPOSITORY}:{NEW_TAG}@{NEW_DIGEST}", release
        # The workload image is the service's own and does not move with the chassis.
        assert _image(old, "workload") == _image(new, "workload") == workload_image
        # Only the chassis image differs, so the pod template changes and the Deployment rolls.
        assert old != new
        assert _without_chassis_image(old) == _without_chassis_image(new), release


def test_a_service_pins_the_previous_chassis_tag_with_its_digest(
    released: tuple[Charts, Charts], tmp_path: Path
) -> None:
    before, after = released
    current_digest = library_digest(before)
    pin = {"chassis": {"image": {"tag": CURRENT_TAG, "digest": current_digest}}}
    pinned = _render(after, "example-agent", tmp_path / "pinned", pin)
    moved = _render(after, "other-agent", tmp_path / "moved", {})
    assert _image(pinned, "chassis") == f"{REPOSITORY}:{CURRENT_TAG}@{current_digest}"
    assert _image(moved, "chassis") == f"{REPOSITORY}:{NEW_TAG}@{NEW_DIGEST}"
    assert (_tag_env(pinned), _tag_env(moved)) == (CURRENT_TAG, NEW_TAG), "each reports its tag"
    # The pin is the only thing that differs from the unpinned render of the same service.
    unpinned = _render(after, "example-agent", tmp_path / "unpinned", {})
    assert _without_chassis_image(pinned) == _without_chassis_image(unpinned)


def test_a_service_pins_the_previous_chassis_tag_with_one_value(
    released: tuple[Charts, Charts], tmp_path: Path
) -> None:
    """024 CH-3 box 3: where the chassis is pinned by tag (the kind drill's values, digest ""),
    one value, `chassis.image.tag`, holds a service on the previous tag after a library bump."""
    _, after = released
    pin = {"chassis": {"image": {"tag": CURRENT_TAG}}}
    pinned = _render(after, "example-agent", tmp_path / "pinned", pin, kind=True)
    moved = _render(after, "other-agent", tmp_path / "moved", {}, kind=True)
    assert _image(pinned, "chassis") == f"{KIND_CHASSIS_REPOSITORY}:{CURRENT_TAG}"
    assert _image(moved, "chassis") == f"{KIND_CHASSIS_REPOSITORY}:{NEW_TAG}"
    assert (_tag_env(pinned), _tag_env(moved)) == (CURRENT_TAG, NEW_TAG), "each reports its tag"
    # The pin is the one value that differs from the unpinned render of the same service.
    unpinned = _render(after, "example-agent", tmp_path / "unpinned", {}, kind=True)
    assert _without_chassis_image(pinned) == _without_chassis_image(unpinned)
