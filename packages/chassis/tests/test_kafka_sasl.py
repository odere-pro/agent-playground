"""`KafkaEvents` SASL settings from the environment (PoC-5 T25, H11; ADR-001 hard requirement 1).

Offline: aiokafka's producer and consumer are replaced by recorders, so no broker is needed. The
broker refusing a client without the credential is the kind case in
`pocs/poc-05-sandboxed/tests/test_poc05_kind_hardreq1.py`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

import pytest
from chassis.adapters.kafka import KafkaEvents
from chassis.adapters.kafka import events as kafka_events
from chassis_contracts.events import make_event

PASSWORD = "s3cret-not-in-any-log"  # pragma: allowlist secret (a test value)
SASL_VARS = (
    "KAFKA_SECURITY_PROTOCOL",
    "KAFKA_SASL_MECHANISM",
    "KAFKA_SASL_USERNAME",
    "KAFKA_SASL_PASSWORD",
    "KAFKA_SASL_PASSWORD_FILE",
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in SASL_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("KAFKA_BOOTSTRAP_SERVERS", "kafka:9092")


class _Recorder:
    """Stands in for `AIOKafkaProducer` and `AIOKafkaConsumer`: records its keyword arguments."""

    made: ClassVar[list[dict[str, Any]]] = []

    def __init__(self, *topics: str, **kwargs: Any) -> None:
        type(self).made.append(kwargs)

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None

    async def send_and_wait(self, topic: str, **kwargs: Any) -> None:
        return None

    def __aiter__(self) -> _Recorder:
        return self

    async def __anext__(self) -> Any:
        raise StopAsyncIteration


@pytest.fixture
def recorder(monkeypatch: pytest.MonkeyPatch) -> type[_Recorder]:
    _Recorder.made = []
    monkeypatch.setattr(kafka_events, "AIOKafkaProducer", _Recorder)
    monkeypatch.setattr(kafka_events, "AIOKafkaConsumer", _Recorder)
    return _Recorder


def _sasl_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    secret = tmp_path / "password"
    secret.write_text(PASSWORD + "\n")
    monkeypatch.setenv("KAFKA_SECURITY_PROTOCOL", "SASL_PLAINTEXT")
    monkeypatch.setenv("KAFKA_SASL_MECHANISM", "SCRAM-SHA-512")
    monkeypatch.setenv("KAFKA_SASL_USERNAME", "chassis")
    monkeypatch.setenv("KAFKA_SASL_PASSWORD_FILE", str(secret))
    return secret


async def test_sasl_settings_reach_the_producer_and_the_consumer(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, recorder: type[_Recorder]
) -> None:
    _sasl_env(monkeypatch, tmp_path)
    port = KafkaEvents.from_env()

    await port.publish("t.v1", make_event("t.v1"))

    async def handler(event: Any) -> None:
        return None

    sub = await port.subscribe("t.v1", handler, group="g")
    await sub.close()
    await port.aclose()

    want = {
        "security_protocol": "SASL_PLAINTEXT",
        "sasl_mechanism": "SCRAM-SHA-512",
        "sasl_plain_username": "chassis",
        "sasl_plain_password": PASSWORD,
    }
    assert len(recorder.made) == 2
    for kwargs in recorder.made:
        assert {k: kwargs.get(k) for k in want} == want


def test_the_password_may_come_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KAFKA_SECURITY_PROTOCOL", "SASL_PLAINTEXT")
    monkeypatch.setenv("KAFKA_SASL_MECHANISM", "SCRAM-SHA-256")
    monkeypatch.setenv("KAFKA_SASL_USERNAME", "chassis")
    monkeypatch.setenv("KAFKA_SASL_PASSWORD", PASSWORD)
    port = KafkaEvents.from_env()
    assert port.sasl is not None
    assert (port.sasl.mechanism, port.sasl.username, port.sasl.password) == (
        "SCRAM-SHA-256",
        "chassis",
        PASSWORD,
    )


def test_the_mechanism_defaults_to_scram_sha_512(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _sasl_env(monkeypatch, tmp_path)
    monkeypatch.delenv("KAFKA_SASL_MECHANISM")
    port = KafkaEvents.from_env()
    assert port.sasl is not None and port.sasl.mechanism == "SCRAM-SHA-512"


def test_repr_and_str_never_show_the_password(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _sasl_env(monkeypatch, tmp_path)
    port = KafkaEvents.from_env()
    assert port.sasl is not None
    for text in (repr(port), str(port), repr(port.sasl), str(port.sasl), repr(vars(port))):
        assert PASSWORD not in text
    assert "chassis" in repr(port)


@pytest.mark.parametrize("missing", ["KAFKA_SASL_USERNAME", "KAFKA_SASL_PASSWORD_FILE"])
def test_sasl_without_a_credential_fails_fast(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, missing: str
) -> None:
    _sasl_env(monkeypatch, tmp_path)
    monkeypatch.delenv(missing)
    with pytest.raises(LookupError, match="KAFKA_SASL_") as info:
        KafkaEvents.from_env()
    assert PASSWORD not in str(info.value)


def test_an_empty_password_file_fails_fast(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _sasl_env(monkeypatch, tmp_path).write_text("\n")
    with pytest.raises(LookupError, match="KAFKA_SASL_PASSWORD_FILE"):
        KafkaEvents.from_env()


def test_a_missing_password_file_fails_fast(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _sasl_env(monkeypatch, tmp_path)
    monkeypatch.setenv("KAFKA_SASL_PASSWORD_FILE", str(tmp_path / "absent"))
    with pytest.raises(LookupError, match="KAFKA_SASL_PASSWORD_FILE"):
        KafkaEvents.from_env()


def test_both_password_forms_are_refused(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _sasl_env(monkeypatch, tmp_path)
    monkeypatch.setenv("KAFKA_SASL_PASSWORD", PASSWORD)
    with pytest.raises(ValueError, match="one of") as info:
        KafkaEvents.from_env()
    assert PASSWORD not in str(info.value)


def test_an_unknown_mechanism_is_refused(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _sasl_env(monkeypatch, tmp_path)
    monkeypatch.setenv("KAFKA_SASL_MECHANISM", "PLAIN")
    with pytest.raises(ValueError, match="KAFKA_SASL_MECHANISM"):
        KafkaEvents.from_env()


def test_sasl_credentials_with_a_plaintext_protocol_are_refused(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _sasl_env(monkeypatch, tmp_path)
    monkeypatch.setenv("KAFKA_SECURITY_PROTOCOL", "PLAINTEXT")
    with pytest.raises(ValueError, match="KAFKA_SECURITY_PROTOCOL"):
        KafkaEvents.from_env()


def test_plaintext_stays_the_default_with_no_sasl(recorder: type[_Recorder]) -> None:
    port = KafkaEvents.from_env()
    assert (port.security_protocol, port.sasl) == ("PLAINTEXT", None)
