"""The checked-in schemas must match the models, and a non-Python event stream must validate."""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema
from chassis.core.events import parse_event
from chassis.schemas import SCHEMA_DIR, drift

FIXTURES = Path(__file__).parent / "fixtures"


def test_checked_in_schemas_match_the_models() -> None:
    assert drift() == [], "run `make schemas` and commit the result"


def test_events_from_a_typescript_workload_validate() -> None:
    schema = json.loads((SCHEMA_DIR / "events.v0.json").read_text())
    validator = jsonschema.Draft202012Validator(schema)
    lines = (FIXTURES / "events_from_typescript.jsonl").read_text().splitlines()
    assert len(lines) >= 4
    for line in lines:
        event = json.loads(line)
        validator.validate(event)
        parse_event(event)
