"""`LiteLLMModel` bound to `ModelPortContract` over a recorded cassette, so the contract runs with
no live model and no fake server.

Replay is the default: with no `--record-mode` flag, pytest-recording's `record_mode` fixture is
`none`, and the gate (`scripts/check_offline.sh`) passes `--record-mode=none` explicitly. Replay
builds the transport with no inner transport at all, so a request the cassette lacks can only
fail. `make record-cassettes` re-records offline with `--record-mode=rewrite`: the inner
transport is then the fake model server over ASGI, with no socket.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from chassis.adapters.litellm import LiteLLMModel
from chassis.ports.model import ModelError, ModelMessage, ToolCallRequest, ToolSpec
from chassis_contracts import ModelPortContract
from chassis_contracts.model import ReceivedMessages, ToolCallCase
from chassis_contracts.recording import CassetteTransport, request_json
from fake_model_server import Script, create_app

ROOT = Path(__file__).resolve().parents[3]
EXAMPLE_SCRIPT = ROOT / "packages/fake-model-server/scripts/example.yaml"
CASSETTES = Path(__file__).parent / "cassettes"
BASE_URL = "http://model.invalid/v1"
# suggested: a key that is obviously not one; the cassette scan proves it never reaches the disk.
TEST_KEY = "cassette-key-not-real"


def _from_openai(message: dict[str, Any]) -> ModelMessage:
    """Read back an OpenAI wire message. `function.arguments` must be a JSON string."""
    tool_calls = None
    if message.get("tool_calls") is not None:
        tool_calls = []
        for call in message["tool_calls"]:
            assert call["type"] == "function"
            assert isinstance(call["function"]["arguments"], str)
            tool_calls.append(
                ToolCallRequest(
                    call_id=call["id"],
                    name=call["function"]["name"],
                    arguments=json.loads(call["function"]["arguments"]),
                )
            )
    return ModelMessage(
        role=message["role"],
        content=message["content"],
        name=message.get("name"),
        tool_calls=tool_calls,
        tool_call_id=message.get("tool_call_id"),
    )


@pytest.fixture(scope="module")
def cassette(record_mode: str) -> Iterator[CassetteTransport]:
    """One transport over `cassettes/litellm_model.yaml` for the module, saved at teardown."""
    path = CASSETTES / "litellm_model.yaml"
    inner = None
    if record_mode == "none":
        assert path.is_file(), f"{path} is missing; run `make record-cassettes`"
    else:
        inner = httpx.ASGITransport(app=create_app(Script.from_yaml(EXAMPLE_SCRIPT)))
    transport = CassetteTransport(inner, path, record_mode)
    yield transport
    transport.save()


class TestRecordedLiteLLMModel(ModelPortContract):
    """The real adapter over `cassettes/litellm_model.yaml`, shared by the module."""

    @pytest.fixture
    def model_port(self, cassette: CassetteTransport) -> Iterator[LiteLLMModel]:
        before = len(cassette.misses)
        yield LiteLLMModel(BASE_URL, TEST_KEY, transport=cassette)
        assert cassette.misses[before:] == [], "a model call missed the cassette"

    @pytest.fixture
    def received_messages(self, cassette: CassetteTransport) -> ReceivedMessages:
        return lambda: [_from_openai(m) for m in request_json(cassette.requests[-1])["messages"]]

    @pytest.fixture
    def tool_call_case(self) -> ToolCallCase:
        return ToolCallCase(
            [ModelMessage(role="user", content="glossary SLM")],
            [ToolSpec(name="glossary_lookup")],
            "glossary_lookup",
        )

    @pytest.fixture
    def error_messages(self) -> list[ModelMessage]:
        return [ModelMessage(role="user", content="fail")]


async def test_replay_never_reaches_a_model(tmp_path: Path) -> None:
    """A request the cassette lacks fails as a `ModelError` and is counted as a miss; the replay
    transport has no inner transport to fall through to.
    """
    cassette = CassetteTransport(None, CASSETTES / "litellm_model.yaml", "none")
    model = LiteLLMModel(BASE_URL, TEST_KEY, transport=cassette)
    with pytest.raises(ModelError) as exc:
        await model.complete([ModelMessage(role="user", content="not recorded")], route="r")
    assert exc.value.code == "transport_error"
    assert len(cassette.misses) == 1
    assert "not recorded" in str(cassette.misses[0])


_FORBIDDEN = re.compile(r"authorization|x-api-key|bearer|sk-|" + re.escape(TEST_KEY), re.IGNORECASE)


def _cassette_files() -> list[Path]:
    """Every `cassettes/**/*.yaml` under the repo, without walking `.venv` and the like."""
    skip = {".venv", "node_modules", ".git", ".mypy_cache", ".ruff_cache", ".pytest_cache"}
    found: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in skip]
        here = Path(dirpath)
        if "cassettes" in here.relative_to(ROOT).parts:
            found.extend(here / f for f in filenames if f.endswith((".yaml", ".yml")))
    return sorted(found)


def test_cassettes_hold_no_key() -> None:
    """No cassette in the repo holds an auth header, a bearer token, a key shape, or the test
    key.
    """
    files = _cassette_files()
    assert CASSETTES / "litellm_model.yaml" in files, "the scan must see the recorded cassette"
    found = {
        f"{path.relative_to(ROOT)}:{n}: {m.group(0)}"
        for path in files
        for n, line in enumerate(path.read_text().splitlines(), start=1)
        for m in [_FORBIDDEN.search(line)]
        if m
    }
    assert not found, f"key material in cassettes: {sorted(found)}"
