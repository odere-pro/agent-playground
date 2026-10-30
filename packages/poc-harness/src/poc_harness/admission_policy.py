"""Helpers that read the PoC-5 admission policy offline, shared with the PoC-6b static tests."""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from poc_harness.paths import ROOT

NAMESPACES = ROOT / "deploy/kind/poc05/base/namespaces.yaml"


SHAPE_LABEL = "agents.platform/pod-shape"


TRUST = "agents.platform/trust"


LANE = "agents.platform/lane"


ROLE = "agents.platform/role"


NAME = "app.kubernetes.io/name"


Doc = dict[str, Any]


def _load_all(path: Path) -> list[Doc]:
    return [d for d in yaml.safe_load_all(path.read_text()) if d]


def _namespace_labels() -> dict[str, dict[str, str]]:
    return {
        d["metadata"]["name"]: dict(d["metadata"].get("labels", {}))
        for d in _load_all(NAMESPACES)
        if d.get("kind") == "Namespace"
    }


def _containers(spec: Doc) -> list[Doc]:
    return [
        *spec.get("initContainers", []),
        *spec["containers"],
        *spec.get("ephemeralContainers", []),
    ]


def _template(doc: Doc) -> Doc:
    kind = doc["kind"]
    if kind == "Pod":
        return doc
    if kind == "CronJob":
        return dict(doc["spec"]["jobTemplate"]["spec"]["template"])
    if kind in {"Sandbox", "SandboxTemplate"}:
        return dict(doc["spec"]["podTemplate"])
    return dict(doc["spec"]["template"])


@dataclass(frozen=True)
class _Params:
    prefix: str
    trusted: tuple[str, ...]
    image_source: str = "pull-never"

    @classmethod
    def of(cls, doc: Doc) -> _Params:
        data = doc.get("data", {})
        trusted = tuple(
            r.strip() for r in data.get("trustedRepositories", "").split(",") if r.strip()
        )
        return cls(
            prefix=data.get("registryPrefix", ""),
            trusted=trusted,
            image_source=data.get("imageSource", ""),
        )


def _is_repo(image: str, repo: str) -> bool:
    return image == repo or image.startswith((repo + ":", repo + "@"))


TOKEN_SECRET = re.compile(r"remote-[a-z0-9]([-a-z0-9]*[a-z0-9])?-token")


def _secret_names(spec: Doc) -> Iterator[str]:
    for c in _containers(spec):
        for e in c.get("env", []):
            ref = e.get("valueFrom", {}).get("secretKeyRef")
            if ref is not None:
                yield str(ref.get("name", ""))
        for f in c.get("envFrom", []):
            if "secretRef" in f:
                yield str(f["secretRef"].get("name", ""))
    for v in spec.get("volumes", []):
        if "secret" in v:
            yield str(v["secret"].get("secretName", ""))
        for s in v.get("projected", {}).get("sources", []):
            if "secret" in s:
                yield str(s["secret"].get("name", ""))


def _projected_token(spec: Doc) -> bool:
    return any(
        "serviceAccountToken" in s
        for v in spec.get("volumes", [])
        for s in v.get("projected", {}).get("sources", [])
    )


def _uses_secret(c: Doc, secret_volumes: set[str]) -> bool:
    return (
        any("secretKeyRef" in e.get("valueFrom", {}) for e in c.get("env", []))
        or any("secretRef" in f for f in c.get("envFrom", []))
        or any(m["name"] in secret_volumes for m in c.get("volumeMounts", []))
    )


def _secret_volumes(spec: Doc) -> set[str]:
    return {
        v["name"]
        for v in spec.get("volumes", [])
        if "secret" in v or any("secret" in s for s in v.get("projected", {}).get("sources", []))
    }


def _gvisor_no_token(spec: Doc) -> bool:
    return (
        spec.get("runtimeClassName") == "gvisor"
        and spec.get("automountServiceAccountToken") is False
    )


def broken_rules(doc: Doc, p: _Params, namespace: str | None = None) -> set[str]:
    """The rules `doc` breaks, by the same logic as the CEL in `policy.yaml`. `namespace` defaults
    to the object's own; its shape label comes from `base/namespaces.yaml`."""
    ns = namespace or doc.get("metadata", {}).get("namespace", "")
    ns_shape = _namespace_labels().get(ns, {}).get(SHAPE_LABEL, "")
    template = _template(doc)
    labels = template.get("metadata", {}).get("labels", {})
    spec = template["spec"]
    containers = _containers(spec)
    images = [c.get("image", "") for c in containers]
    chassis_repo = p.prefix + "chassis"
    chassis = [i for i in images if _is_repo(i, chassis_repo)]
    workload = [i for i in images if i not in chassis]
    trust = labels.get(TRUST, "")
    sidecar = bool(chassis) and bool(workload)
    remote = not chassis and (labels.get(LANE, "") == "remote" or trust == "untrusted")
    shape = "chassis" if chassis else ("remote" if remote else "tool")
    names = list(_secret_names(spec))
    own_token = labels.get(NAME, "") + "-token"
    secret_users = [
        c.get("image", "") for c in containers if _uses_secret(c, _secret_volumes(spec))
    ]
    if p.image_source == "pull-never":
        own_store = all(c.get("imagePullPolicy") == "Never" for c in containers)
    else:
        own_store = all("@sha256:" in i for i in images)

    checks: dict[str, Callable[[], bool]] = {
        "0": lambda: (
            p.prefix != "" and bool(p.trusted) and p.image_source in {"pull-never", "digest"}
        ),
        "1": lambda: trust in {"trusted", "untrusted"},
        "2": lambda: not (sidecar and trust == "untrusted"),
        "3": lambda: remote or all(i.startswith(p.prefix) for i in images),
        "4": lambda: (
            not (sidecar and trust == "trusted")
            or all(
                any(_is_repo(i, r) for r in p.trusted) for i in workload if i.startswith(p.prefix)
            )
        ),
        "5": lambda: (
            not remote
            or (
                _gvisor_no_token(spec)
                and not _projected_token(spec)
                and all(TOKEN_SECRET.fullmatch(n) for n in names)
            )
        ),
        "6a": lambda: len(chassis) <= 1,
        "6b": lambda: all(
            not c.get("command") for c in containers if c.get("image", "") in chassis
        ),
        "6c": lambda: remote or all(i in chassis for i in secret_users),
        "7a": lambda: shape == ns_shape,
        "7b": lambda: labels.get(ROLE, "") != "chassis" or bool(chassis),
        "7c": lambda: (
            not remote or all(not TOKEN_SECRET.fullmatch(n) or n == own_token for n in names)
        ),
        "7d": lambda: shape != "tool" or _gvisor_no_token(spec),
        "8": lambda: remote or own_store,
    }
    return {n for n, ok in checks.items() if not ok()}
