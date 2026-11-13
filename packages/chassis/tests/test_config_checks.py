"""H-12 (010): the load check refuses a wrong `risk_class` and a credential-shaped value.

Every credential literal here is a made-up value with a credential's shape, never a real one.
"""

from __future__ import annotations

import copy
from typing import Any

import pytest
from chassis.server.config import ChassisConfig, load_config
from chassis.server.config_loader import ConfigRejected, parse_document
from pydantic import ValidationError

DOC: dict[str, Any] = {
    "version": "cfg-1",
    "profile": "fake",
    "agent": {"name": "echo", "version": "0.0.1"},
    "spec": {
        "engine": {"connector": "inprocess", "handle": "chassis.core.handle:echo_wire"},
        "model": {"route": "fake-route"},
        "prompt": {"version": "p1"},
    },
}

FAKE_KEY = "sk-" + "madeupkey0123456789"
FAKE_BEARER = "Bearer " + "madeup.token-0123456789"
FAKE_URL = "https://user:" + "madeuppass1" + "@store.example/bucket"


def _doc(**spec: Any) -> dict[str, Any]:
    doc = copy.deepcopy(DOC)
    doc["spec"].update(spec)
    return doc


@pytest.mark.parametrize("value", ["low", "unacceptable", "HIGH", ""])
def test_a_wrong_risk_class_fails_the_check(value: str) -> None:
    with pytest.raises(ValidationError) as caught:
        load_config(_doc(governance={"risk_class": value}))
    assert "risk_class" in str(caught.value)


@pytest.mark.parametrize("value", ["minimal", "limited", "high"])
def test_each_allowed_risk_class_passes(value: str) -> None:
    config = load_config(_doc(governance={"risk_class": value}))
    assert config.spec.governance.risk_class == value


@pytest.mark.parametrize(
    ("spec", "path"),
    [
        ({"model": {"route": FAKE_KEY}}, "spec.model.route"),
        ({"prompt": {"version": FAKE_BEARER}}, "spec.prompt.version"),
        (
            {"model": {"route": "fake-route", "routes": {"chat": FAKE_KEY}}},
            "spec.model.routes.chat",
        ),
    ],
)
def test_a_credential_shaped_value_is_refused_and_named_by_path(
    spec: dict[str, Any], path: str
) -> None:
    with pytest.raises(ValidationError) as caught:
        load_config(_doc(**spec))
    message = str(caught.value)
    assert path in message
    assert "madeup" not in message


def test_a_password_in_a_url_is_refused_and_not_echoed() -> None:
    doc = copy.deepcopy(DOC)
    doc["agent"]["name"] = FAKE_URL
    with pytest.raises(ValidationError) as caught:
        ChassisConfig.model_validate(doc)
    assert "agent.name" in str(caught.value)
    assert "madeuppass1" not in str(caught.value)


def test_a_credential_in_the_version_is_refused_too() -> None:
    doc = copy.deepcopy(DOC)
    doc["version"] = FAKE_KEY
    with pytest.raises(ValidationError) as caught:
        ChassisConfig.model_validate(doc)
    assert "version" in str(caught.value)
    assert "madeup" not in str(caught.value)


def test_a_store_document_with_a_credential_is_rejected_by_path_only() -> None:
    with pytest.raises(ConfigRejected) as caught:
        parse_document(_doc(model={"route": FAKE_KEY}))
    assert "spec.model.route" in str(caught.value)
    assert "madeup" not in str(caught.value)


def test_a_store_document_with_ordinary_words_is_not_refused() -> None:
    doc = _doc(model={"route": "token-counter"}, prompt={"version": "bearer-auth-v2"})
    config = parse_document(doc)
    assert config.spec.model.route == "token-counter"
    assert config.spec.prompt.version == "bearer-auth-v2"


def test_ordinary_words_and_env_names_are_not_credentials() -> None:
    doc = copy.deepcopy(DOC)
    doc["agent"]["name"] = "token-counter"
    doc["spec"]["prompt"]["version"] = "bearer-auth-v2"
    assert ChassisConfig.model_validate(doc).agent.name == "token-counter"
