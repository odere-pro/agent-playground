"""PoC-5 process limit, static (exit criterion 8, H23; offline).

Under gVisor, hitting the per-pod pids cap restarts the whole sandbox (spike, question 6), so the
kind tier checks that the cap is set rather than exhausting it. This test is the offline half:
the kubelet in `deploy/kind/poc05/cluster.yaml` sets `podPidsLimit`, at most `PIDS_CEILING`, and
above `PIDS_FACTOR * (NPROC_ALLOWANCE + PIDS_BASELINE) + PIDS_HEADROOM`, so the code runner's
in-guest `RLIMIT_NPROC` is hit before the sandbox's pod cap (security review 2026-10-02, item
12). The control: the same parse finds the KubeletConfiguration patch at all, so a missing patch
fails here instead of passing as "no limit found"; and the band between floor and ceiling is not
empty.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml
from code_runner.isolation import NPROC_ALLOWANCE

ROOT = Path(__file__).resolve().parents[3]
CLUSTER = ROOT / "deploy/kind/poc05/cluster.yaml"
PIDS_CEILING = 256
"""suggested: the spike's value; a larger cap would weaken H23."""
PIDS_FACTOR = 3
"""suggested: spike question 6; the pod cap must hold three calls' worth of tasks at once."""
PIDS_BASELINE = 16
"""suggested: tasks the code runner's uid holds before a call (server threads, the child)."""
PIDS_HEADROOM = 16
"""suggested: pause, sidecars, and exec probes that count against the same pod cap."""


NODES: list[dict[str, Any]] = yaml.safe_load(CLUSTER.read_text())["nodes"]


def _kubelet_patches(node: dict[str, Any]) -> list[dict[str, Any]]:
    docs = [yaml.safe_load(raw) for raw in node.get("kubeadmConfigPatches", [])]
    return [doc for doc in docs if doc.get("kind") == "KubeletConfiguration"]


def pids_floor() -> int:
    """The smallest pod cap that still lets the in-guest cap trip first."""
    return PIDS_FACTOR * (NPROC_ALLOWANCE + PIDS_BASELINE) + PIDS_HEADROOM


def test_poc05_every_node_has_a_kubelet_patch() -> None:
    """Criterion 8: "The hostile suites pass in both lanes."

    Criterion 8, H23, the control: each node has one KubeletConfiguration patch, so a missing
    patch fails here instead of passing as "no limit found"."""
    assert NODES, "the cluster has no node"
    assert all(len(_kubelet_patches(node)) == 1 for node in NODES)


@pytest.mark.parametrize("node", NODES, ids=[f"{n['role']}-{i}" for i, n in enumerate(NODES)])
def test_poc05_kubelet_sets_a_small_pod_pids_limit(node: dict[str, Any]) -> None:
    """Criterion 8: "The hostile suites pass in both lanes."

    Criterion 8, H23: this node's `podPidsLimit` is above the floor and at most the ceiling."""
    limits = [patch.get("podPidsLimit") for patch in _kubelet_patches(node)]
    assert limits, "the node has no KubeletConfiguration patch"
    for limit in limits:
        assert isinstance(limit, int), "podPidsLimit is not set"
        assert pids_floor() < limit <= PIDS_CEILING, (
            f"podPidsLimit {limit} not in ({pids_floor()}, {PIDS_CEILING}]"
        )


def test_poc05_pids_floor_is_below_the_ceiling() -> None:
    """Criterion 8: "The hostile suites pass in both lanes."

    Criterion 8, H23, the control: the band is not empty, so some limit passes."""
    assert NPROC_ALLOWANCE > 0
    assert pids_floor() < PIDS_CEILING
