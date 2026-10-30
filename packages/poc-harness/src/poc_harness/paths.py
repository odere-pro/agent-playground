"""The repo root, the one place a harness module finds files outside its package."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
"""The repo root: `packages/poc-harness/src/poc_harness/paths.py` sits four folders below it."""
