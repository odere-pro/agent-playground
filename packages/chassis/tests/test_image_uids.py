"""One uid table for every image this repo builds, and every manifest that runs one.

ADR-001 hard requirement 1 and the PoC-5 hardening table (plan section 2.11): the chassis and its
workload run as different uids, so neither can read the other's files. Each image has one
non-root uid, its gid equals its uid, and every Compose service and kind container that runs the
image uses that same uid. A drift between a Dockerfile and a manifest is a pod that fails to start
or, worse, a uid shared with a neighbour.

Also: the app code under `/app` is owned by root and read-only to the app user (no `COPY --chown`),
the image creates no home directory (`HOME=/tmp`), and Python images write no bytecode at run time.

Parses Dockerfiles and YAML only; no Docker, no network. The table is `suggested:` in
`deploy/README.md` ("Container uids").
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]

UIDS: dict[str, int] = {
    "chassis": 10001,
    "echo-python": 10002,
    "echo-pydanticai": 10002,
    "echo-langgraph": 10002,
    "echo-openai-agents": 10002,
    "echo-claude-agent": 10002,
    "echo-smolagents": 10002,
    "echo-typescript": 10002,
    "code-runner": 10003,
    "fake-model-server": 10004,
    "fake-mcp-server": 10005,
}
"""suggested: image name (the part after `agent-platform/`) to its uid and gid."""

REJECTED = re.compile(r"^#\s*expect:\s*rejected\s*$", re.M)
"""An admission fixture the policy must refuse. Each differs from its admitted twin in exactly the
path its `differs:` header names (test_poc05_admission_static.py), so it may run an image in the
wrong slot on purpose (rule 6a: the chassis image as the workload). Admitted fixtures are checked.
"""

DOCKERFILES: dict[str, Path] = {
    "chassis": ROOT / "packages/chassis/Dockerfile",
    "echo-python": ROOT / "packages/workloads/echo-python/Dockerfile",
    "echo-pydanticai": ROOT / "packages/workloads/echo-pydanticai/Dockerfile",
    "echo-langgraph": ROOT / "packages/workloads/echo-langgraph/Dockerfile",
    "echo-openai-agents": ROOT / "packages/workloads/echo-openai-agents/Dockerfile",
    "echo-claude-agent": ROOT / "packages/workloads/echo-claude-agent/Dockerfile",
    "echo-smolagents": ROOT / "packages/workloads/echo-smolagents/Dockerfile",
    "echo-typescript": ROOT / "packages/workloads/echo-typescript/Dockerfile",
    "code-runner": ROOT / "packages/code-runner/Dockerfile",
    "fake-model-server": ROOT / "packages/fake-model-server/Dockerfile",
    "fake-mcp-server": ROOT / "packages/fake-mcp-server/Dockerfile",
}
PYTHON_IMAGES = sorted(set(DOCKERFILES) - {"echo-typescript"})

OURS = re.compile(r"(?:^|/)agent-platform/([a-z0-9-]+)(?::|@|$)")
"""An image built from this repo: `[registry/]agent-platform/<name>[:tag]`."""
COMPOSE_DEFAULT = re.compile(r"^\$\{[A-Z0-9_]+:-(.+)\}$")
"""`${WORKLOAD_IMAGE:-agent-platform/echo-python:poc04}`: the default is the image."""


def _image_name(ref: str) -> str | None:
    m = COMPOSE_DEFAULT.match(ref)
    if m:
        ref = m.group(1)
    found = OURS.search(ref)
    return found.group(1) if found else None


def _want(name: str) -> int:
    assert name in UIDS, f"agent-platform/{name} is not in the uid table"
    return UIDS[name]


def _runtime_stage(text: str) -> str:
    """The last stage of a multi-stage Dockerfile: what the image runs."""
    return re.split(r"^FROM\s", text, flags=re.M)[-1]


def test_every_dockerfile_is_in_the_table() -> None:
    found = {
        p
        for p in (ROOT / "packages").rglob("Dockerfile")
        if "node_modules" not in p.parts and ".venv" not in p.parts
    }
    assert found == set(DOCKERFILES.values())
    assert set(UIDS) == set(DOCKERFILES)


def test_distinct_roles_have_distinct_uids() -> None:
    """Workloads share one uid; the chassis, code-runner, and each fake server have their own."""
    roles = {uid for name, uid in UIDS.items() if not name.startswith("echo-")}
    others = [uid for name, uid in UIDS.items() if not name.startswith("echo-")]
    assert len(roles) == len(others), UIDS
    assert UIDS["echo-python"] not in roles


@pytest.mark.parametrize("name", sorted(DOCKERFILES))
def test_image_creates_its_table_uid(name: str) -> None:
    uid = UIDS[name]
    stage = _runtime_stage(DOCKERFILES[name].read_text())
    assert re.search(rf"groupadd --gid {uid} \w+", stage), f"{name}: groupadd --gid {uid}"
    assert re.search(rf"useradd --uid {uid} --gid \w+", stage), f"{name}: useradd --uid {uid}"
    users = re.findall(r"^USER\s+(\S+)\s*$", stage, re.M)
    assert users == [f"{uid}:{uid}"], (
        f"{name}: USER {users} (numeric uid:gid, so runAsNonRoot holds)"
    )


@pytest.mark.parametrize("name", sorted(DOCKERFILES))
def test_app_code_is_root_owned(name: str) -> None:
    """The app user cannot change its own code: no `COPY --chown`, no home directory."""
    text = DOCKERFILES[name].read_text()
    chowned = [line for line in text.splitlines() if re.match(r"^COPY\b.*--chown", line)]
    assert not chowned, f"{name}: {chowned}"
    assert "--create-home" not in text, name
    assert re.search(r"\bHOME=/tmp\b", _runtime_stage(text)), f"{name}: HOME=/tmp"


@pytest.mark.parametrize("name", PYTHON_IMAGES)
def test_python_image_writes_no_bytecode(name: str) -> None:
    stage = _runtime_stage(DOCKERFILES[name].read_text())
    assert "PYTHONDONTWRITEBYTECODE=1" in stage, name


# --- manifests ---------------------------------------------------------------------------------


def _yaml_docs(path: Path) -> Iterator[Any]:
    yield from (d for d in yaml.safe_load_all(path.read_text()) if d is not None)


def _pod_specs(node: Any) -> Iterator[dict[str, Any]]:
    """Every mapping with a `containers` list, at any depth (Pod, Deployment, CronJob, Sandbox)."""
    if isinstance(node, dict):
        if isinstance(node.get("containers"), list):
            yield node
        for value in node.values():
            yield from _pod_specs(value)
    elif isinstance(node, list):
        for item in node:
            yield from _pod_specs(item)


def _kind_runs() -> Iterator[tuple[str, str, int | None, int | None]]:
    """(where, image name, effective runAsUser, effective runAsGroup) for every container."""
    for path in sorted((ROOT / "deploy/kind").rglob("*.yaml")):
        if REJECTED.search(path.read_text()):
            continue
        for doc in _yaml_docs(path):
            for spec in _pod_specs(doc):
                pod_sc = spec.get("securityContext") or {}
                for c in [*spec.get("initContainers", []), *spec["containers"]]:
                    name = _image_name(str(c.get("image", "")))
                    if name is None:
                        continue
                    sc = c.get("securityContext") or {}
                    uid = sc.get("runAsUser", pod_sc.get("runAsUser"))
                    gid = sc.get("runAsGroup", pod_sc.get("runAsGroup"))
                    yield f"{path.relative_to(ROOT)}:{c.get('name')}", name, uid, gid


def _compose_runs() -> Iterator[tuple[str, str, str | None]]:
    """(where, image name, `user:`) for every Compose service that runs one of our images."""
    for path in sorted((ROOT / "deploy/compose").glob("docker-compose*.yaml")):
        for doc in _yaml_docs(path):
            for svc_name, svc in (doc.get("services") or {}).items():
                name = _image_name(str((svc or {}).get("image", "")))
                if name is not None:
                    user = svc.get("user")
                    yield f"{path.relative_to(ROOT)}:{svc_name}", name, user


KIND = list(_kind_runs())
COMPOSE = list(_compose_runs())


def test_manifests_were_found() -> None:
    """Guard against a vacuous pass: every image in the table runs somewhere."""
    run = {name for _, name, *_ in KIND} | {name for _, name, _ in COMPOSE}
    assert run >= set(UIDS), sorted(set(UIDS) - run)


@pytest.mark.parametrize(("where", "name", "uid", "gid"), KIND, ids=[k[0] for k in KIND])
def test_kind_container_runs_the_image_uid(
    where: str, name: str, uid: int | None, gid: int | None
) -> None:
    want = _want(name)
    assert uid in (None, want), f"{where}: runAsUser {uid}, image {name} is {want}"
    assert gid in (None, want), f"{where}: runAsGroup {gid}, image {name} is {want}"


@pytest.mark.parametrize(("where", "name", "user"), COMPOSE, ids=[c[0] for c in COMPOSE])
def test_compose_service_runs_the_image_uid(where: str, name: str, user: str | None) -> None:
    want = _want(name)
    assert user in (None, f"{want}:{want}"), f"{where}: user {user!r}, image {name} is {want}"
