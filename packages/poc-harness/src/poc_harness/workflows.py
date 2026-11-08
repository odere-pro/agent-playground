"""Read GitHub workflow files, for the tests that pin what CI runs."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any

import yaml

PINNED = re.compile(r"^[\w.-]+/[\w./-]+@[0-9a-f]{40}$")
"""An action reference pinned by commit."""
SHA256 = re.compile(r"^[0-9a-f]{64}$")


def load_workflow(path: Path) -> dict[str, Any]:
    doc = yaml.safe_load(path.read_text())
    assert isinstance(doc, dict), path
    # YAML 1.1 reads the bare key `on` as the boolean True.
    if True in doc:
        doc["on"] = doc.pop(True)
    return doc


def workflow_steps(doc: dict[str, Any]) -> list[dict[str, Any]]:
    steps: list[dict[str, Any]] = []
    for job in doc["jobs"].values():
        steps.extend(job.get("steps", []))
    return steps


def workflow_runs(doc: dict[str, Any]) -> str:
    return "\n".join(str(s.get("run", "")) for s in workflow_steps(doc))


def workflow_triggers(doc: dict[str, Any]) -> set[str]:
    triggers = doc["on"]
    return set(triggers) if isinstance(triggers, (dict, list)) else {triggers}


def gate_paths(root: Path) -> dict[str, list[str]]:
    """`make gate-paths` in `root`: {"gate": what the CI gate tests, "local": the test folders of
    the finished PoCs, which run only in `make test-pocs-done`}."""
    done = subprocess.run(
        ["make", "--no-print-directory", "-s", "gate-paths"],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )
    lists: dict[str, list[str]] = {}
    for line in done.stdout.splitlines():
        name, _, paths = line.partition(":")
        lists[name.strip()] = paths.split()
    if set(lists) != {"gate", "local"}:
        raise RuntimeError(f"make gate-paths printed {done.stdout!r}")
    return lists
