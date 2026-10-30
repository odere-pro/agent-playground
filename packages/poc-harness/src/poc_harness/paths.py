"""Where the repo's files are. The one place a `poc_harness` module finds files outside itself."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
"""The repo root (the package is installed editable from `packages/poc-harness/src`)."""
EXAMPLE_SCRIPT = ROOT / "packages/fake-model-server/scripts/example.yaml"
TYPESCRIPT_ECHO = ROOT / "packages/workloads/echo-typescript"
