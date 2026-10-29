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


@pytest.mark.parametrize(
    "data",
    [
        pytest.param(None, id="none"),
        pytest.param("end", id="str"),
        pytest.param(["type", "end"], id="list"),
        pytest.param(7, id="int"),
    ],
)
def test_parse_event_raises_type_error_when_data_is_not_an_object(data: object) -> None:
    """A non-object is refused as bad input with `TypeError`, never `AttributeError`."""
    with pytest.raises(TypeError, match="event must be a JSON object"):
        parse_event(data)  # type: ignore[arg-type]


def test_parse_event_cuts_the_version_in_its_error_when_the_version_is_long() -> None:
    """A long unknown `schema_version` is cut to 40 characters in the message."""
    long_version = "9" * 500

    with pytest.raises(UnsupportedSchemaVersion) as raised:
        parse_event({"type": "end", "schema_version": long_version})

    assert "9" * 41 not in str(raised.value)
