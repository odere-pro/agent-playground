"""Capture server: log what the Claude Code CLI sends, answer valid Anthropic Messages replies.

Modes (first argument):
  messages  serve /v1/messages (JSON and SSE) and /v1/messages/count_tokens; log every request.
  tripwire  accept TCP connections on a dead proxy port, log the first request line, then close.
  mcp       a streamable-HTTP MCP server with one tool, glossary_lookup(term); log every request.

The log is JSON lines. Header values of authorization, x-api-key, and any header whose name
contains "token" or "key" are replaced with <redacted>. Offline; stdlib plus the scratch venv.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import threading
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path
from typing import Any

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route

_LOCK = threading.Lock()
GLOSSARY = {"SLM": "small language model", "A2A": "agent-to-agent protocol"}


def redact(headers: Any) -> dict[str, str]:
    out: dict[str, str] = {}
    for name, value in headers.items():
        low = name.lower()
        secret = low in ("authorization", "x-api-key") or "token" in low or "key" in low
        out[low] = "<redacted>" if secret else value
    return out


def write_log(path: Path, entry: dict[str, Any]) -> None:
    entry["t"] = round(time.time(), 3)
    with _LOCK, path.open("a") as fh:
        fh.write(json.dumps(entry) + "\n")


async def log_request(request: Request, log: Path, body: bytes) -> Any:
    parsed: Any = None
    if body:
        try:
            parsed = json.loads(body)
        except ValueError:
            parsed = {"_raw": body[:2000].decode(errors="replace")}
    write_log(
        log,
        {
            "kind": "http",
            "method": request.method,
            "path": request.url.path,
            "query": dict(request.query_params),
            "headers": redact(request.headers),
            "body": parsed,
        },
    )
    return parsed


# ---- scripted model -------------------------------------------------------------------------


def count_results(body: dict[str, Any]) -> int:
    n = 0
    for msg in body.get("messages", []):
        content = msg.get("content")
        if isinstance(content, list):
            n += sum(1 for b in content if isinstance(b, dict) and b.get("type") == "tool_result")
    return n


def plan(body: dict[str, Any], scenario: str, workdir: str) -> list[dict[str, Any]]:
    """Return the content blocks of the reply."""
    names = {t.get("name", "") for t in body.get("tools") or []}
    done = count_results(body)
    mcp = next((n for n in names if n.endswith("glossary_lookup")), None)
    if scenario == "tool" and mcp and done == 0:
        return [{"type": "tool_use", "name": mcp, "input": {"term": "SLM"}}]
    if scenario == "shell" and {"Bash", "Write", "Read"} <= names and done < 3:
        steps = [
            ("Bash", {"command": f"sleep 3; printf 'from-bash\\n' > {workdir}/bash-out.txt"}),
            ("Write", {"file_path": f"{workdir}/write-out.txt", "content": "from-write\n"}),
            ("Read", {"file_path": f"{workdir}/write-out.txt"}),
        ]
        name, tool_input = steps[done]
        return [{"type": "tool_use", "name": name, "input": tool_input}]
    return [{"type": "text", "text": "Hello." if done == 0 else "Done."}]


def with_ids(blocks: list[dict[str, Any]], seq: int) -> list[dict[str, Any]]:
    out = []
    for i, block in enumerate(blocks):
        block = dict(block)
        if block["type"] == "tool_use":
            block["id"] = f"toolu_cap{seq:03d}{i}"
        out.append(block)
    return out


def sse(event: str, data: dict[str, Any]) -> bytes:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n".encode()


def usage(body: dict[str, Any]) -> dict[str, int]:
    return {
        "input_tokens": max(1, len(json.dumps(body.get("messages", []))) // 4),
        "output_tokens": 5,
    }


async def stream_events(
    body: dict[str, Any], blocks: list[dict[str, Any]], seq: int
) -> AsyncIterator[bytes]:
    stop = "tool_use" if any(b["type"] == "tool_use" for b in blocks) else "end_turn"
    use = usage(body)
    start = {
        "id": f"msg_cap{seq:04d}",
        "type": "message",
        "role": "assistant",
        "model": body.get("model", "unknown"),
        "content": [],
        "stop_reason": None,
        "stop_sequence": None,
        "usage": {"input_tokens": use["input_tokens"], "output_tokens": 1},
    }
    yield sse("message_start", {"type": "message_start", "message": start})
    yield sse("ping", {"type": "ping"})
    for i, block in enumerate(blocks):
        if block["type"] == "text":
            empty = {"type": "text", "text": ""}
            delta = {"type": "text_delta", "text": block["text"]}
        else:
            empty = {k: block[k] for k in ("type", "id", "name")} | {"input": {}}
            delta = {"type": "input_json_delta", "partial_json": json.dumps(block["input"])}
        yield sse(
            "content_block_start",
            {"type": "content_block_start", "index": i, "content_block": empty},
        )
        yield sse(
            "content_block_delta", {"type": "content_block_delta", "index": i, "delta": delta}
        )
        yield sse("content_block_stop", {"type": "content_block_stop", "index": i})
    yield sse(
        "message_delta",
        {
            "type": "message_delta",
            "delta": {"stop_reason": stop, "stop_sequence": None},
            "usage": {"output_tokens": use["output_tokens"]},
        },
    )
    yield sse("message_stop", {"type": "message_stop"})


def build_messages_app(log: Path, scenario: str, workdir: str) -> Starlette:
    counter = {"n": 0}

    async def handle(request: Request) -> Response:
        raw = await request.body()
        body = await log_request(request, log, raw)
        path = request.url.path
        if request.method == "POST" and path == "/v1/messages/count_tokens":
            return JSONResponse({"input_tokens": 42})
        if request.method == "POST" and path == "/v1/messages" and isinstance(body, dict):
            counter["n"] += 1
            seq = counter["n"]
            blocks = with_ids(plan(body, scenario, workdir), seq)
            if body.get("stream"):
                return StreamingResponse(
                    stream_events(body, blocks, seq),
                    media_type="text/event-stream",
                    headers={"cache-control": "no-cache"},
                )
            stop = "tool_use" if any(b["type"] == "tool_use" for b in blocks) else "end_turn"
            return JSONResponse(
                {
                    "id": f"msg_cap{seq:04d}",
                    "type": "message",
                    "role": "assistant",
                    "model": body.get("model", "unknown"),
                    "content": blocks,
                    "stop_reason": stop,
                    "stop_sequence": None,
                    "usage": usage(body),
                }
            )
        return JSONResponse(
            {"type": "error", "error": {"type": "not_found_error", "message": "capture: no route"}},
            status_code=404,
        )

    methods = ["GET", "POST", "PUT", "DELETE", "HEAD", "OPTIONS", "PATCH"]
    return Starlette(routes=[Route("/{rest:path}", handle, methods=methods)])


# ---- tripwire for the dead proxy port -------------------------------------------------------


async def run_tripwire(port: int, log: Path) -> None:
    async def on_conn(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            line = await asyncio.wait_for(reader.readline(), timeout=2)
        except TimeoutError:
            line = b""
        write_log(log, {"kind": "tripwire", "first_line": line.decode(errors="replace").strip()})
        writer.close()

    server = await asyncio.start_server(on_conn, "127.0.0.1", port)
    async with server:
        await server.serve_forever()


# ---- MCP server -----------------------------------------------------------------------------


def build_mcp_app(log: Path) -> Any:
    from mcp.server.fastmcp import FastMCP
    from mcp.server.transport_security import TransportSecuritySettings

    mcp = FastMCP(
        "glossary",
        stateless_http=True,
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )

    @mcp.tool()
    def glossary_lookup(term: str) -> str:
        """Look up a term in the glossary."""
        return GLOSSARY.get(term, "unknown term")

    app = mcp.streamable_http_app()

    class LogMiddleware:
        def __init__(self, inner: Any) -> None:
            self.inner = inner

        async def __call__(
            self,
            scope: dict[str, Any],
            receive: Callable[[], Awaitable[dict[str, Any]]],
            send: Callable[[dict[str, Any]], Awaitable[None]],
        ) -> None:
            if scope["type"] != "http":
                await self.inner(scope, receive, send)
                return
            chunks: list[bytes] = []

            async def spy() -> dict[str, Any]:
                msg = await receive()
                if msg["type"] == "http.request":
                    chunks.append(msg.get("body", b""))
                return msg

            headers = {k.decode(): v.decode() for k, v in scope["headers"]}
            await self.inner(scope, spy, send)
            raw = b"".join(chunks)
            try:
                body: Any = json.loads(raw) if raw else None
            except ValueError:
                body = {"_raw": raw[:500].decode(errors="replace")}
            write_log(
                log,
                {
                    "kind": "mcp",
                    "method": scope["method"],
                    "path": scope["path"],
                    "headers": redact(headers),
                    "traceparent": headers.get("traceparent"),
                    "auth_scheme": (headers.get("authorization") or "").split(" ")[0] or None,
                    "body": body,
                },
            )

    return LogMiddleware(app)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["messages", "tripwire", "mcp"])
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--log", type=Path, required=True)
    ap.add_argument("--scenario", default="plain", choices=["plain", "tool", "shell"])
    ap.add_argument("--workdir", default="/tmp")
    args = ap.parse_args()
    if args.mode == "tripwire":
        asyncio.run(run_tripwire(args.port, args.log))
        return
    app: Any = (
        build_messages_app(args.log, args.scenario, args.workdir)
        if args.mode == "messages"
        else build_mcp_app(args.log)
    )
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
