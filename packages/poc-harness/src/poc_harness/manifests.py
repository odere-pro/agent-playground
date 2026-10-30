"""Read Kubernetes manifests: the documents of a file, a pod spec's containers, and its Secrets."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import yaml

Doc = dict[str, Any]


def load_manifests(path: Path) -> list[Doc]:
    """Every mapping in the YAML file `path`, in order. An empty document is dropped."""
    return [d for d in yaml.safe_load_all(path.read_text()) if d and isinstance(d, dict)]


def containers_of(spec: Doc) -> list[Doc]:
    """Every container of a pod spec: init, main, and ephemeral."""
    return [
        *spec.get("initContainers", []),
        *spec["containers"],
        *spec.get("ephemeralContainers", []),
    ]


def container_secret_names(container: Doc) -> Iterator[str]:
    """The Secret each `env` and `envFrom` entry of `container` reads."""
    for e in container.get("env", []):
        ref = e.get("valueFrom", {}).get("secretKeyRef")
        if ref is not None:
            yield str(ref.get("name", ""))
    for f in container.get("envFrom", []):
        if "secretRef" in f:
            yield str(f["secretRef"].get("name", ""))


def secret_names_of(spec: Doc) -> Iterator[str]:
    """Every Secret a pod spec names: in a container's env, or in a volume."""
    for c in containers_of(spec):
        yield from container_secret_names(c)
    for v in spec.get("volumes", []):
        if "secret" in v:
            yield str(v["secret"].get("secretName", ""))
        for s in v.get("projected", {}).get("sources", []):
            if "secret" in s:
                yield str(s["secret"].get("name", ""))
