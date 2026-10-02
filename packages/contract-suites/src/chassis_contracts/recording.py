"""Record and replay model calls: an httpx transport over a vcrpy cassette.

`CassetteTransport` sits on one client's transport, for example the `transport=` of
`LiteLLMModel`. It never uses vcrpy's global patch: that patch also wraps every other httpx
transport in the process, `ASGITransport` included, and reads each response to its end before it
returns it, so it would buffer the `sidecar` lane's SSE and the public SSE.

Record modes are pytest-recording's (`--record-mode`, its session fixture `record_mode`):

- `none` (the default when the flag is absent): replay only. `inner` is never called; it may be
  `None`. A request the cassette does not hold raises `CassetteMiss` and is counted in `misses`.
- `rewrite`: ignore what is on disk, send every request to `inner`, record it, and overwrite the
  file on `save()`.
- `once`, `new_episodes`, `all`: vcrpy's meaning.

A streamed response is recorded as it passes through (the caller still sees it chunk by chunk)
and is stored once it has been closed. On replay a `text/event-stream` body is handed out one SSE
frame per chunk.

Built on vcrpy's `VCR().get_merged_config`, `Cassette.load`, `can_play_response_for`,
`play_response`, `responses_of`, `append`, and `find_requests_with_most_matches`, plus the private
`Cassette._save`. Because of `_save`, vcrpy is pinned to 8.3.0 in this package's dependencies;
check `_save(force=...)` before you move the pin.
"""

from __future__ import annotations

import copy
import json
import re
from collections.abc import AsyncIterator, Callable, Mapping
from pathlib import Path
from typing import Any

import httpx
from vcr import VCR
from vcr.cassette import Cassette
from vcr.errors import UnhandledHTTPRequestError
from vcr.record_mode import RecordMode
from vcr.request import Request as VcrRequest

__all__ = [
    "KEPT_HEADERS",
    "RECORD_MODES",
    "CassetteMiss",
    "CassetteTransport",
    "default_vcr_config",
    "request_json",
]

RECORD_MODES = ("none", "once", "new_episodes", "all", "rewrite")
"""The modes pytest-recording's `--record-mode` accepts."""

KEPT_HEADERS = frozenset({"content-type"})
"""The only header stored on a request or a response. `content-type` lets the `body` matcher
compare JSON bodies as JSON."""

# suggested: 2000 characters of the unmatched body in a miss message; enough to see what changed.
MISS_BODY_CAP = 2000

_CASSETTE_KWARGS = (
    "serializer",
    "persister",
    "match_on",
    "before_record_request",
    "before_record_response",
    "allow_playback_repeats",
    "drop_unused_requests",
)


def _keep_request_headers(request: Any) -> Any:
    kept = copy.copy(request)
    kept.headers = {k: v for k, v in request.headers.items() if k.lower() in KEPT_HEADERS}
    return kept


def _keep_response_headers(response: dict[str, Any]) -> dict[str, Any]:
    headers = response.get("headers") or {}
    response["headers"] = {k: v for k, v in headers.items() if k.lower() in KEPT_HEADERS}
    return response


def default_vcr_config() -> dict[str, Any]:
    """The shared config: match on everything but headers, store only `content-type`, and drop
    auth headers as a second guard. Return it from a `vcr_config` fixture or pass it directly.
    """
    return {
        "match_on": ["method", "scheme", "host", "port", "path", "query", "body"],
        "allow_playback_repeats": True,
        "decode_compressed_response": True,
        "before_record_request": _keep_request_headers,
        "before_record_response": _keep_response_headers,
        "filter_headers": [
            "authorization",
            "x-api-key",
            "api-key",
            "cookie",
            "set-cookie",
            "x-litellm-api-key",
        ],
    }


class CassetteMiss(httpx.TransportError):
    """A request the cassette cannot answer in this record mode. The message names the body."""

    def __init__(self, path: Path, request: httpx.Request, body: bytes, nearest: str) -> None:
        text = body.decode(errors="replace")
        if len(text) > MISS_BODY_CAP:
            text = text[:MISS_BODY_CAP] + "...[truncated]"
        super().__init__(
            f"cassette miss: {request.method} {request.url} is not in {path}"
            f" ({nearest}). Unmatched body: {text}",
            request=request,
        )
        self.path = path
        self.body = body


def _nearest(cassette: Any, vcr_request: Any) -> str:
    if len(cassette) == 0:
        return "the cassette is empty or missing; record it with --record-mode=rewrite"
    best = cassette.find_requests_with_most_matches(vcr_request)
    if not best:
        return "no recorded request is close"
    _, _, failed = best[0]
    names = [name for name, _ in failed]
    if not names:
        return "the matching recording was already played and repeats are off"
    return f"nearest recording differs in: {', '.join(names)}"


def _sse_frames(body: bytes) -> list[bytes]:
    return [frame for frame in re.split(rb"(?<=\n\n)", body) if frame]


class _ReplayStream(httpx.AsyncByteStream):
    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = chunks

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for chunk in self._chunks:
            yield chunk


class _TeeStream(httpx.AsyncByteStream):
    """Passes `inner` through chunk by chunk and hands the whole body to `on_done` at close. A
    caller that stops early (a client that breaks on `[DONE]`) still gets the rest recorded.
    """

    def __init__(self, inner: httpx.AsyncByteStream, on_done: Callable[[bytes], None]) -> None:
        self._inner = inner
        self._chunks: list[bytes] = []
        self._iter: AsyncIterator[bytes] | None = None
        self._on_done: Callable[[bytes], None] | None = on_done

    async def __aiter__(self) -> AsyncIterator[bytes]:
        self._iter = self._inner.__aiter__()
        async for chunk in self._iter:
            self._chunks.append(chunk)
            yield chunk

    async def aclose(self) -> None:
        if self._on_done is None:
            return
        on_done, self._on_done = self._on_done, None
        try:
            if self._iter is None:
                self._iter = self._inner.__aiter__()
            async for chunk in self._iter:
                self._chunks.append(chunk)
        finally:
            await self._inner.aclose()
        on_done(b"".join(self._chunks))


class CassetteTransport(httpx.AsyncBaseTransport):
    """An `httpx.AsyncBaseTransport` that answers from a cassette at `path`, and records through
    `inner` when `record_mode` allows it.

    `requests` holds every request seen, in order, with its body read. `misses` holds every
    `CassetteMiss` raised; call `assert_no_misses()` at teardown, because an adapter may turn the
    transport error into its own error type. `aclose()` saves and leaves `inner` open, so one
    transport can outlive the clients that share it; call `save()` or `aclose()` when done.
    """

    def __init__(
        self,
        inner: httpx.AsyncBaseTransport | None,
        path: str | Path,
        record_mode: str = "none",
        vcr_config: Mapping[str, Any] | None = None,
    ) -> None:
        if record_mode not in RECORD_MODES:
            raise ValueError(f"record_mode {record_mode!r} is not one of {RECORD_MODES}")
        if inner is None and record_mode != "none":
            raise ValueError(f"record_mode {record_mode!r} records, so it needs an inner transport")
        self.path = Path(path)
        self.record_mode = record_mode
        self.requests: list[httpx.Request] = []
        self.misses: list[CassetteMiss] = []
        self._inner = inner
        config = dict(default_vcr_config() if vcr_config is None else vcr_config)
        vcr_mode = RecordMode.ALL if record_mode == "rewrite" else RecordMode(record_mode)
        merged = VCR().get_merged_config(path=str(self.path), record_mode=vcr_mode, **config)
        kwargs = {k: merged[k] for k in _CASSETTE_KWARGS}
        if record_mode == "rewrite":
            self._cassette: Any = Cassette(str(self.path), record_mode=vcr_mode, **kwargs)
        else:
            self._cassette = Cassette.load(path=str(self.path), record_mode=vcr_mode, **kwargs)

    def __len__(self) -> int:
        """The number of recorded interactions this transport holds."""
        return len(self._cassette)

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        body = await request.aread()
        self.requests.append(request)
        vcr_request = VcrRequest(request.method, str(request.url), body, dict(request.headers))
        if self._cassette.can_play_response_for(vcr_request):
            try:
                return self._replay(request, self._cassette.play_response(vcr_request))
            except UnhandledHTTPRequestError:
                pass
        if self._cassette.write_protected or self._inner is None:
            miss = CassetteMiss(self.path, request, body, _nearest(self._cassette, vcr_request))
            self.misses.append(miss)
            raise miss
        response = await self._inner.handle_async_request(request)

        def record(content: bytes) -> None:
            self._record(vcr_request, response, content)

        assert isinstance(response.stream, httpx.AsyncByteStream)
        return httpx.Response(
            status_code=response.status_code,
            headers=response.headers,
            stream=_TeeStream(response.stream, record),
            extensions=response.extensions,
        )

    def _record(self, vcr_request: Any, response: httpx.Response, content: bytes) -> None:
        try:
            self._cassette.responses_of(vcr_request)
            return  # an equal request is already recorded; keep the cassette free of repeats
        except UnhandledHTTPRequestError:
            pass
        headers: dict[str, list[str]] = {}
        for name, value in response.headers.multi_items():
            headers.setdefault(name, []).append(value)
        self._cassette.append(
            vcr_request,
            {
                "status": {"code": response.status_code, "message": response.reason_phrase},
                "headers": headers,
                "body": {"string": content},
            },
        )

    @staticmethod
    def _replay(request: httpx.Request, stored: Mapping[str, Any]) -> httpx.Response:
        body = stored["body"]["string"]
        if isinstance(body, str):
            body = body.encode()
        headers = [(name, value) for name, values in stored["headers"].items() for value in values]
        content_type = ",".join(v for n, v in headers if n.lower() == "content-type")
        sse = "text/event-stream" in content_type.lower()
        return httpx.Response(
            status_code=stored["status"]["code"],
            headers=headers,
            stream=_ReplayStream(_sse_frames(body) if sse else [body]),
            request=request,
        )

    def save(self) -> None:
        """Write the cassette if anything was recorded; `rewrite` always overwrites the file.
        `none` never writes.
        """
        if self.record_mode == "none":
            return
        self._cassette._save(force=self.record_mode == "rewrite")

    def assert_no_misses(self) -> None:
        if self.misses:
            listed = "\n".join(f"- {miss}" for miss in self.misses)
            raise AssertionError(f"{len(self.misses)} cassette miss(es) on {self.path}:\n{listed}")

    async def aclose(self) -> None:
        self.save()


def request_json(request: httpx.Request) -> Any:
    """The JSON body of a request the transport saw, for a binding's `received_messages`."""
    return json.loads(request.content)
