"""Backlog 056 CH-7 on kind: a chassis release through the rings, a pin, and a rollback, on `poc05`.

Four releases of `deploy/helm/example-agent` run in `poc05-chart` (`kind_rings`): one per ring,
and a fourth in `ten-percent` that pins the previous tag with its own `chassis.image.tag`. The
test walks the steps in `deploy/rings/README.md` with helm, as cluster admin: before the release,
the release (the library's tag bumped from `chart_render.CURRENT_TAG` to `NEW_TAG` in a copy of
the charts, and the rings it has not reached held on `CURRENT_TAG`), the promotion of
`ten-percent`, the promotion of `all`, then the canary's rollback. Each step is a
`helm upgrade --install --wait` of every release with the charts and ring overlay that step
holds. After each step it reads every
release's live pod: the chassis image the spec names, the image id the node runs, the
workload's image and image id, the ring label, and `GET /manifest` from the chassis.

What it proves (the issue's boxes):

- "A service pinned to the previous tag keeps it through a rollout, and a rollback is one change":
  the pinned release runs `CURRENT_TAG`, on the same pod, through every step, while its ring's
  other release moves to `NEW_TAG`; the canary's rollback is one overlay value and moves the
  canary only.
- "`/manifest` on every service reports the chassis version it runs": after each step, every
  release's `/manifest` `versions.chassis` equals its pod's chassis image tag.

What it does not prove: Argo CD driving the rings (its sync waves and the Application health
check), and the promotion gate (no Prometheus on `poc05`). Helm stands in for the sync.

A kind test: marked `network`, skipped unless `KIND_DRILLS=1`. It needs
`deploy/kind/poc05/run.sh up` (the images and the namespace). Run, from the repo root:
`KIND_DRILLS=1 uv run pytest -m network <this file> -v -s -rs`.
Every kubectl and helm call pins `--context kind-poc05`. The releases are removed after, and the
fixture fails if `helm -n poc05-chart list -q` still lists any release.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import pytest
from chart_render import Charts, bump_library, containers, make_charts
from kind_chart import CHASSIS_PORT, CHASSIS_REPOSITORY, CONTEXT, NAMESPACE, WORKLOAD_IMAGE
from kind_rings import (
    PINNED,
    PLAN,
    RING_LABEL,
    RING_OF,
    RING_RELEASES,
    RUNS,
    STEP_NAMES,
    changed_rings,
    write_values,
)
from poc_harness.drills import KIND_SANDBOX
from poc_harness.kind import get_json, kubectl, node_sh

pytestmark = KIND_SANDBOX

CHASSIS = "chassis"
WORKLOAD = "workload"
HELM_WAIT_S = 300
# The helm process gets a minute more than its own wait, so helm reports a slow rollout itself.
HELM_PROCESS_S = HELM_WAIT_S + 60
# Run in the chassis container with its own Python: the `versions` block of its `/manifest`.
MANIFEST_SCRIPT = (
    "import json, sys, urllib.request\n"
    "with urllib.request.urlopen(sys.argv[1], timeout=10) as r:\n"
    "    print(json.dumps({'status': r.status, 'versions': json.load(r)['versions']}))\n"
)

Snapshot = dict[str, Any]


def _live_pod(release: str) -> dict[str, Any]:
    """The release's one live pod: Running and not being deleted."""
    items = get_json("pods", "-n", NAMESPACE, "-l", f"app.kubernetes.io/instance={release}")
    live = [
        p
        for p in items["items"]
        if p["status"].get("phase") == "Running" and "deletionTimestamp" not in p["metadata"]
    ]
    assert len(live) == 1, f"{release}: want one live pod, got {len(live)}"
    found: dict[str, Any] = live[0]
    return found


def _status(pod: dict[str, Any], name: str) -> dict[str, Any]:
    every = [*pod["status"].get("initContainerStatuses", []), *pod["status"]["containerStatuses"]]
    (found,) = [s for s in every if s["name"] == name]
    status: dict[str, Any] = found
    return status


def _manifest_versions(pod: dict[str, Any]) -> dict[str, Any]:
    """`GET /manifest` on the chassis's public port, from inside its own container."""
    url = f"http://{pod['status']['podIP']}:{CHASSIS_PORT}/manifest"
    got = kubectl(
        "exec", "-n", NAMESPACE, pod["metadata"]["name"], "-c", CHASSIS, "--",
        "python", "-c", MANIFEST_SCRIPT, url,
    )  # fmt: skip
    assert got.returncode == 0, got.stderr[-400:]
    reply: dict[str, Any] = json.loads(got.stdout.strip().splitlines()[-1])
    assert reply["status"] == 200, reply
    versions: dict[str, Any] = reply["versions"]
    return versions


def _snapshot(release: str) -> Snapshot:
    pod = _live_pod(release)
    spec = containers(pod["spec"])
    ready = any(c["type"] == "Ready" and c["status"] == "True" for c in pod["status"]["conditions"])
    return {
        "uid": pod["metadata"]["uid"],
        "ready": ready,
        "ring": pod["metadata"]["labels"].get(RING_LABEL),
        "chassis": (spec[CHASSIS]["image"], _status(pod, CHASSIS)["imageID"]),
        "workload": (spec[WORKLOAD]["image"], _status(pod, WORKLOAD)["imageID"]),
        "env_tag": {e["name"]: e.get("value") for e in spec[CHASSIS]["env"]}["CHASSIS_IMAGE_TAG"],
        "manifest": _manifest_versions(pod),
    }


def _tag(snap: Snapshot) -> str:
    repository, tag = str(snap["chassis"][0]).rsplit(":", 1)
    assert repository == CHASSIS_REPOSITORY
    return tag


def _helm(charts: Charts, *args: str, check: bool = True) -> None:
    charts.helm(
        "--kube-context", CONTEXT, "-n", NAMESPACE, *args, check=check, timeout=HELM_PROCESS_S
    )


@pytest.fixture(scope="module")
def walk(tmp_path_factory: pytest.TempPathFactory) -> Iterator[dict[str, dict[str, Snapshot]]]:
    """Per step, per release: its live pod after the step. The releases are removed after."""
    held = node_sh("crictl images -o json")
    tags = {t for image in json.loads(held)["images"] for t in image.get("repoTags") or []}
    need = {f"{CHASSIS_REPOSITORY}:{t}" for run in RUNS.values() for t in run.values()}
    missing = sorted(i for i in need | {WORKLOAD_IMAGE} if i not in tags)
    assert not missing, f"not on the node: {missing}; run deploy/kind/poc05/run.sh build load"

    before = make_charts(tmp_path_factory.mktemp("rings-before"))
    after = make_charts(tmp_path_factory.mktemp("rings-after"))
    bump_library(after)
    values_dir = tmp_path_factory.mktemp("rings-values")
    seen: dict[str, dict[str, Snapshot]] = {}
    try:
        for step in PLAN:
            charts = after if step.released else before
            for release in RING_RELEASES:
                own, ring = write_values(values_dir, step, release)
                _helm(
                    charts, "upgrade", "--install", release, str(charts.example),
                    "-f", str(charts.example / "values-kind.yaml"), "-f", str(own),
                    "-f", str(ring), "--wait", "--timeout", f"{HELM_WAIT_S}s",
                )  # fmt: skip
            seen[step.name] = {release: _snapshot(release) for release in RING_RELEASES}
            line = "  ".join(f"{r}={_tag(s)}" for r, s in seen[step.name].items())
            print(f"\n{step.name}: {line}")
        yield seen
    finally:
        for release in RING_RELEASES:
            _helm(before, "uninstall", release, "--wait", check=False)
        left = before.helm(
            "--kube-context", CONTEXT, "-n", NAMESPACE, "list", "-q",
            check=False, timeout=HELM_PROCESS_S,
        )  # fmt: skip
        assert left.returncode == 0 and not left.stdout.strip(), (
            f"releases left in {NAMESPACE}: {left.stdout!r} {left.stderr[-400:]!r}"
        )


@pytest.mark.parametrize("step", STEP_NAMES)
def test_each_release_runs_the_tag_its_ring_and_pin_hold_after_each_step(
    walk: dict[str, dict[str, Snapshot]], step: str
) -> None:
    """Each ring runs the tag its overlay holds, a pin wins over its ring, and no workload image
    moves: the service images are the ones from before the release."""
    first = walk[STEP_NAMES[0]]
    for release, snap in walk[step].items():
        assert _tag(snap) == RUNS[step][release], (step, release)
        assert snap["ready"], (step, release)
        assert snap["ring"] == RING_OF[release], (step, release)
        assert snap["workload"][0] == WORKLOAD_IMAGE
        assert snap["workload"] == first[release]["workload"], f"{release}: workload image moved"


@pytest.mark.parametrize("step", STEP_NAMES)
def test_manifest_on_every_service_reports_the_chassis_tag_it_runs(
    walk: dict[str, dict[str, Snapshot]], step: str
) -> None:
    """056 CH-7: "`/manifest` on every service reports the chassis version it runs"."""
    for release, snap in walk[step].items():
        tag = _tag(snap)
        print(
            f"\n{step} {release}: image tag {tag}, /manifest chassis {snap['manifest']['chassis']}"
        )
        assert snap["env_tag"] == tag, (step, release)
        assert snap["manifest"]["chassis"] == tag, (step, release, snap["manifest"])


def test_each_reported_chassis_version_is_one_image_the_node_runs(
    walk: dict[str, dict[str, Snapshot]],
) -> None:
    """The version `/manifest` reports names the image, not only the chart's env: across every
    step and release, each reported version maps to one running image id, and the two differ."""
    ids: dict[str, set[str]] = {}
    for snaps in walk.values():
        for snap in snaps.values():
            ids.setdefault(snap["manifest"]["chassis"], set()).add(snap["chassis"][1])
    print(f"\n{ {version: sorted(i[-8:] for i in found) for version, found in ids.items()} }")
    assert sorted(ids) == sorted({t for run in RUNS.values() for t in run.values()})
    assert all(len(found) == 1 for found in ids.values()), ids
    assert len(set().union(*ids.values())) == len(ids), "two versions run the same image"


def test_a_pinned_service_keeps_the_previous_tag_through_the_rollout(
    walk: dict[str, dict[str, Snapshot]],
) -> None:
    """056 CH-7: "A service pinned to the previous tag keeps it through a rollout". The pin's own
    pod never rolls, while the other release in its ring moves to the new tag."""
    pins = [walk[step][PINNED] for step in STEP_NAMES]
    assert {_tag(s) for s in pins} == {RUNS["before"][PINNED]}
    assert len({s["uid"] for s in pins}) == 1, "the pinned service's pod rolled"
    assert len({s["chassis"] for s in pins}) == 1, "the pinned service's chassis image moved"
    twin = next(r for r in RING_RELEASES if RING_OF[r] == RING_OF[PINNED] and r != PINNED)
    moved = [walk[step][twin] for step in STEP_NAMES]
    assert _tag(moved[0]) == RUNS["before"][twin] != _tag(moved[-1]), "its ring did not move"
    assert moved[0]["chassis"][1] != moved[-1]["chassis"][1], "the ring's twin runs the same id"


def test_the_canary_rollback_is_one_value_and_moves_the_canary_only(
    walk: dict[str, dict[str, Snapshot]],
) -> None:
    """056 CH-7: "a rollback is one change". One overlay value; only the canary's pod rolls, back
    to the image id it ran before the release."""
    steps = {s.name: s for s in PLAN}
    assert changed_rings(steps["promote-all"], steps["rollback"]) == ["canary"]
    last, back, first = walk["promote-all"], walk["rollback"], walk["before"]
    for release in RING_RELEASES:
        if RING_OF[release] == "canary":
            assert back[release]["uid"] != last[release]["uid"], "the canary did not roll back"
            assert back[release]["chassis"] == first[release]["chassis"], "not the previous image"
        else:
            assert back[release]["uid"] == last[release]["uid"], f"{release} rolled"
            assert back[release]["chassis"] == last[release]["chassis"], release
