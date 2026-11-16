"""010 H-12 box 11, its second half: "The workload container is given no config-store
credential." (docs/planning/issues/010-H-12-config-loader.md; ADR-001 hard requirement 1)

Renders `deploy/helm/example-agent` with the chassis reading its config from the store
(`spec.adapters.config: minio`), the `CONFIG_S3_*` variables `S3Config.from_env` reads, and the
two keys from a Secret through `chassis.secretEnv`. Then checks, in both lanes:

- the paired control: the chassis container does hold the store Secret and the store variables,
  so "none on the workload" is not vacuous;
- the workload container has no `CONFIG_S3_*` variable, no reference to the store Secret (env,
  envFrom, or a secret volume), and no service account token;
- a value that tries to hand the workload a store key fails the render.

And, outside the chart: no workload source the platform ships (`packages/workloads`,
`packages/workload-a2a`, the `agentctl` template) imports an object-store client or reads a
`CONFIG_S3_*` variable, so no workload-side code path can reach the store.

The box's first half, the config and system prompt through `ctx`, was dropped by P-8, decided
2026-11-16 by the user, option 1: the workload keeps its own prompt, and the chassis reports its
version in `versions.prompt` (`docs/reviews/2026-11-06-backlog-review/proposals.md`).
Skipped, with the reason, when `helm` is not on PATH.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
from chart_render import (
    NEEDS_HELM,
    RELEASE,
    REMOTE_VALUES,
    Charts,
    Doc,
    containers,
    pod_of,
    secret_names,
    values_file,
)
from poc_harness.paths import ROOT

STORE_SECRET = "example-agent-config-store"  # pragma: allowlist secret (a Secret name, no value)
STORE_PREFIX = "CONFIG_S3_"
"""Every variable `chassis.adapters.s3.config.S3Config.from_env` reads starts with this."""

STORE_VALUES: dict[str, Any] = {
    "chassis": {
        "env": [
            {"name": "CONFIG_S3_ENDPOINT", "value": "minio.platform.svc.cluster.local:9000"},
            {"name": "CONFIG_S3_BUCKET", "value": "agent-config"},
        ],
        "secretEnv": [
            {
                "name": "LITELLM_API_KEY",
                "secretName": "example-agent-litellm",  # pragma: allowlist secret (a name)
                "key": "k",
            },
            {"name": "CONFIG_S3_ACCESS_KEY", "secretName": STORE_SECRET, "key": "access"},
            {"name": "CONFIG_S3_SECRET_KEY", "secretName": STORE_SECRET, "key": "secret"},
        ],
        "config": {"spec": {"adapters": {"config": "minio"}}},
    }
}


def _merge(base: dict[str, Any], over: dict[str, Any]) -> dict[str, Any]:
    """`over` on `base`, maps merged key by key; a new dict, neither input changed."""
    out = dict(base)
    for key, value in over.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def _env_names(container: Doc) -> set[str]:
    return {e["name"] for e in container.get("env", [])}


def _assert_store_on_chassis_only(chassis: Doc, chassis_pod: Doc, workload: Doc, pod: Doc) -> None:
    # Paired control: the chassis holds the store Secret and the store variables.
    assert STORE_SECRET in secret_names(chassis, chassis_pod)
    assert {"CONFIG_S3_ACCESS_KEY", "CONFIG_S3_SECRET_KEY", "CONFIG_S3_ENDPOINT"} <= _env_names(
        chassis
    )
    # The workload: none of it.
    assert STORE_SECRET not in secret_names(workload, pod)
    assert not {n for n in _env_names(workload) if n.startswith(STORE_PREFIX)}
    assert not workload.get("envFrom")
    assert pod["automountServiceAccountToken"] is False


@NEEDS_HELM
def test_sidecar_lane_the_workload_gets_no_config_store_credential(
    chart: Charts, tmp_path: Path
) -> None:
    docs = chart.template(values_file(tmp_path, STORE_VALUES))
    pod = pod_of(docs, RELEASE)
    by_name = containers(pod)
    _assert_store_on_chassis_only(by_name["chassis"], pod, by_name["workload"], pod)
    assert secret_names(by_name["workload"], pod) == set()


@NEEDS_HELM
def test_remote_lane_the_workload_gets_no_config_store_credential(
    chart: Charts, tmp_path: Path
) -> None:
    docs = chart.template(values_file(tmp_path, _merge(REMOTE_VALUES, STORE_VALUES)))
    chassis_pod = pod_of(docs, RELEASE)
    workload_pod = pod_of(docs, f"{RELEASE}-workload")
    workload = containers(workload_pod)["workload"]
    _assert_store_on_chassis_only(
        containers(chassis_pod)["chassis"], chassis_pod, workload, workload_pod
    )
    assert secret_names(workload, workload_pod) == {f"{RELEASE}-workload-token"}


@NEEDS_HELM
@pytest.mark.parametrize("name", ["CONFIG_S3_ACCESS_KEY", "CONFIG_S3_SECRET_KEY"])
def test_a_store_key_on_the_workload_fails_the_render(
    chart: Charts, tmp_path: Path, name: str
) -> None:
    values = _merge(STORE_VALUES, {"workload": {"env": [{"name": name, "value": "x"}]}})
    proc = chart.render(
        chart.example, RELEASE, "agents", values_file(tmp_path, values), check=False
    )
    assert proc.returncode != 0
    assert f"agent-lib: workload.env {name} looks like a credential" in proc.stderr


WORKLOAD_SOURCES = (
    ROOT / "packages/workloads",
    ROOT / "packages/workload-a2a/src",
    ROOT / "packages/agentctl/src/agentctl/template",
)
STORE_CLIENT = re.compile(
    r"^\s*(import|from)\s+(minio|boto3|botocore|aiobotocore|aioboto3|s3fs)\b"
    r"|\bgoogle\.cloud\.storage\b|^\s*from\s+google\.cloud\s+import\s+storage\b"
    r"""|\bfrom\s+['"]minio['"]|\brequire\(\s*['"]minio['"]\s*\)"""
    r"|@aws-sdk/client-s3|\bCONFIG_S3_",
    re.MULTILINE,
)
"""An object-store client import (Python, TypeScript, or CommonJS) or a read of the store
variables."""
SOURCE_SUFFIXES = {".py", ".jinja", ".ts", ".js", ".toml", ".json", ".yaml", ".yml", ".tpl"}


def _workload_sources() -> list[Path]:
    return sorted(
        path
        for root in WORKLOAD_SOURCES
        for path in root.rglob("*")
        if path.is_file()
        and path.suffix in SOURCE_SUFFIXES
        and not {"node_modules", ".venv", "dist"} & set(path.parts)
    )


def test_the_store_client_pattern_catches_a_store_import() -> None:
    # Paired control: the scan below has teeth.
    assert STORE_CLIENT.search("from minio import Minio\n")
    assert STORE_CLIENT.search("import boto3\n")
    assert STORE_CLIENT.search('key = os.environ["CONFIG_S3_SECRET_KEY"]\n')
    assert STORE_CLIENT.search('import { S3Client } from "@aws-sdk/client-s3";\n')
    assert STORE_CLIENT.search("import aioboto3\n")
    assert STORE_CLIENT.search("from google.cloud import storage\n")
    assert STORE_CLIENT.search("client = google.cloud.storage.Client()\n")
    assert STORE_CLIENT.search("import * as Minio from 'minio';\n")
    assert STORE_CLIENT.search("const Minio = require('minio');\n")


def test_no_workload_source_can_reach_the_config_store() -> None:
    sources = _workload_sources()
    assert len(sources) > 20, "the scan found the workload sources"
    hits = [
        str(path.relative_to(ROOT))
        for path in sources
        if STORE_CLIENT.search(path.read_text(encoding="utf-8", errors="replace"))
    ]
    assert hits == []
