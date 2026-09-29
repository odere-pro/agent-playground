from __future__ import annotations

import pytest
from chassis.core.events import Delta, End, Error, Start, UnsupportedSchemaVersion, parse_event
from pydantic import ValidationError


def test_parse_each_event_type() -> None:
    assert parse_event({"type": "start", "request_id": "r"}) == Start(request_id="r")
    assert parse_event({"type": "delta", "text": "hi"}) == Delta(text="hi")
    assert parse_event({"type": "end"}) == End()
    assert parse_event({"type": "error", "code": "x", "message": "m"}) == Error(
        code="x", message="m"
    )


def test_unknown_type_is_rejected() -> None:
    with pytest.raises(ValidationError):
        parse_event({"type": "thought", "text": "hmm"})


def test_unknown_field_is_rejected() -> None:
    with pytest.raises(ValidationError):
        parse_event({"type": "delta", "text": "hi", "extra": 1})


def test_unknown_schema_version_is_refused_with_a_clear_error() -> None:
    with pytest.raises(UnsupportedSchemaVersion, match="not supported"):
        parse_event({"type": "delta", "text": "hi", "schema_version": "99"})


def test_wire_round_trip() -> None:
    event = End(status="fallback", output={"text": "x"})
    assert parse_event(event.model_dump(mode="json")) == event
