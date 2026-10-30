"""R4-09: `S3Config.load` refuses an object over the size cap, a YAML alias, and a document
too deep to compose, all as `ConfigUnavailable`. A fake `Minio` client; no network.
"""

from __future__ import annotations

from typing import Any

import pytest
import yaml
from chassis.adapters.s3.config import MAX_DEPTH, MAX_DOCUMENT_BYTES, S3Config
from chassis.ports.config import ConfigUnavailable


class _Response:
    def __init__(self, body: bytes) -> None:
        self.body = body
        self.reads: list[int | None] = []
        self.headers = {"ETag": '"etag-1"'}

    def read(self, amt: int | None = None) -> bytes:
        self.reads.append(amt)
        return self.body if amt is None else self.body[:amt]

    def close(self) -> None:
        pass

    def release_conn(self) -> None:
        pass


class _Client:
    def __init__(self, body: bytes) -> None:
        self.response = _Response(body)

    def get_object(self, bucket: str, key: str) -> _Response:
        return self.response


def _port(body: bytes) -> tuple[S3Config, _Client]:
    client = _Client(body)
    return S3Config(client), client  # type: ignore[arg-type]  # a fake of the two calls used


def _never_parse(*args: Any, **kwargs: Any) -> Any:
    raise AssertionError("the document was parsed")


ALIAS_BOMB = b"""\
a: &a ["x", "x", "x", "x", "x", "x", "x", "x", "x"]
b: &b [*a, *a, *a, *a, *a, *a, *a, *a, *a]
c: &c [*b, *b, *b, *b, *b, *b, *b, *b, *b]
d: [*c, *c, *c, *c, *c, *c, *c, *c, *c]
"""


async def test_s3_config_load_raises_unavailable_when_the_object_is_over_the_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An object past the cap is refused before YAML sees it; at most cap + 1 bytes are read."""
    # Arrange
    port, client = _port(b"a: 1\n" + b"#" * MAX_DOCUMENT_BYTES)
    monkeypatch.setattr(yaml, "safe_load", _never_parse)
    # Act / Assert
    with pytest.raises(ConfigUnavailable, match="larger than"):
        await port.load("echo")
    assert client.response.reads == [MAX_DOCUMENT_BYTES + 1]


async def test_s3_config_load_raises_unavailable_when_the_document_has_an_alias(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An alias (the billion-laughs shape) is refused before the document is constructed."""
    # Arrange
    port, _ = _port(ALIAS_BOMB)
    monkeypatch.setattr(yaml, "safe_load", _never_parse)
    # Act / Assert
    with pytest.raises(ConfigUnavailable, match="alias"):
        await port.load("echo")


def _nested(depth: int) -> bytes:
    """A mapping (depth 1) holding `depth - 1` nested flow sequences."""
    return b"a: " + b"[" * (depth - 1) + b"]" * (depth - 1) + b"\n"


async def test_s3_config_load_raises_unavailable_when_the_document_nests_too_deep(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Deep nesting (which would recurse, and parse in quadratic time) is refused before it is
    built."""
    # Arrange
    port, _ = _port(_nested(20_000))
    monkeypatch.setattr(yaml, "safe_load", _never_parse)
    # Act / Assert
    with pytest.raises(ConfigUnavailable, match=f"deeper than {MAX_DEPTH}"):
        await port.load("echo")


async def test_s3_config_load_returns_the_mapping_when_the_document_is_at_the_depth_cap() -> None:
    """`MAX_DEPTH` itself is allowed."""
    # Arrange
    port, _ = _port(_nested(MAX_DEPTH))
    # Act
    loaded = await port.load("echo")
    # Assert
    assert "a" in loaded.data


async def test_s3_config_load_returns_the_mapping_when_the_document_is_plain_and_small() -> None:
    """The guards leave a normal document alone: anchors without aliases are fine too."""
    # Arrange
    port, _ = _port(b"version: cfg-1\nagent: &agent {name: echo}\n")
    # Act
    loaded = await port.load("echo")
    # Assert
    assert loaded.data == {"version": "cfg-1", "agent": {"name": "echo"}}
    assert loaded.version == "etag-1"
