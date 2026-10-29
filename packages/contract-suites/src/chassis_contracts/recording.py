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
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx
from vcr import VCR
from vcr.cassette import Cassette
from vcr.errors import UnhandledHTTPRequestError
from vcr.record_mode import RecordMode
from vcr.request import Request as VcrRequest

__all__ = [
    "KEPT_HEADERS",
    "KEY_FIELDS",
    "KEY_SHAPED",
    "RECORD_MODES",
    "CassetteMiss",
    "CassetteSecretError",
    "CassetteTransport",
    "default_vcr_config",
    "request_json",
]

RECORD_MODES = ("none", "once", "new_episodes", "all", "rewrite")
"""The modes pytest-recording's `--record-mode` accepts."""

KEPT_HEADERS = frozenset({"content-type"})
"""The only header stored on a request or a response. `content-type` lets the `body` matcher
compare JSON bodies as JSON."""

# suggested: the query parameter and JSON body field names that carry a key or a secret.
KEY_FIELDS = (
    "key",
    "api_key",
    "apikey",
    "access_token",
    "token",
    "authorization",
    "client_secret",
    "password",
    "refresh_token",
    "secret",
    "x-api-key",
)
"""Dropped from a stored query string; replaced by `REDACTED` in a stored JSON request or
response body, at any depth. Names match case-insensitively with `-` and `_` removed, so
`API_KEY`, `api-key`, and `apiKey` all count. The same scrub runs on replay, so matching is
unchanged."""

# suggested: the key shapes (OpenAI and Anthropic `sk-`, AWS, GitHub, Google API key, Slack,
# Google OAuth access token).
KEY_SHAPED = re.compile(
    r"(?<![A-Za-z0-9])sk-[A-Za-z0-9_-]{8,}|AKIA[0-9A-Z]{16}|ghp_[A-Za-z0-9]{20,}|AIza[0-9A-Za-z_-]{35}"
    r"|xox[abposr]-[A-Za-z0-9-]{10,}|ya29\.[0-9A-Za-z_-]{20,}"
)
"""A key-shaped string. It is replaced by `REDACTED` in a stored response body and in a miss
message. A request or response that still holds one at save time stops `save()`."""

_KEY_SHAPED_BYTES = re.compile(KEY_SHAPED.pattern.encode())

REDACTED = "[redacted]"

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


def _normal_name(name: str) -> str:
    return name.lower().replace("-", "").replace("_", "")


_KEY_NAMES = frozenset(_normal_name(name) for name in KEY_FIELDS)


def _is_key_name(name: object) -> bool:
    """True when `name` is a `KEY_FIELDS` name in any case, with `-` and `_` ignored."""
    return isinstance(name, str) and _normal_name(name) in _KEY_NAMES


def _scrub(node: Any) -> Any:
    """`node` with the value of every `KEY_FIELDS` key replaced by `REDACTED`, at any depth."""
    if isinstance(node, dict):
        return {k: REDACTED if _is_key_name(k) else _scrub(v) for k, v in node.items()}
    if isinstance(node, list):
        return [_scrub(v) for v in node]
    return node


def _scrub_body(body: bytes | str | None) -> bytes | str | None:
    """A JSON body with its key fields scrubbed; any other body, or one with none, as it is. A
    body nested too deep to parse is opaque and kept as it is."""
    if not body:
        return body
    try:
        data = json.loads(body)
        scrubbed = _scrub(data)
        if scrubbed == data:
            return body
        return json.dumps(scrubbed, separators=(",", ":")).encode()
    except (ValueError, UnicodeDecodeError, RecursionError):
        return body


def _drop_key_params(uri: str) -> str:
    """`uri` without the query parameters whose names are `KEY_FIELDS` names."""
    parts = urlsplit(uri)
    params = parse_qsl(parts.query, keep_blank_values=True)
    kept = [(k, v) for k, v in params if not _is_key_name(k)]
    if len(kept) == len(params):
        return uri
    return urlunsplit(parts._replace(query=urlencode(kept)))


def _keep_request_headers(request: Any) -> Any:
    """Keep only `KEPT_HEADERS`, drop key query parameters, and scrub the JSON body."""
    kept = copy.copy(request)
    kept.headers = {k: v for k, v in request.headers.items() if k.lower() in KEPT_HEADERS}
    kept.uri = _drop_key_params(request.uri)
    kept.body = _scrub_body(request.body)
    return kept


def _redact_body(body: bytes | str | None) -> bytes | str | None:
    """`body` with key fields scrubbed if it is JSON, then every `KEY_SHAPED` match redacted."""
    scrubbed = _scrub_body(body)
    if isinstance(scrubbed, bytes):
        return _KEY_SHAPED_BYTES.sub(REDACTED.encode(), scrubbed)
    if isinstance(scrubbed, str):
        return KEY_SHAPED.sub(REDACTED, scrubbed)
    return scrubbed


def _keep_response(response: dict[str, Any]) -> dict[str, Any]:
    """Keep only `KEPT_HEADERS` and redact keys in the body. Returns a new dict."""
    headers = response.get("headers") or {}
    body = response.get("body") or {}
    return {
        **response,
        "headers": {k: v for k, v in headers.items() if k.lower() in KEPT_HEADERS},
        "body": {**body, "string": _redact_body(body.get("string"))},
    }


def default_vcr_config() -> dict[str, Any]:
    """The shared config: match on everything but headers, store only `content-type`, drop auth
    headers as a second guard, drop `KEY_FIELDS` query parameters, scrub `KEY_FIELDS` in a JSON
    request or response body, and redact `KEY_SHAPED` strings in a response body. Return it from
    a `vcr_config` fixture or pass it directly.
    """
    return {
        "match_on": ["method", "scheme", "host", "port", "path", "query", "body"],
        "allow_playback_repeats": True,
        "decode_compressed_response": True,
        "before_record_request": _keep_request_headers,
        "before_record_response": _keep_response,
        "filter_headers": [
            "authorization",
            "x-api-key",
            "api-key",
            "cookie",
            "set-cookie",
            "x-litellm-api-key",
        ],
        "filter_query_parameters": list(KEY_FIELDS),
    }


class CassetteSecretError(RuntimeError):
    """A request or response about to be saved still holds a key-shaped string; nothing is
    written."""


class CassetteMiss(httpx.TransportError):
    """A request the cassette cannot answer in this record mode. The message names the body."""

    def __init__(self, path: Path, request: httpx.Request, body: bytes, nearest: str) -> None:
        text = _redact(_scrub_body(body) or b"")
        if len(text) > MISS_BODY_CAP:
            text = text[:MISS_BODY_CAP] + "...[truncated]"
        url = _redact(str(request.url.copy_with(userinfo=b"", query=None, fragment=None)))
        if request.url.query:
            url = f"{url}?{REDACTED}"
        super().__init__(
            f"cassette miss: {request.method} {url} is not in {path}"
            f" ({nearest}). Unmatched body: {text}",
            request=request,
        )
        self.path = path
        self.body = body


def _redact(text: bytes | str) -> str:
    """`text` with every `KEY_SHAPED` substring replaced by `REDACTED`."""
    decoded = text.decode(errors="replace") if isinstance(text, bytes) else text
    return KEY_SHAPED.sub(REDACTED, decoded)


def _has_key(parts: list[Any]) -> bool:
    texts = (p.decode(errors="replace") if isinstance(p, bytes) else str(p) for p in parts)
    return any(KEY_SHAPED.search(text) for text in texts)


def _check_no_key(requests: list[Any], responses: list[Mapping[str, Any]]) -> None:
    """Raise `CassetteSecretError` if a request or response to be saved still holds a key-shaped
    string."""
    for index, request in enumerate(requests):
        seen = [request.uri, request.body or b""]
        seen.extend(f"{k}: {v}" for k, v in request.headers.items())
        if _has_key(seen):
            raise CassetteSecretError(
                f"request {index} ({request.method} {_redact(request.uri)}) still holds a"
                " key-shaped string after the scrub; the cassette is not saved"
            )
    for index, response in enumerate(responses):
        seen = [(response.get("body") or {}).get("string") or b""]
        seen.extend(f"{k}: {v}" for k, v in (response.get("headers") or {}).items())
        if _has_key(seen):
            raise CassetteSecretError(
                f"response {index} still holds a key-shaped string after the scrub;"
                " the cassette is not saved"
            )


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
        `none` never writes. Raises `CassetteSecretError`, and writes nothing, if a recorded
        request or response still holds a key-shaped string after the scrub.
        """
        if self.record_mode == "none":
            return
        _check_no_key(list(self._cassette.requests), list(self._cassette.responses))
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
