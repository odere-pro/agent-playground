"""The key manifest: the model routes a service's LiteLLM key lists, written at deploy (059 H-15).

The seed scripts mint the key and then call `write_key_manifest` (`deploy/kind/lib.sh`), which
writes one JSON object per key. `agentctl new --key-manifest PATH` reads it, so the routes come from
the deploy, not from a hand-typed `--key-route` list.

suggested: the format (version 1), exactly these fields and no others:

    {"version": 1, "key_alias": "svc-<svc>", "routes": ["<route>", ...],
     "written_at": "<UTC ISO 8601>"}

The manifest names routes only. It never holds the key: a field outside the four is refused, and
an error never echoes a value from the file.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agentctl.rules import UsageError

VERSION = 1
FIELDS: frozenset[str] = frozenset({"version", "key_alias", "routes", "written_at"})
SEED_HINT = (
    "the deploy seed writes it when it mints the key "
    "(`seed.sh keys` in deploy/kind/poc05/platform or deploy/kind/poc06)"
)


@dataclass(frozen=True)
class KeyManifest:
    key_alias: str
    routes: tuple[str, ...]
    written_at: str


def _refuse(path: Path, why: str) -> UsageError:
    return UsageError(f"--key-manifest {path}: {why}; nothing was written")


def _text(data: dict[str, Any], field: str, path: Path) -> str:
    value = data.get(field)
    if not isinstance(value, str) or not value:
        raise _refuse(path, f"{field} must be a non-empty string")
    return value


def _routes(data: dict[str, Any], path: Path) -> tuple[str, ...]:
    value = data.get("routes")
    if not isinstance(value, list) or not value:
        raise _refuse(path, "routes must be a non-empty list of route names")
    if not all(isinstance(route, str) and route for route in value):
        raise _refuse(path, "routes must hold non-empty route names only")
    return tuple(value)


def _load(path: Path) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise _refuse(path, f"no file there; {SEED_HINT}") from None
    except OSError as exc:
        raise _refuse(path, f"cannot read it ({exc.strerror})") from None
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise _refuse(path, f"not JSON (line {exc.lineno})") from None
    if not isinstance(data, dict):
        raise _refuse(path, "the manifest must be one JSON object")
    return data


def read_key_manifest(path: Path) -> KeyManifest:
    """Read and check one manifest. Every problem raises `UsageError` (the CLI exits 2)."""
    data = _load(path)
    extra = sorted(set(data) - FIELDS)
    if extra:
        raise _refuse(
            path,
            f"unknown field(s) {', '.join(map(repr, extra))}; a manifest names routes only, "
            "never the key",
        )
    if data.get("version") != VERSION:
        raise _refuse(path, f"version must be {VERSION}")
    return KeyManifest(
        key_alias=_text(data, "key_alias", path),
        routes=_routes(data, path),
        written_at=_text(data, "written_at", path),
    )
