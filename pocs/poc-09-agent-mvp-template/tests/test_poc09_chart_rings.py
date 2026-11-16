"""Backlog 056 CH-7, offline: the chassis rollout rings, rendered (ADR-001 items 9 and 10).

A chassis release is one library tag bump, rolled out in three rings: `canary`, `ten-percent`,
then `all` (suggested names; the epic gives the order). Each ring is a values overlay in
`deploy/rings/<ring>.yaml` that every service in the ring layers on its own values:

- `ring.name` labels the pod `agents.platform/chassis-ring`, so the promotion gate compares the
  ring's error rate and latency against the services still on the previous tag.
- `ring.chassisTag` holds the ring on a tag. Empty follows the library's tag. A release sets it to
  the previous tag in the rings it has not reached; a promotion clears it in the next ring; a
  rollback sets it back. Each step is one value in one file.
- A service's own `chassis.image.tag` is a pin, and wins over its ring.
- The library's digest pins the library's tag only (139 CH-19). So a hold is one value where the
  chassis is pinned by tag (a service's `chassis.image.digest: ""`, as on kind); under the
  library's digest the render refuses a hold rather than run the library's image under the held
  tag's name.

`deploy/helm/agent-rings` renders one Argo CD Application per service, its ring's overlay in its
value files and the ring's sync wave on it (canary 0, ten-percent 1, all 2), so a fast-ring
urgent fix still reaches the canary first.

What this does not prove: a rollout on a cluster, the gate stopping one, and Argo CD itself.
Those are the issue's live boxes. Skipped, with the reason, when `helm` is not on PATH.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml
from chart_render import (
    CHASSIS_REPOSITORY,
    CURRENT_TAG,
    NAMESPACE,
    NEEDS_HELM,
    NEW_DIGEST,
    NEW_TAG,
    PREVIOUS_TAG,
    Charts,
    Doc,
    bump_library,
    containers,
    make_charts,
    pod_of,
    values_file,
)
from poc_harness.paths import ROOT

pytestmark = NEEDS_HELM

RINGS_DIR = ROOT / "deploy/rings"
RINGS = ("canary", "ten-percent", "all")
WAVES = {"canary": "0", "ten-percent": "1", "all": "2"}
RING_LABEL = "agents.platform/chassis-ring"
WAVE = "argocd.argoproj.io/sync-wave"
WORKLOAD_IMAGE = "kind.local/agent-platform/echo-python:poc09"
# The service pins its chassis by tag, so a ring's hold names the image it runs (139 CH-19).
BY_TAG: dict[str, Any] = {"chassis": {"image": {"digest": ""}}}


@pytest.fixture(scope="module")
def released(tmp_path_factory: pytest.TempPathFactory) -> Charts:
    """The charts after a chassis release: the library's tag is NEW_TAG, and CURRENT_TAG is the
    previous one."""
    charts = make_charts(tmp_path_factory.mktemp("helm-rings"))
    bump_library(charts)
    return charts


def _overlay(ring: str) -> dict[str, Any]:
    data: dict[str, Any] = yaml.safe_load((RINGS_DIR / f"{ring}.yaml").read_text(encoding="utf-8"))
    return data


def _with_tag(ring: str, tag: str) -> dict[str, Any]:
    """The ring's overlay with `ring.chassisTag` set: the one value a release step changes."""
    overlay = _overlay(ring)
    return {**overlay, "ring": {**overlay["ring"], "chassisTag": tag}}


def _render(
    charts: Charts, tmp_path: Path, overlay: dict[str, Any], service: dict[str, Any] | None = None
) -> Doc:
    """Render `example-agent` with the service's values, then the ring overlay on top, as Argo
    CD passes them (`valueFiles: [values.yaml, <ring>.yaml]`). The service's values default to
    BY_TAG."""
    own = values_file(
        tmp_path / "own", {"workload": {"image": WORKLOAD_IMAGE}, **(service or BY_TAG)}
    )
    ring = values_file(tmp_path / "ring", overlay)
    proc = charts.helm(
        "template", "example-agent", str(charts.example), "--namespace", NAMESPACE,
        "-f", str(own), "-f", str(ring),
    )  # fmt: skip
    docs = [d for d in yaml.safe_load_all(proc.stdout) if d]
    return pod_of(docs, "example-agent")


def _chassis_tag(pod: Doc) -> str:
    image: str = containers(pod)["chassis"]["image"]
    repository, tag = image.rsplit(":", 1)
    assert repository == CHASSIS_REPOSITORY
    env = {e["name"]: e.get("value") for e in containers(pod)["chassis"].get("env", [])}
    assert env["CHASSIS_IMAGE_TAG"] == tag, "the chassis reports the tag it runs (/manifest)"
    return tag


def _template_labels(charts: Charts, tmp_path: Path, overlay: dict[str, Any]) -> dict[str, str]:
    own = values_file(tmp_path / "own", {"workload": {"image": WORKLOAD_IMAGE}})
    ring = values_file(tmp_path / "ring", overlay)
    proc = charts.helm(
        "template", "example-agent", str(charts.example), "--namespace", NAMESPACE,
        "-f", str(own), "-f", str(ring),
    )  # fmt: skip
    (deployment,) = [d for d in yaml.safe_load_all(proc.stdout) if d and d["kind"] == "Deployment"]
    labels: dict[str, str] = deployment["spec"]["template"]["metadata"]["labels"]
    return labels


# --- the overlays -----------------------------------------------------------------------------


def test_there_are_three_ring_overlays_and_each_follows_the_library_between_releases() -> None:
    """The steady state in the repo: every ring's `chassisTag` is empty, so every service runs the
    library's tag. A release writes the previous tag into the rings it has not reached."""
    assert sorted(p.stem for p in RINGS_DIR.glob("*.yaml")) == sorted(RINGS)
    for ring in RINGS:
        assert _overlay(ring) == {"ring": {"name": ring, "chassisTag": ""}}, ring


@pytest.mark.parametrize("ring", RINGS)
def test_a_service_carries_its_ring_on_the_pod(released: Charts, tmp_path: Path, ring: str) -> None:
    """The gate compares rings by this label (`deploy/prometheus/rules/rings.yaml`)."""
    assert _template_labels(released, tmp_path, _overlay(ring))[RING_LABEL] == ring


def test_a_service_in_no_ring_has_no_ring_label(released: Charts, tmp_path: Path) -> None:
    """The ring is opt-in per service, so the charts that predate the rings render unchanged."""
    assert RING_LABEL not in _template_labels(released, tmp_path, {})


# --- a release, step by step ------------------------------------------------------------------

# Each step: the tag each ring's overlay holds (empty follows the library), and what each ring runs.
STEPS: dict[str, tuple[dict[str, str], dict[str, str]]] = {
    "the release reaches the canary only": (
        {"canary": "", "ten-percent": CURRENT_TAG, "all": CURRENT_TAG},
        {"canary": NEW_TAG, "ten-percent": CURRENT_TAG, "all": CURRENT_TAG},
    ),
    "a promotion moves ten-percent": (
        {"canary": "", "ten-percent": "", "all": CURRENT_TAG},
        {"canary": NEW_TAG, "ten-percent": NEW_TAG, "all": CURRENT_TAG},
    ),
    "the last promotion moves all": (
        {"canary": "", "ten-percent": "", "all": ""},
        {"canary": NEW_TAG, "ten-percent": NEW_TAG, "all": NEW_TAG},
    ),
    "a rollback of the canary": (
        {"canary": CURRENT_TAG, "ten-percent": CURRENT_TAG, "all": CURRENT_TAG},
        {"canary": CURRENT_TAG, "ten-percent": CURRENT_TAG, "all": CURRENT_TAG},
    ),
}


@pytest.mark.parametrize("step", list(STEPS))
def test_each_ring_runs_the_tag_its_overlay_holds_with_no_service_image_rebuilt(
    released: Charts, tmp_path: Path, step: str
) -> None:
    held, runs = STEPS[step]
    for ring in RINGS:
        pod = _render(released, tmp_path / ring, _with_tag(ring, held[ring]))
        assert _chassis_tag(pod) == runs[ring], (step, ring)
        assert containers(pod)["workload"]["image"] == WORKLOAD_IMAGE, "no service image moves"


def _changed(before: str, after: str) -> list[str]:
    return [r for r in RINGS if STEPS[before][0][r] != STEPS[after][0][r]]


def test_each_promotion_and_the_rollback_is_one_value_in_one_overlay() -> None:
    """From the release: each promotion clears one ring's tag, and a rollback sets one."""
    release, promote, last, rollback = STEPS
    assert _changed(release, promote) == ["ten-percent"]
    assert _changed(promote, last) == ["all"]
    assert _changed(release, rollback) == ["canary"]


@pytest.mark.parametrize("ring", RINGS)
def test_a_pinned_service_keeps_its_tag_whatever_its_ring_holds(
    released: Charts, tmp_path: Path, ring: str
) -> None:
    """A service's own `chassis.image.tag` wins over the ring's, in every release step."""
    pin = {"chassis": {"image": {"tag": CURRENT_TAG, "digest": ""}}}
    for held in ("", CURRENT_TAG):
        pod = _render(released, tmp_path / (held or "follow"), _with_tag(ring, held), pin)
        assert _chassis_tag(pod) == CURRENT_TAG, (ring, held)


@pytest.mark.parametrize("ring", RINGS)
def test_a_ring_hold_under_the_library_digest_is_refused(
    released: Charts, tmp_path: Path, ring: str
) -> None:
    """139 CH-19 with 056 CH-7: the library's digest pins its own tag. A service that keeps it
    follows its ring onto that image only; a hold needs the service's own digest."""
    own = values_file(tmp_path / "own", {"workload": {"image": WORKLOAD_IMAGE}})
    for held in ("", CURRENT_TAG):
        overlay = values_file(tmp_path / (held or "follow"), _with_tag(ring, held))
        proc = released.helm(
            "template", "example-agent", str(released.example), "--namespace", NAMESPACE,
            "-f", str(own), "-f", str(overlay), check=False,
        )  # fmt: skip
        if not held:
            pod = pod_of([d for d in yaml.safe_load_all(proc.stdout) if d], "example-agent")
            assert containers(pod)["chassis"]["image"].endswith(f":{NEW_TAG}@{NEW_DIGEST}")
            continue
        assert proc.returncode != 0, f"rendered {ring}'s hold under the library's digest"
        assert "ring.chassisTag" in proc.stderr and "chassis.image.digest" in proc.stderr


# --- refusals ---------------------------------------------------------------------------------

# (overlay, a word the agent-lib: message must carry)
REFUSED: dict[str, tuple[dict[str, Any], str]] = {
    "a ring that is not one of the three": ({"ring": {"name": "half"}}, "ring.name"),
    "a ring tag below the minimum version": (
        {"ring": {"name": "all", "chassisTag": PREVIOUS_TAG}},
        "below the minimum",
    ),
    "a ring tag of latest": ({"ring": {"name": "all", "chassisTag": "latest"}}, "latest"),
    "a ring key the chart does not read": (
        {"ring": {"name": "all", "wave": 3}},
        "ring.wave",
    ),
}


@pytest.mark.parametrize("case", sorted(REFUSED))
def test_the_render_refuses_a_ring_value_that_breaks_the_rollout(
    released: Charts, tmp_path: Path, case: str
) -> None:
    overlay, needle = REFUSED[case]
    own = values_file(tmp_path / "own", {"workload": {"image": WORKLOAD_IMAGE}})
    ring = values_file(tmp_path / "ring", overlay)
    proc = released.helm(
        "template", "example-agent", str(released.example), "--namespace", NAMESPACE,
        "-f", str(own), "-f", str(ring), check=False,
    )  # fmt: skip
    assert proc.returncode != 0, f"{case}: rendered, want a refusal"
    assert "agent-lib:" in proc.stderr and needle in proc.stderr, proc.stderr


# --- the Argo CD Applications ------------------------------------------------------------------

SERVICES: list[dict[str, Any]] = [
    {
        "name": "echo-canary",
        "path": "deploy/helm/echo-canary",
        "namespace": "agents",
        "ring": "canary",
    },
    {
        "name": "billing",
        "path": "deploy/helm/billing",
        "namespace": "agents",
        "ring": "ten-percent",
    },
    {"name": "search", "path": "deploy/helm/search", "namespace": "agents", "ring": "all"},
    {"name": "triage", "path": "deploy/helm/triage", "namespace": "agents", "ring": "all"},
]


def _applications(charts: Charts, tmp_path: Path, services: list[dict[str, Any]]) -> list[Doc]:
    path = values_file(tmp_path, {"services": services})
    proc = charts.helm("template", "agent-rings", str(charts.rings), "--namespace", "argocd",
                       "-f", str(path))  # fmt: skip
    return [d for d in yaml.safe_load_all(proc.stdout) if d]


def test_each_service_is_an_application_in_its_rings_sync_wave(
    chart: Charts, tmp_path: Path
) -> None:
    apps = {a["metadata"]["name"]: a for a in _applications(chart, tmp_path, SERVICES)}
    assert sorted(apps) == sorted(s["name"] for s in SERVICES)
    for service in SERVICES:
        app = apps[service["name"]]
        ring = service["ring"]
        assert app["kind"] == "Application" and app["apiVersion"] == "argoproj.io/v1alpha1"
        assert app["metadata"]["annotations"][WAVE] == WAVES[ring]
        assert app["metadata"]["labels"][RING_LABEL] == ring
        source = app["spec"]["source"]
        assert source["path"] == service["path"]
        # The service's values first, then its ring's overlay, so the ring's tag applies.
        files = source["helm"]["valueFiles"]
        assert files[0] == "values.yaml" and files[-1].endswith(f"deploy/rings/{ring}.yaml")
        assert app["spec"]["destination"]["namespace"] == service["namespace"]


def test_the_canary_wave_comes_before_every_other_ring(chart: Charts, tmp_path: Path) -> None:
    apps = _applications(chart, tmp_path, SERVICES)
    waves = {
        a["metadata"]["labels"][RING_LABEL]: int(a["metadata"]["annotations"][WAVE]) for a in apps
    }
    assert waves["canary"] < waves["ten-percent"] < waves["all"]


APP_REFUSED: dict[str, tuple[list[dict[str, Any]], str]] = {
    "no canary": ([s for s in SERVICES if s["ring"] != "canary"], "one canary"),
    "two canaries": (
        [*SERVICES, {**SERVICES[0], "name": "echo-canary-2"}],
        "one canary",
    ),
    "a service with no ring": ([*SERVICES, {**SERVICES[1], "name": "x", "ring": ""}], "ring"),
    "a ring that is not one of the three": (
        [*SERVICES, {**SERVICES[1], "name": "x", "ring": "half"}],
        "ring",
    ),
    "a service named twice": ([*SERVICES, SERVICES[2]], "twice"),
}


@pytest.mark.parametrize("case", sorted(APP_REFUSED))
def test_the_rings_chart_refuses_a_ring_plan_that_skips_the_canary(
    chart: Charts, tmp_path: Path, case: str
) -> None:
    services, needle = APP_REFUSED[case]
    path = values_file(tmp_path, {"services": services})
    proc = chart.helm("template", "agent-rings", str(chart.rings), "--namespace", "argocd",
                      "-f", str(path), check=False)  # fmt: skip
    assert proc.returncode != 0, f"{case}: rendered, want a refusal"
    assert "agent-rings:" in proc.stderr and needle in proc.stderr, proc.stderr


# --- the minimum-version binding shows its ring ------------------------------------------------

RING_ANNOTATION = "agents.platform/chassis-version-ring"


@pytest.mark.parametrize(("action", "shown"), [("Deny", None), ("Warn", "warn")])
def test_the_version_binding_shows_the_warn_ring_on_itself(
    chart: Charts, tmp_path: Path, action: str, shown: str | None
) -> None:
    """026 CH-4 and the platform-security review of 2026-11-08: in Warn mode the binding carries
    `agents.platform/chassis-version-ring: warn`, so `kubectl get` shows the ring, not only the
    values. In Deny mode it carries none."""
    path = values_file(tmp_path, {"admission": {"chassisVersionAction": action}})
    (binding,) = [
        d
        for d in chart.template_admission(path)
        if d["kind"] == "ValidatingAdmissionPolicyBinding"
        and d["metadata"]["name"] == "agent-chassis-version"
    ]
    assert (binding["metadata"].get("annotations") or {}).get(RING_ANNOTATION) == shown
