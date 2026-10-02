"""PoC-3 demo clients: call one agent through the chassis with off-the-shelf clients only.

Demo (docs/planning/poc/003-PoC-3-one-interface-every-client.md): "The same agent is called from
the OpenAI Python SDK, the Anthropic Python SDK, and an MCP client ... Every call streams, and
every call shows up in the router." Exit criteria: "The OpenAI and Anthropic SDKs work with only a
base URL change" and "An MCP client lists the agent's tool and runs it."

No custom client code: each client gets the chassis's base URL, a placeholder key (the chassis's
public port takes none in PoC-3), and `model` = the agent's name. Streamed chunks print as they
arrive, each with the seconds since the call started, so streaming is visible in the record.

    uv run --no-sync python pocs/poc-03-one-interface-every-client/demo/clients.py \\
        http://127.0.0.1:8080 echo --text "glossary: what does SLM mean?"
    # one client only; the MCP client through another URL (LiteLLM's MCP gateway):
    ... clients.py http://127.0.0.1:8080 echo --client mcp --mcp-url http://127.0.0.1:4000/mcp/

Exits 1 if any call fails, a streamed call yields no text, or the MCP tool answers an error.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
import urllib.request
from typing import Any

from anthropic import Anthropic
from fastmcp import Client
from openai import OpenAI

PLACEHOLDER_KEY = "placeholder-not-a-key"
"""Not a credential. The SDKs refuse to start without some key; the chassis ignores it."""
CLIENTS = ("openai", "anthropic", "mcp", "manifest")


def _since(start: float) -> str:
    return f"+{time.monotonic() - start:6.3f}s"


def call_openai(base: str, agent: str, text: str) -> None:
    """OpenAI Python SDK, `chat.completions.create(stream=True)`; base URL `<base>/v1`."""
    client = OpenAI(base_url=f"{base}/v1", api_key=PLACEHOLDER_KEY, max_retries=0)
    print(f"OpenAI(base_url={base + '/v1'!r}, api_key=<placeholder>)")
    print(f"chat.completions.create(model={agent!r}, stream=True)")
    start = time.monotonic()
    pieces: list[str] = []
    stream = client.chat.completions.create(
        model=agent, messages=[{"role": "user", "content": text}], stream=True
    )
    for chunk in stream:
        for choice in chunk.choices:
            piece = choice.delta.content or ""
            pieces.append(piece)
            finish = f" finish_reason={choice.finish_reason}" if choice.finish_reason else ""
            print(f"  {_since(start)} chunk {piece!r}{finish}", flush=True)
    answer = "".join(pieces)
    print(f"text: {answer!r}")
    if not answer:
        raise RuntimeError("openai: the stream carried no text")


def call_anthropic(base: str, agent: str, text: str) -> None:
    """Anthropic Python SDK, `messages.stream`; base URL `<base>` (the SDK adds `/v1/messages`)."""
    client = Anthropic(base_url=base, api_key=PLACEHOLDER_KEY, max_retries=0)
    print(f"Anthropic(base_url={base!r}, api_key=<placeholder>)")
    print(f"messages.stream(model={agent!r}, max_tokens=256)")
    start = time.monotonic()
    with client.messages.stream(
        model=agent, max_tokens=256, messages=[{"role": "user", "content": text}]
    ) as stream:
        for event in stream:
            if event.type == "text":
                continue  # the SDK's own echo of each text delta, printed below already
            detail = ""
            if event.type == "content_block_delta" and event.delta.type == "text_delta":
                detail = f" {event.delta.text!r}"
            elif event.type == "message_delta":
                detail = f" stop_reason={event.delta.stop_reason}"
            print(f"  {_since(start)} event {event.type}{detail}", flush=True)
        answer = stream.get_final_text()
    print(f"text: {answer!r}")
    if not answer:
        raise RuntimeError("anthropic: the stream carried no text")


async def _call_mcp(url: str, agent: str, text: str) -> None:
    async with Client(url) as client:
        tools = await client.list_tools()
        for tool in tools:
            props = sorted((tool.input_schema or {}).get("properties", {}))
            print(f"  tool {tool.name!r} input properties {props}")
        # Direct, the tool is named after the agent; LiteLLM's gateway prefixes the server name.
        names = [t.name for t in tools if t.name == agent or t.name.endswith(f"-{agent}")]
        if not names:
            raise RuntimeError(f"mcp: no tool named {agent!r} in {[t.name for t in tools]}")
        print(f"call_tool({names[0]!r}, {{'input': {{'text': ...}}}})")
        start = time.monotonic()
        result = await client.call_tool(names[0], {"input": {"text": text}}, raise_on_error=False)
        print(f"  {_since(start)} result (one message: the MCP tool does not stream)")
        data: Any = result.structured_content
        if data is None:
            data = [getattr(block, "text", str(block)) for block in result.content]
        print(f"  {json.dumps(data)[:1200]}")
        if result.is_error:
            raise RuntimeError("mcp: the tool answered an error")
        if isinstance(data, dict) and data.get("status") not in (None, "ok"):
            raise RuntimeError(f"mcp: run status {data.get('status')!r}")


def call_mcp(url: str, agent: str, text: str) -> None:
    """`fastmcp.Client` over streamable HTTP: list the tools, call the agent's tool."""
    print(f"fastmcp.Client({url!r})  # streamable HTTP")
    asyncio.run(_call_mcp(url, agent, text))


def get_manifest(base: str) -> None:
    """`GET /manifest`, printed as it comes."""
    print(f"GET {base}/manifest")
    with urllib.request.urlopen(f"{base}/manifest", timeout=10) as response:
        manifest = json.load(response)
    print(json.dumps(manifest, indent=1)[:3000])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "base_url", help="the chassis's public base URL, e.g. http://127.0.0.1:8080"
    )
    parser.add_argument("agent", help="the agent's name; sent as `model`")
    parser.add_argument("--text", default="glossary: what does SLM mean? Say it in plain words.")
    parser.add_argument("--client", action="append", choices=CLIENTS, help="repeatable; all")
    parser.add_argument("--mcp-url", help="the MCP endpoint; default <base_url>/v1/mcp")
    args = parser.parse_args(argv)
    base = args.base_url.rstrip("/")
    failed = 0
    for name in args.client or CLIENTS:
        print(f"### {name}", flush=True)
        try:
            if name == "openai":
                call_openai(base, args.agent, args.text)
            elif name == "anthropic":
                call_anthropic(base, args.agent, args.text)
            elif name == "mcp":
                call_mcp(args.mcp_url or f"{base}/v1/mcp", args.agent, args.text)
            else:
                get_manifest(base)
            print(f"{name}: ok", flush=True)
        except Exception as exc:  # report every client, then fail once
            print(f"{name}: FAILED {type(exc).__name__}: {exc}", flush=True)
            failed = 1
    return failed


if __name__ == "__main__":
    sys.exit(main())
