"""Helpers for the remote-lane controls on kind (PoC-5), shared with the PoC-6b kind tests."""

from __future__ import annotations

from typing import Any


def traceparent(trace_id: str) -> dict[str, str]:
    return {"traceparent": f"00-{trace_id}-00f067aa0ba902b7-01"}


def secret_sources(spec: dict[str, Any]) -> list[tuple[str, str, str]]:
    """Every place a pod spec takes a Secret: (container, env name or kind, Secret name)."""
    found = []
    containers = [
        *spec.get("initContainers", []),
        *spec.get("containers", []),
        *spec.get("ephemeralContainers", []),
    ]
    for c in containers:
        for e in c.get("env", []):
            ref = (e.get("valueFrom") or {}).get("secretKeyRef")
            if ref:
                found.append((c["name"], e["name"], ref["name"]))
        found.extend(
            (c["name"], "envFrom", e["secretRef"]["name"])
            for e in c.get("envFrom", [])
            if "secretRef" in e
        )
    for v in spec.get("volumes", []):
        if "secret" in v:
            found.append(("volume", v["name"], v["secret"]["secretName"]))
        for source in (v.get("projected") or {}).get("sources", []):
            if "secret" in source:
                found.append(("volume", v["name"], source["secret"]["name"]))
            if "serviceAccountToken" in source:
                found.append(("volume", v["name"], "serviceAccountToken"))
    return found
