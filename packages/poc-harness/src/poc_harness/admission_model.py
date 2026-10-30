"""A Python model of the PoC-5 admission rules. It mirrors the CEL in `admission/policy.yaml` and
`extension-policy.yaml`; it does not run it. The kind suite runs the real policy.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

from poc_harness.admission_manifests import (
    LANE,
    NAME,
    POOL,
    ROLE,
    SHAPE_LABEL,
    TRUST,
    namespace_labels,
    template_of,
)
from poc_harness.manifests import Doc, containers_of, secret_names_of


@dataclass(frozen=True)
class AdmissionParams:
    prefix: str
    trusted: tuple[str, ...]
    image_source: str = "pull-never"

    @classmethod
    def of(cls, doc: Doc) -> AdmissionParams:
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


@dataclass(frozen=True)
class _Pod:
    """What the rules read from one pod template, named the way the CEL variables are."""

    spec: Doc
    labels: dict[str, str]
    containers: list[Doc]
    images: list[str]
    chassis: list[str]
    workload: list[str]
    trust: str
    sidecar: bool
    remote: bool
    shape: str
    ns_shape: str
    names: list[str]
    secret_users: list[str]
    own_store: bool


def _pod_facts(doc: Doc, p: AdmissionParams, namespace: str | None) -> _Pod:
    ns = namespace or doc.get("metadata", {}).get("namespace", "")
    template = template_of(doc)
    labels = template.get("metadata", {}).get("labels", {})
    spec = template["spec"]
    containers = containers_of(spec)
    images = [c.get("image", "") for c in containers]
    chassis = [i for i in images if _is_repo(i, p.prefix + "chassis")]
    trust = labels.get(TRUST, "")
    remote = not chassis and (labels.get(LANE, "") == "remote" or trust == "untrusted")
    if p.image_source == "pull-never":
        own_store = all(c.get("imagePullPolicy") == "Never" for c in containers)
    else:
        own_store = all("@sha256:" in i for i in images)
    secret_volumes = _secret_volumes(spec)
    return _Pod(
        spec=spec,
        labels=labels,
        containers=containers,
        images=images,
        chassis=chassis,
        workload=[i for i in images if i not in chassis],
        trust=trust,
        sidecar=bool(chassis) and any(i not in chassis for i in images),
        remote=remote,
        shape="chassis" if chassis else ("remote" if remote else "tool"),
        ns_shape=namespace_labels().get(ns, {}).get(SHAPE_LABEL, ""),
        names=list(secret_names_of(spec)),
        secret_users=[c.get("image", "") for c in containers if _uses_secret(c, secret_volumes)],
        own_store=own_store,
    )


def _rule_checks(pod: _Pod, p: AdmissionParams) -> dict[str, Callable[[], bool]]:
    """Rule id to its check. Lazy, like the CEL: a rule's expression runs only when it is asked."""
    own_token = pod.labels.get(NAME, "") + "-token"
    return {
        "0": lambda: (
            p.prefix != "" and bool(p.trusted) and p.image_source in {"pull-never", "digest"}
        ),
        "1": lambda: pod.trust in {"trusted", "untrusted"},
        "2": lambda: not (pod.sidecar and pod.trust == "untrusted"),
        "3": lambda: pod.remote or all(i.startswith(p.prefix) for i in pod.images),
        "4": lambda: (
            not (pod.sidecar and pod.trust == "trusted")
            or all(
                any(_is_repo(i, r) for r in p.trusted)
                for i in pod.workload
                if i.startswith(p.prefix)
            )
        ),
        "5": lambda: (
            not pod.remote
            or (
                _gvisor_no_token(pod.spec)
                and not _projected_token(pod.spec)
                and all(TOKEN_SECRET.fullmatch(n) for n in pod.names)
            )
        ),
        "6a": lambda: len(pod.chassis) <= 1,
        "6b": lambda: all(
            not c.get("command") for c in pod.containers if c.get("image", "") in pod.chassis
        ),
        "6c": lambda: pod.remote or all(i in pod.chassis for i in pod.secret_users),
        "7a": lambda: pod.shape == pod.ns_shape,
        "7b": lambda: pod.labels.get(ROLE, "") != "chassis" or bool(pod.chassis),
        "7c": lambda: (
            not pod.remote
            or all(not TOKEN_SECRET.fullmatch(n) or n == own_token for n in pod.names)
        ),
        "7d": lambda: pod.shape != "tool" or _gvisor_no_token(pod.spec),
        "8": lambda: pod.remote or pod.own_store,
    }


def broken_rules(doc: Doc, p: AdmissionParams, namespace: str | None = None) -> set[str]:
    """The rules `doc` breaks, by the same logic as the CEL in `policy.yaml`. `namespace` defaults
    to the object's own; its shape label comes from `base/namespaces.yaml`.

    This is a hand-written model of the rules, not the policy: it never reads `policy.yaml`, so a
    fixture that agrees with it proves nothing about the CEL by itself. The expression pins in
    `test_rule_expression_matches_its_pin_when_the_policy_loads` tie the two together offline;
    `test_poc05_kind_admission.py` runs the real policy."""
    checks = _rule_checks(_pod_facts(doc, p, namespace), p)
    return {n for n, ok in checks.items() if not ok()}


def extension_broken_rules(doc: Doc) -> set[str]:
    """The template and claim rules `doc` breaks, by the same logic as `extension-policy.yaml`."""
    spec = doc.get("spec", {})
    if doc["kind"] == "SandboxTemplate":
        checks = {
            "T1": spec.get("networkPolicyManagement") == "Unmanaged",
            "T2": spec.get("envVarsInjectionPolicy", "Disallowed") == "Disallowed",
            "T3": spec.get("volumeClaimTemplatesPolicy", "Disallowed") == "Disallowed",
            "T4": not spec.get("volumeClaimTemplates"),
            "T5": "networkPolicy" not in spec,
        }
    elif doc["kind"] == "SandboxClaim":
        lifecycle = spec.get("lifecycle", {})
        checks = {
            "C1": not spec.get("env"),
            "C2": not any(
                (spec.get("additionalPodMetadata") or {}).get(k) for k in ("labels", "annotations")
            ),
            "C3": spec.get("warmPoolRef", {}).get("name") == POOL,
            "C4": lifecycle.get("shutdownPolicy") == "Delete" and "shutdownTime" in lifecycle,
            "C5": not spec.get("volumeClaimTemplates"),
        }
    else:
        checks = {}
    return {n for n, ok in checks.items() if not ok}


def all_broken_rules(doc: Doc, p: AdmissionParams) -> set[str]:
    trust = set() if doc["kind"] == "SandboxClaim" else broken_rules(doc, p)
    return trust | extension_broken_rules(doc)
