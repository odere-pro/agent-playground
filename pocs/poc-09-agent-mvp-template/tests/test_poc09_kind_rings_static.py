"""Backlog 056 CH-7, offline: the rings drill on `poc05` holds the plan it claims.

`test_poc09_kind_rings.py` walks a chassis release through the rings on kind with helm. This file
holds the same plan (`kind_rings.PLAN`, `RUNS`) with no cluster: each promotion and the rollback
is one value in one overlay, the overlays are the repo's with only `chassisTag` set, and
`helm template` of each step gives each release the tag `RUNS` expects, its ring label, and the
same workload image. Skipped, with the reason, when `helm` is not on PATH (the render tests).
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from chart_render import (
    CURRENT_TAG,
    NEEDS_HELM,
    NEW_TAG,
    Charts,
    bump_library,
    containers,
    make_charts,
    pod_of,
)
from kind_chart import CHASSIS_REPOSITORY, NAMESPACE, WORKLOAD_IMAGE
from kind_rings import (
    PINNED,
    PLAN,
    RING_LABEL,
    RING_OF,
    RING_RELEASES,
    RINGS,
    RINGS_DIR,
    RUNS,
    STEP_NAMES,
    changed_rings,
    overlay,
    write_values,
)


def test_each_promotion_and_the_rollback_change_one_ring_value() -> None:
    """README: each step is one change. The release bumps the library and holds the rings it has
    not reached; after it, each step changes one ring's `chassisTag`."""
    steps = {s.name: s for s in PLAN}
    assert STEP_NAMES == ("before", "release", "promote-ten", "promote-all", "rollback")
    assert not steps["before"].released and steps["release"].released
    assert changed_rings(steps["before"], steps["release"]) == ["ten-percent", "all"]
    assert changed_rings(steps["release"], steps["promote-ten"]) == ["ten-percent"]
    assert changed_rings(steps["promote-ten"], steps["promote-all"]) == ["all"]
    assert changed_rings(steps["promote-all"], steps["rollback"]) == ["canary"]


def test_the_drill_overlays_are_the_repo_files_with_only_the_tag_set() -> None:
    for ring in RINGS:
        repo = yaml.safe_load((RINGS_DIR / f"{ring}.yaml").read_text(encoding="utf-8"))
        assert repo == {"ring": {"name": ring, "chassisTag": ""}}
        assert overlay(ring, CURRENT_TAG) == {"ring": {"name": ring, "chassisTag": CURRENT_TAG}}


def test_one_release_per_ring_and_a_pinned_one_in_a_ring_that_moves() -> None:
    assert sorted(set(RING_OF.values())) == sorted(RINGS)
    assert [r for r in RING_RELEASES if RING_OF[r] == "canary"] == ["ring-canary"]
    pinned_ring = RING_OF[PINNED]
    moves = [r for r in RING_RELEASES if RING_OF[r] == pinned_ring and r != PINNED]
    assert moves, "the pin needs a twin in its ring that follows the ring"
    assert {RUNS[s][moves[0]] for s in STEP_NAMES} == {CURRENT_TAG, NEW_TAG}
    assert {RUNS[s][PINNED] for s in STEP_NAMES} == {CURRENT_TAG}


@pytest.fixture(scope="module")
def released(tmp_path_factory: pytest.TempPathFactory) -> Charts:
    charts = make_charts(tmp_path_factory.mktemp("rings-static"))
    bump_library(charts)
    return charts


@NEEDS_HELM
@pytest.mark.parametrize("name", STEP_NAMES)
def test_each_step_renders_the_tag_the_kind_drill_expects(
    chart: Charts, released: Charts, tmp_path: Path, name: str
) -> None:
    (found,) = [s for s in PLAN if s.name == name]
    charts = released if found.released else chart
    for release in RING_RELEASES:
        own, ring = write_values(tmp_path, found, release)
        proc = charts.helm(
            "template", release, str(charts.example), "--namespace", NAMESPACE,
            "-f", str(charts.example / "values-kind.yaml"), "-f", str(own), "-f", str(ring),
        )  # fmt: skip
        docs = [d for d in yaml.safe_load_all(proc.stdout) if d]
        pod = pod_of(docs, release)
        named = containers(pod)
        assert named["chassis"]["image"] == f"{CHASSIS_REPOSITORY}:{RUNS[found.name][release]}"
        env = {e["name"]: e.get("value") for e in named["chassis"]["env"]}
        assert env["CHASSIS_IMAGE_TAG"] == RUNS[found.name][release], (found.name, release)
        assert named["workload"]["image"] == WORKLOAD_IMAGE
        (deployment,) = [d for d in docs if d["kind"] == "Deployment"]
        labels = deployment["spec"]["template"]["metadata"]["labels"]
        assert labels[RING_LABEL] == RING_OF[release]
