from __future__ import annotations

from typing import Any

import pytest
from chassis.core.envelope import Response, Versions
from pydantic import ValidationError

EPIC_EXAMPLE: dict[str, Any] = {
    "request_id": "uuid",
    "trace_id": "uuid",
    "idempotency_key": "uuid",
    "agent": "simplifier",
    "agent_version": "1.0.0",
    "output": {},
    "metrics": {},
    "status": "ok",
    "context_ref": None,
}


def test_epic_example_response_is_accepted() -> None:
    response = Response(**EPIC_EXAMPLE, versions=Versions(chassis="0.1.0"))
    assert response.status == "ok"
    assert response.versions.chassis == "0.1.0"


def test_unknown_status_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Response(**{**EPIC_EXAMPLE, "status": "maybe"}, versions=Versions(chassis="0.1.0"))


def test_versions_is_required() -> None:
    with pytest.raises(ValidationError):
        Response(**EPIC_EXAMPLE)
