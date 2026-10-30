"""Helpers that read the CI workflow files, shared by the PoC-5 and PoC-6b static tests."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

PINNED = re.compile(r"^[\w.-]+/[\w./-]+@[0-9a-f]{40}$")


SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _workflow(path: Path) -> dict[str, Any]:
    doc = yaml.safe_load(path.read_text())
    assert isinstance(doc, dict), path
    # YAML 1.1 reads the bare key `on` as the boolean True.
    if True in doc:
        doc["on"] = doc.pop(True)
    return doc


def _steps(doc: dict[str, Any]) -> list[dict[str, Any]]:
    steps: list[dict[str, Any]] = []
    for job in doc["jobs"].values():
        steps.extend(job.get("steps", []))
    return steps


def _runs(doc: dict[str, Any]) -> str:
    return "\n".join(str(s.get("run", "")) for s in _steps(doc))


def _triggers(doc: dict[str, Any]) -> set[str]:
    triggers = doc["on"]
    return set(triggers) if isinstance(triggers, (dict, list)) else {triggers}
