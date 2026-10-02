"""PoC-5 process limit, static (exit criterion 8, H23; offline).

Under gVisor, hitting the per-pod pids cap restarts the whole sandbox (spike, question 6), so the
kind tier checks that the cap is set rather than exhausting it. This test is the offline half:
the kubelet in `deploy/kind/poc05/cluster.yaml` sets `podPidsLimit`, and the value is a small
positive number. The control: the same parse finds the KubeletConfiguration patch at all, so a
missing patch fails here instead of passing as "no limit found".
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[3]
CLUSTER = ROOT / "deploy/kind/poc05/cluster.yaml"
PIDS_CEILING = 256
"""suggested: the spike's value; a larger cap would weaken H23."""


def _kubelet_patches() -> list[dict[str, Any]]:
    cluster = yaml.safe_load(CLUSTER.read_text())
    patches: list[dict[str, Any]] = []
    for node in cluster["nodes"]:
        for raw in node.get("kubeadmConfigPatches", []):
            doc = yaml.safe_load(raw)
            if doc.get("kind") == "KubeletConfiguration":
                patches.append(doc)
    return patches


def test_poc05_every_node_has_a_kubelet_patch() -> None:
    cluster = yaml.safe_load(CLUSTER.read_text())
    assert cluster["nodes"], "the cluster has no node"
    assert len(_kubelet_patches()) == len(cluster["nodes"])


def test_poc05_kubelet_sets_a_small_pod_pids_limit() -> None:
    for patch in _kubelet_patches():
        limit = patch.get("podPidsLimit")
        assert isinstance(limit, int), "podPidsLimit is not set"
        assert 0 < limit <= PIDS_CEILING
