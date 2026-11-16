"""The library chart's drill on the PoC-5 cluster `poc05` (backlog 024 CH-3): what the kind test
(`test_poc09_kind_chart.py`) and its offline guard (`test_poc09_kind_chart_static.py`) share.

Two releases of `deploy/helm/example-agent`, the same chart under two names, stand for two
services that include the library chart. They run in `poc05-chart`, a namespace with Pod Security
`restricted` and the platform's default deny (`deploy/kind/poc05/base/`), and outside the
admission rules: whether the chart's defaults pass admission is 026 CH-4.

The images are built and loaded by `deploy/kind/poc05/run.sh` (`build`, `load`). The chart's own
tags are this checkout's `poc05` builds under a second tag: the chassis under the library's tag
(`v0.1.0`) in the `kind.local` repository `values-kind.yaml` names (139 CH-19), the workload under
`poc09`. `v0.2.0` is the next chassis release: the same build with one label more, so its image
id differs.

The chassis gets one stand-in credential, `chart-drill-token`, made by `seed.sh base` through a
pipe, as an env variable and as a mounted file. Nothing reads its value; the tests check names
and paths only.
"""

from __future__ import annotations

from typing import Any

from chart_render import CHASSIS_REPOSITORY as PUBLISHED_REPOSITORY
from chart_render import CURRENT_TAG as CURRENT_TAG
from chart_render import KIND_CHASSIS_REPOSITORY as CHASSIS_REPOSITORY
from chart_render import NEW_TAG

LIBRARY_REPOSITORY = PUBLISHED_REPOSITORY
CONTEXT = "kind-poc05"
NAMESPACE = "poc05-chart"
RELEASES = ("chart-a", "chart-b")
REGISTRY = "kind.local/agent-platform"
# The library's chassis tag today (`agent-lib.defaults`), and the release a bump moves to
# (`chart_render.bump_library`). On kind, CHASSIS_REPOSITORY is values-kind.yaml's, loaded into
# the node and pinned by tag; LIBRARY_REPOSITORY is the published default it replaces.
NEXT_TAG = NEW_TAG
WORKLOAD_IMAGE = f"{REGISTRY}/echo-python:poc09"
# The images run.sh puts on the node for the drill, besides its own `poc05` builds.
DRILL_IMAGES = (
    f"{CHASSIS_REPOSITORY}:{CURRENT_TAG}",
    f"{CHASSIS_REPOSITORY}:{NEXT_TAG}",
    WORKLOAD_IMAGE,
)

SECRET = "chart-drill-token"  # pragma: allowlist secret (a Secret name, no value)
SECRET_KEY = "token"  # pragma: allowlist secret (a key name, no value)
SECRET_ENV = "CHART_DRILL_TOKEN"  # pragma: allowlist secret (an env name, no value)
SECRET_MOUNT = "/etc/chart-drill"

CHASSIS_PORT = 8080
WORKLOAD_PORT = 9000


def drill_values() -> dict[str, Any]:
    """The values each release adds on top of `example-agent/values-kind.yaml`: one replica, no
    HPA (no metrics-server on `poc05`), and the stand-in credential on the chassis."""
    return {
        "replicas": 1,
        "hpa": {"enabled": False},
        "chassis": {
            "secretEnv": [{"name": SECRET_ENV, "secretName": SECRET, "key": SECRET_KEY}],
            "secretVolumes": [{"name": "drill", "secretName": SECRET, "mountPath": SECRET_MOUNT}],
        },
    }
