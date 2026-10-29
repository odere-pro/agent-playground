"""`CassetteTransport`: record then replay gives the same bytes, a miss raises and is counted, and
nothing but `content-type` reaches the disk.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Awaitable, Callable, MutableMapping
from pathlib import Path
from typing import Any

import httpx
import pytest
import yaml
from chassis_contracts.recording import KEY_SHAPED as KEY_SHAPED_PATTERN
from chassis_contracts.recording import (
    CassetteMiss,
    CassetteSecretError,
    CassetteTransport,
    default_vcr_config,
)

KEY = "cassette-key-not-real"
BASE = "http://model.invalid/v1"

Scope = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[MutableMapping[str, Any]]]
Send = Callable[[MutableMapping[str, Any]], Awaitable[None]]

SSE = b'data: {"n": 1}\n\ndata: {"n": 2}\n\ndata: [DONE]\n\n'


class Upstream:
    """A tiny ASGI model: JSON for a plain body, three SSE frames when `stream` is set. Counts
    calls, and answers with headers that must never be stored.
    """

    def __init__(self) -> None:
        self.calls = 0

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        assert scope["type"] == "http"
        body = b""
        while True:
            message = await receive()
            body += message.get("body", b"")
            if not message.get("more_body"):
                break
        self.calls += 1
        data = json.loads(body)
        streamed = bool(data.get("stream"))
        payload = SSE if streamed else json.dumps({"echo": data, "n": self.calls}).encode()
        content_type = b"text/event-stream" if streamed else b"application/json"
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [
                    (b"content-type", content_type),
                    (b"set-cookie", b"session=secret"),
                    (b"x-request-id", b"abc"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": payload})


def _client(transport: httpx.AsyncBaseTransport) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=BASE,
        transport=transport,
        headers={"Authorization": f"Bearer {KEY}", "x-api-key": KEY, "x-custom": "drop-me"},
    )


async def _post(transport: httpx.AsyncBaseTransport, body: dict[str, Any]) -> httpx.Response:
    async with _client(transport) as client:
        return await client.post("/chat/completions", json=body)


async def _stream(transport: httpx.AsyncBaseTransport, body: dict[str, Any]) -> list[bytes]:
    async with (
        _client(transport) as client,
        client.stream("POST", "/chat/completions", json=body) as response,
    ):
        assert response.status_code == 200
        return [chunk async for chunk in response.aiter_raw()]


async def _record(path: Path, *bodies: dict[str, Any]) -> Upstream:
    upstream = Upstream()
    transport = CassetteTransport(httpx.ASGITransport(app=upstream), path, "rewrite")
    for body in bodies:
        if body.get("stream"):
            await _stream(transport, body)
        else:
            await _post(transport, body)
    await transport.aclose()
    return upstream


async def test_complete_record_then_replay_gives_the_same_bytes(tmp_path: Path) -> None:
    path = tmp_path / "c.yaml"
    upstream = Upstream()
    recorder = CassetteTransport(httpx.ASGITransport(app=upstream), path, "rewrite")
    recorded = await _post(recorder, {"q": "hi"})
    await recorder.aclose()

    replayer = CassetteTransport(None, path, "none")
    replayed = await _post(replayer, {"q": "hi"})

    assert upstream.calls == 1, "replay must never reach the upstream"
    assert replayed.status_code == recorded.status_code == 200
    assert replayed.content == recorded.content
    assert replayed.headers["content-type"] == "application/json"
    assert replayer.misses == []
    replayer.assert_no_misses()


async def test_streamed_record_then_replay_gives_the_same_bytes_frame_by_frame(
    tmp_path: Path,
) -> None:
    path = tmp_path / "s.yaml"
    upstream = Upstream()
    recorder = CassetteTransport(httpx.ASGITransport(app=upstream), path, "rewrite")
    recorded = await _stream(recorder, {"q": "hi", "stream": True})
    await recorder.aclose()

    replayer = CassetteTransport(None, path, "none")
    replayed = await _stream(replayer, {"q": "hi", "stream": True})

    assert b"".join(recorded) == b"".join(replayed) == SSE
    assert replayed == [b'data: {"n": 1}\n\n', b'data: {"n": 2}\n\n', b"data: [DONE]\n\n"]
    assert upstream.calls == 1


class _Frames(httpx.AsyncByteStream):
    async def __aiter__(self) -> AsyncIterator[bytes]:
        for frame in (b'data: {"n": 1}\n\n', b'data: {"n": 2}\n\n', b"data: [DONE]\n\n"):
            yield frame


class FrameByFrame(httpx.AsyncBaseTransport):
    """An upstream that streams SSE one frame per chunk, as a real HTTP transport does.
    `ASGITransport` hands the whole body out in one chunk.
    """

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, headers={"content-type": "text/event-stream"}, stream=_Frames(), request=request
        )


class Tripwire(httpx.AsyncBaseTransport):
    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        raise AssertionError("replay called the inner transport")


async def test_record_passes_a_stream_through_chunk_by_chunk(tmp_path: Path) -> None:
    recorder = CassetteTransport(FrameByFrame(), tmp_path / "c.yaml", "rewrite")
    assert await _stream(recorder, {"stream": True}) == [
        b'data: {"n": 1}\n\n',
        b'data: {"n": 2}\n\n',
        b"data: [DONE]\n\n",
    ]


async def test_replay_never_calls_inner_even_on_a_miss(tmp_path: Path) -> None:
    path = tmp_path / "c.yaml"
    await _record(path, {"q": "hi"})
    replayer = CassetteTransport(Tripwire(), path, "none")
    assert (await _post(replayer, {"q": "hi"})).status_code == 200
    with pytest.raises(CassetteMiss):
        await _post(replayer, {"q": "not recorded"})
    assert len(replayer.misses) == 1


async def test_a_stream_closed_early_is_still_recorded_whole(tmp_path: Path) -> None:
    path = tmp_path / "early.yaml"
    recorder = CassetteTransport(FrameByFrame(), path, "rewrite")
    async with (
        _client(recorder) as client,
        client.stream("POST", "/chat/completions", json={"stream": True}) as response,
    ):
        async for _ in response.aiter_raw():
            break
    await recorder.aclose()

    replayed = await _stream(CassetteTransport(None, path, "none"), {"stream": True})
    assert b"".join(replayed) == SSE


async def test_a_miss_raises_names_the_body_and_is_counted(tmp_path: Path) -> None:
    path = tmp_path / "c.yaml"
    await _record(path, {"q": "recorded"})
    replayer = CassetteTransport(None, path, "none")

    with pytest.raises(CassetteMiss) as exc:
        await _post(replayer, {"q": "never recorded"})
    assert isinstance(exc.value, httpx.TransportError)
    assert "never recorded" in str(exc.value)
    assert "body" in str(exc.value), "the message names the matcher that failed"

    with pytest.raises(CassetteMiss):
        await _post(replayer, {"q": "also new"})
    assert len(replayer.misses) == 2
    with pytest.raises(AssertionError, match="2 cassette miss"):
        replayer.assert_no_misses()


async def test_a_changed_body_is_a_miss(tmp_path: Path) -> None:
    path = tmp_path / "c.yaml"
    await _record(path, {"messages": [{"role": "user", "content": "hello"}]})
    replayer = CassetteTransport(None, path, "none")

    same = await _post(replayer, {"messages": [{"role": "user", "content": "hello"}]})
    assert same.status_code == 200
    with pytest.raises(CassetteMiss):
        await _post(replayer, {"messages": [{"role": "user", "content": "hello!"}]})
    assert len(replayer.misses) == 1


async def test_a_missing_cassette_is_a_miss_that_says_how_to_record(tmp_path: Path) -> None:
    replayer = CassetteTransport(None, tmp_path / "absent.yaml", "none")
    with pytest.raises(CassetteMiss, match="--record-mode=rewrite"):
        await _post(replayer, {"q": "hi"})
    replayer.save()
    assert not (tmp_path / "absent.yaml").exists(), "replay never writes"


async def test_nothing_but_content_type_is_stored(tmp_path: Path) -> None:
    path = tmp_path / "c.yaml"
    await _record(path, {"q": "hi"}, {"q": "hi", "stream": True})
    text = path.read_text()
    data = yaml.safe_load(text)

    assert len(data["interactions"]) == 2
    for interaction in data["interactions"]:
        assert set(interaction["request"]["headers"]) == {"content-type"}
        assert set(interaction["response"]["headers"]) == {"content-type"}
    for needle in (KEY, "authorization", "x-api-key", "bearer", "set-cookie", "x-custom"):
        assert needle not in text.lower()


async def test_rewrite_replaces_the_file_and_stores_each_request_once(tmp_path: Path) -> None:
    path = tmp_path / "c.yaml"
    await _record(path, {"q": "old"})
    await _record(path, {"q": "new"}, {"q": "new"})
    data = yaml.safe_load(path.read_text())
    bodies = [json.loads(i["request"]["body"]) for i in data["interactions"]]
    assert bodies == [{"q": "new"}]


async def test_replay_can_repeat_and_sees_every_request(tmp_path: Path) -> None:
    path = tmp_path / "c.yaml"
    await _record(path, {"q": "hi"})
    replayer = CassetteTransport(None, path, "none")
    first = await _post(replayer, {"q": "hi"})
    second = await _post(replayer, {"q": "hi"})
    assert first.content == second.content
    assert [json.loads(r.content) for r in replayer.requests] == [{"q": "hi"}, {"q": "hi"}]


def test_a_recording_mode_needs_an_inner_transport(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="inner"):
        CassetteTransport(None, tmp_path / "c.yaml", "rewrite")
    with pytest.raises(ValueError, match="record_mode"):
        CassetteTransport(None, tmp_path / "c.yaml", "sometimes")


# --- Keys never reach the disk or a miss message (R1-27) ---

FAKE_SK = "sk-abcdefghijklmnop"
KEY_SHAPED = {
    "openai-style": FAKE_SK,
    "aws-access-key": "AKIA" + "ABCDEFGHIJKLMNOP",
    "github-token": "ghp_" + "abcdefghijklmnopqrstuvwx",
    "anthropic-style": "sk-ant-api03-" + "ABCDEFGHIJKLMNOP1234567890",
    "openai-project": "sk-proj-" + "ABCDEFGHIJKLMNOP1234567890",
    "google-api-key": "AIza" + "SyABCDEFGHIJKLMNOPQRSTUVWXYZ0123456",
    "slack-token": "xoxb-" + "1234567890-abcdefghij",
    "google-oauth": "ya29." + "a0ABCDEFGHIJKLMNOPQRSTUV",
}
KEY_NAMES = ("key", "api_key", "apikey", "access_token", "token")


async def _record_url(path: Path, url: str, body: dict[str, Any]) -> None:
    transport = CassetteTransport(httpx.ASGITransport(app=Upstream()), path, "rewrite")
    async with httpx.AsyncClient(transport=transport) as client:
        await client.post(url, json=body)
    await transport.aclose()


async def test_save_drops_the_key_when_the_spec_url_carries_one(tmp_path: Path) -> None:
    """A request to `https://x/v1?key=sk-...` is saved with no `sk-` string in the file."""
    path = tmp_path / "c.yaml"

    await _record_url(path, f"https://x/v1?key={FAKE_SK}", {"q": "hi"})

    assert "sk-" not in path.read_text()


@pytest.mark.parametrize("name", KEY_NAMES, ids=KEY_NAMES)
async def test_save_drops_a_query_parameter_when_its_name_is_key_like(
    tmp_path: Path, name: str
) -> None:
    """A key-like query parameter is not stored; the others are."""
    path = tmp_path / "c.yaml"

    await _record_url(path, f"https://x/v1?{name}={FAKE_SK}&page=2", {"q": "hi"})

    (interaction,) = yaml.safe_load(path.read_text())["interactions"]
    assert interaction["request"]["uri"] == "https://x/v1?page=2"


BODIES = {
    "top-level": {"q": "hi", "api_key": FAKE_SK},
    "nested": {"q": "hi", "auth": {"access_token": FAKE_SK}},
    "in-a-list": {"q": "hi", "tools": [{"token": FAKE_SK}]},
}


@pytest.mark.parametrize("body", BODIES.values(), ids=BODIES.keys())
async def test_save_scrubs_a_body_field_when_its_name_is_key_like(
    tmp_path: Path, body: dict[str, Any]
) -> None:
    """A key-like JSON body field is stored as `[redacted]` in the request, at any depth."""
    path = tmp_path / "c.yaml"

    await _record_url(path, "https://x/v1", body)

    (interaction,) = yaml.safe_load(path.read_text())["interactions"]
    stored = interaction["request"]["body"]
    assert FAKE_SK not in stored
    assert "[redacted]" in stored


@pytest.mark.parametrize("secret", KEY_SHAPED.values(), ids=KEY_SHAPED.keys())
async def test_save_raises_when_a_key_shaped_string_survives_the_scrub(
    tmp_path: Path, secret: str
) -> None:
    """A key-shaped string in a field the scrub does not know stops the save; no file is written."""
    path = tmp_path / "c.yaml"

    with pytest.raises(CassetteSecretError, match="key-shaped"):
        await _record_url(path, "https://x/v1", {"prompt": f"use {secret} please"})

    assert not path.exists()


async def test_replay_matches_when_only_the_scrubbed_key_differs(tmp_path: Path) -> None:
    """The scrub runs on replay too, so a request with another key still matches."""
    path = tmp_path / "c.yaml"
    await _record_url(path, f"https://x/v1?key={FAKE_SK}", {"q": "hi", "api_key": FAKE_SK})
    replayer = CassetteTransport(None, path, "none")

    async with httpx.AsyncClient(transport=replayer) as client:
        response = await client.post("https://x/v1?key=other", json={"q": "hi", "api_key": "x"})

    assert response.status_code == 200
    replayer.assert_no_misses()


async def test_a_miss_message_redacts_the_query_and_key_shaped_strings(tmp_path: Path) -> None:
    """A miss names the path and the body, but not the query string or a key-shaped value."""
    path = tmp_path / "c.yaml"
    await _record_url(path, "https://x/v1", {"q": "recorded"})
    replayer = CassetteTransport(None, path, "none")

    with pytest.raises(CassetteMiss) as exc:
        async with httpx.AsyncClient(transport=replayer) as client:
            await client.post(f"https://x/v1?key={FAKE_SK}", json={"prompt": f"use {FAKE_SK}"})

    message = str(exc.value)
    assert "sk-" not in message
    assert "key=" not in message
    assert "https://x/v1?[redacted]" in message
    assert "use [redacted]" in message


# --- Response bodies, name variants, and miss URLs (security review of 051b9c5) ---

OPAQUE = "opaque-value-123"
"""A secret that is not key-shaped, so only the field name can catch it."""


class FixedReply(httpx.AsyncBaseTransport):
    """An upstream that answers every request with the same JSON body."""

    def __init__(self, body: bytes) -> None:
        self._body = body

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, headers={"content-type": "application/json"}, content=self._body, request=request
        )


async def _record_reply(
    path: Path, reply: bytes, request_body: bytes = b'{"q":"hi"}', **kwargs: Any
) -> None:
    transport = CassetteTransport(FixedReply(reply), path, "rewrite", **kwargs)
    async with httpx.AsyncClient(transport=transport) as client:
        await client.post(
            "https://x/v1", content=request_body, headers={"content-type": "application/json"}
        )
    await transport.aclose()


async def test_save_redacts_a_response_when_it_echoes_a_key_shaped_string(tmp_path: Path) -> None:
    """A 200 response that quotes a key is stored with the key replaced by `[redacted]`."""
    path = tmp_path / "c.yaml"
    reply = json.dumps(
        {"error": {"message": "Incorrect API key provided: sk-" + "ABCDEFGH1234567890"}}
    ).encode()

    await _record_reply(path, reply)

    text = path.read_text()
    assert "sk-" not in text
    assert "Incorrect API key provided: [redacted]" in text


async def test_save_scrubs_a_response_body_field_when_its_name_is_key_like(tmp_path: Path) -> None:
    """A key-like field in a JSON response is stored as `[redacted]`; the others are kept."""
    path = tmp_path / "c.yaml"

    await _record_reply(path, json.dumps({"access_token": OPAQUE, "n": 1}).encode())

    (interaction,) = yaml.safe_load(path.read_text())["interactions"]
    stored = json.loads(interaction["response"]["body"]["string"])
    assert stored == {"access_token": "[redacted]", "n": 1}


async def test_save_raises_when_a_response_still_holds_a_key_shaped_string(tmp_path: Path) -> None:
    """With no response scrub configured, a key-shaped response body stops the save."""
    path = tmp_path / "c.yaml"
    config = {**default_vcr_config(), "before_record_response": None}

    with pytest.raises(CassetteSecretError, match="response 0"):
        await _record_reply(path, b'{"echo":"' + FAKE_SK.encode() + b'"}', vcr_config=config)

    assert not path.exists()


NAME_VARIANTS = (
    "API_KEY",
    "api-key",
    "apiKey",
    "authorization",
    "client_secret",
    "password",
    "refresh_token",
    "secret",
    "x-api-key",
)


@pytest.mark.parametrize("name", NAME_VARIANTS, ids=NAME_VARIANTS)
async def test_save_scrubs_a_body_field_when_its_name_is_a_key_name_variant(
    tmp_path: Path, name: str
) -> None:
    """A body field named like a key in any case, with `-` or `_`, is stored as `[redacted]`."""
    path = tmp_path / "c.yaml"

    await _record_url(path, "https://x/v1", {"q": "hi", name: OPAQUE})

    assert OPAQUE not in path.read_text()


@pytest.mark.parametrize("name", ("API_KEY", "apiKey", "api-key"), ids=("upper", "camel", "dash"))
async def test_save_drops_a_query_parameter_when_its_name_is_a_key_name_variant(
    tmp_path: Path, name: str
) -> None:
    """A query parameter named like a key in any case, with `-` or `_`, is not stored."""
    path = tmp_path / "c.yaml"

    await _record_url(path, f"https://x/v1?{name}={OPAQUE}&page=2", {"q": "hi"})

    (interaction,) = yaml.safe_load(path.read_text())["interactions"]
    assert interaction["request"]["uri"] == "https://x/v1?page=2"


async def test_a_miss_message_drops_userinfo_and_fragment_when_the_url_has_them(
    tmp_path: Path,
) -> None:
    """A miss URL shows no userinfo, no fragment, and no key-shaped string in the path."""
    path = tmp_path / "c.yaml"
    await _record_url(path, "https://x/v1", {"q": "recorded"})
    replayer = CassetteTransport(None, path, "none")

    with pytest.raises(CassetteMiss) as exc:
        async with httpx.AsyncClient(transport=replayer) as client:
            await client.post(f"https://user:hunter2@x/v1/{FAKE_SK}#frag", json={"q": "new"})

    message = str(exc.value)
    assert "https://x/v1/[redacted] is not in" in message
    assert "hunter2" not in message
    assert "#frag" not in message


async def test_save_keeps_a_body_opaque_when_it_nests_too_deep_to_parse(tmp_path: Path) -> None:
    """A JSON body nested past the recursion limit is stored as it is, not a crash."""
    path = tmp_path / "c.yaml"
    deep = b"[" * 100_000 + b"]" * 100_000

    await _record_reply(path, b'{"n":1}', request_body=deep)

    (interaction,) = yaml.safe_load(path.read_text())["interactions"]
    assert len(interaction["request"]["body"]) == len(deep)


@pytest.mark.parametrize(
    "text", ["task-oriented work", "a risk-assessment note"], ids=["task", "risk"]
)
def test_key_shape_ignores_sk_when_it_ends_a_longer_word(text: str) -> None:
    """`sk-` inside a word such as `task-oriented` is not a key."""
    match = KEY_SHAPED_PATTERN.search(text)

    assert match is None
