"""Generate the published JSON Schemas from the models.

`make schemas` writes them; a test checks drift.

A workload in any language validates its events against `schemas/events.v0.json`.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from chassis.core.envelope import Context, Request, Response, TaskInput
from chassis.core.events import SCHEMA_VERSION, event_json_schema

SCHEMA_DIR = Path(__file__).resolve().parents[2] / "schemas"


def generate() -> dict[str, dict[str, Any]]:
    return {
        f"events.v{SCHEMA_VERSION}.json": event_json_schema(),
        f"task_input.v{SCHEMA_VERSION}.json": TaskInput.model_json_schema(),
        f"context.v{SCHEMA_VERSION}.json": Context.model_json_schema(),
        f"request.v{SCHEMA_VERSION}.json": Request.model_json_schema(),
        f"response.v{SCHEMA_VERSION}.json": Response.model_json_schema(),
    }


def render(schema: dict[str, Any]) -> str:
    return json.dumps(schema, indent=2, sort_keys=True) + "\n"


def write(target: Path = SCHEMA_DIR) -> list[Path]:
    target.mkdir(parents=True, exist_ok=True)
    written = []
    for name, schema in generate().items():
        path = target / name
        path.write_text(render(schema))
        written.append(path)
    return written


def drift(target: Path = SCHEMA_DIR) -> list[str]:
    """Names of schema files that differ from the models, or are missing."""
    out = []
    for name, schema in generate().items():
        path = target / name
        if not path.exists() or path.read_text() != render(schema):
            out.append(name)
    return out


if __name__ == "__main__":
    if "--write" in sys.argv:
        for p in write():
            print(f"wrote {p.relative_to(Path.cwd())}")
    else:
        stale = drift()
        print("schemas up to date" if not stale else f"stale: {', '.join(stale)}")
        sys.exit(1 if stale else 0)
