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
from chassis_contracts.recording import CassetteMiss, CassetteTransport

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
