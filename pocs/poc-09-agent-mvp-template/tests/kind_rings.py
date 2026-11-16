"""The rollout rings' drill on the PoC-5 cluster `poc05` (backlog 056 CH-7): what the kind test
(`test_poc09_kind_rings.py`) and its offline twin (`test_poc09_kind_rings_static.py`) share.

Four releases of `deploy/helm/example-agent` stand for four services that include the library
chart, in `poc05-chart` (`kind_chart`). Three follow their ring, one per ring; the fourth sits in
`ten-percent` and pins the previous tag with its own `chassis.image.tag`. Each release layers its
ring's overlay from `deploy/rings/` last, as `agent-rings` passes it to Argo CD.

The drill walks the steps in `deploy/rings/README.md` with helm (no Argo CD on `poc05`): before
the release, the release (the library bump from `chart_render.CURRENT_TAG` to `NEW_TAG`, the
rings it has not reached held on `CURRENT_TAG`), the two promotions, then the canary's rollback.
`PLAN` is each step's values and `RUNS` the chassis tag each release must run after it.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from chart_render import CURRENT_TAG, NEW_TAG
from poc_harness.paths import ROOT

RINGS_DIR = ROOT / "deploy/rings"
RINGS = ("canary", "ten-percent", "all")
RING_LABEL = "agents.platform/chassis-ring"

# release: its ring. The pinned one shares `ten-percent` with a release that follows it.
RING_OF = {
    "ring-canary": "canary",
    "ring-ten": "ten-percent",
    "ring-pin": "ten-percent",
    "ring-all": "all",
}
RING_RELEASES = tuple(RING_OF)
PINNED = "ring-pin"


@dataclass(frozen=True)
class Step:
    """One step of a release: whether the library holds the new tag, and each ring's tag."""

    name: str
    released: bool
    held: dict[str, str]


PLAN: tuple[Step, ...] = (
    Step("before", False, {"canary": "", "ten-percent": "", "all": ""}),
    Step("release", True, {"canary": "", "ten-percent": CURRENT_TAG, "all": CURRENT_TAG}),
    Step("promote-ten", True, {"canary": "", "ten-percent": "", "all": CURRENT_TAG}),
    Step("promote-all", True, {"canary": "", "ten-percent": "", "all": ""}),
    Step("rollback", True, {"canary": CURRENT_TAG, "ten-percent": "", "all": ""}),
)
STEP_NAMES = tuple(s.name for s in PLAN)

_C, _N = CURRENT_TAG, NEW_TAG
# step: the chassis tag each release runs after it (canary, ten, pin, all).
RUNS: dict[str, dict[str, str]] = {
    "before": dict(zip(RING_RELEASES, (_C, _C, _C, _C), strict=True)),
    "release": dict(zip(RING_RELEASES, (_N, _C, _C, _C), strict=True)),
    "promote-ten": dict(zip(RING_RELEASES, (_N, _N, _C, _C), strict=True)),
    "promote-all": dict(zip(RING_RELEASES, (_N, _N, _C, _N), strict=True)),
    "rollback": dict(zip(RING_RELEASES, (_C, _N, _C, _N), strict=True)),
}


def overlay(ring: str, tag: str) -> dict[str, Any]:
    """The repo's overlay for `ring` with `ring.chassisTag` set: the one value a step changes."""
    data: dict[str, Any] = yaml.safe_load((RINGS_DIR / f"{ring}.yaml").read_text(encoding="utf-8"))
    return {**data, "ring": {**data["ring"], "chassisTag": tag}}


def service_values(release: str) -> dict[str, Any]:
    """The release's own values on top of `example-agent/values-kind.yaml`: one replica, no HPA
    (no metrics-server on `poc05`), and for the pinned service the previous tag."""
    values: dict[str, Any] = {"replicas": 1, "hpa": {"enabled": False}}
    if release == PINNED:
        values["chassis"] = {"image": {"tag": CURRENT_TAG}}
    return values


def write_values(base: Path, step: Step, release: str) -> tuple[Path, Path]:
    """The release's own values file and its ring's overlay file for `step`."""
    ring = RING_OF[release]
    own = base / step.name / release / "own.yaml"
    ring_file = base / step.name / release / "ring.yaml"
    own.parent.mkdir(parents=True, exist_ok=True)
    own.write_text(yaml.safe_dump(service_values(release)), encoding="utf-8")
    ring_file.write_text(yaml.safe_dump(overlay(ring, step.held[ring])), encoding="utf-8")
    return own, ring_file


def changed_rings(before: Step, after: Step) -> list[str]:
    return [r for r in RINGS if before.held[r] != after.held[r]]
