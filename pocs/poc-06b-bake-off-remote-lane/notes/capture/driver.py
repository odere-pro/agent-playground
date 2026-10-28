"""Drive the Claude Agent SDK query() against the capture server and record the CLI process.

Run it only through run.sh: the SDK merges os.environ into the CLI environment, so the child
environment is an allow-list built by `env -i` in the shell. This script refuses to start if
it sees any ANTHROPIC_, CLAUDE_, *_TOKEN, or *_KEY variable that is not on the allow-list.
It writes the CLI environment variable NAMES (never values) and ps snapshots to a JSON file.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

import claude_agent_sdk
from claude_agent_sdk import ClaudeAgentOptions, query

ALLOWED = {
    "PATH",
    "HOME",
    "ANTHROPIC_BASE_URL",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_MODEL",
    "ANTHROPIC_SMALL_FAST_MODEL",
    "ANTHROPIC_CUSTOM_HEADERS",
    "DISABLE_TELEMETRY",
    "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC",
    "DISABLE_AUTOUPDATER",
    "DISABLE_ERROR_REPORTING",
    "HTTPS_PROXY",
    "HTTP_PROXY",
    "NO_PROXY",
}


def guard() -> None:
    for name in os.environ:
        risky = name.startswith(("ANTHROPIC_", "CLAUDE")) or name.endswith(("_TOKEN", "_KEY"))
        if risky and name not in ALLOWED:
            sys.exit(f"refusing to run: unexpected variable name {name}")
    if os.environ.get("ANTHROPIC_AUTH_TOKEN") != "dummy-capture-token":
        sys.exit("refusing to run: ANTHROPIC_AUTH_TOKEN must be the literal dummy-capture-token")


def descendants(root: int) -> list[dict[str, Any]]:
    out = subprocess.run(
        ["ps", "-eo", "pid,ppid,etimes,rss,args", "--no-headers"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    rows = []
    for line in out.splitlines():
        pid, ppid, et, rss, args = line.split(None, 4)
        rows.append(
            {
                "pid": int(pid),
                "ppid": int(ppid),
                "etimes": int(et),
                "rss_kb": int(rss),
                "args": args,
            }
        )
    keep = {root}
    changed = True
    while changed:
        changed = False
        for r in rows:
            if r["ppid"] in keep and r["pid"] not in keep:
                keep.add(r["pid"])
                changed = True
    return [r for r in rows if r["pid"] in keep and r["pid"] != root]


def environ_names(pid: int) -> list[str]:
    raw = Path(f"/proc/{pid}/environ").read_bytes()
    return sorted(e.split(b"=", 1)[0].decode() for e in raw.split(b"\0") if e)


def sampler(report: dict[str, Any], stop: threading.Event) -> None:
    seen: dict[int, str] = {}
    while not stop.is_set():
        try:
            procs = descendants(os.getpid())
        except Exception:
            procs = []
        for p in procs:
            if p["pid"] not in seen:
                seen[p["pid"]] = p["args"][:300]
                snap = {"args": p["args"][:300], "ppid": p["ppid"], "pid": p["pid"]}
                with contextlib.suppress(OSError):
                    snap["environ_names"] = environ_names(p["pid"])
                report["processes"].append(snap)
        if procs:
            report["ps_snapshots"].append(
                [f"{p['pid']} {p['ppid']} rss={p['rss_kb']}kB {p['args'][:160]}" for p in procs]
            )
        time.sleep(0.4)


def save(path: str, report: dict[str, Any], stderr_lines: list[str]) -> None:
    Path(path).write_text(json.dumps(report, indent=1))
    Path(path + ".stderr").write_text("\n".join(stderr_lines))


async def run(args: list[str]) -> None:
    scenario, prompt, workdir, mcp_port, report_path = args[:5]
    cli = Path(claude_agent_sdk.__file__).parent / "_bundled" / "claude"
    report: dict[str, Any] = {
        "scenario": scenario,
        "cli_path": str(cli),
        "sdk_version": claude_agent_sdk.__version__,
        "processes": [],
        "ps_snapshots": [],
        "messages": [],
    }
    stderr_lines: list[str] = []
    kwargs: dict[str, Any] = {
        "cli_path": str(cli),
        "cwd": workdir,
        "max_turns": 6,
        "stderr": stderr_lines.append,
        "extra_args": {"debug-to-stderr": None},
    }
    if scenario in ("tool", "tool-nohdr"):
        server: dict[str, Any] = {"type": "http", "url": f"http://127.0.0.1:{mcp_port}/mcp"}
        if scenario == "tool":
            server["headers"] = {
                "traceparent": "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01",
                "Authorization": "Bearer dummy",
            }
        kwargs["mcp_servers"] = {"glossary": server}
        kwargs["allowed_tools"] = ["mcp__glossary__glossary_lookup"]
    if scenario == "shell":
        kwargs["allowed_tools"] = ["Bash", "Read", "Write"]
    stop = threading.Event()
    thread = threading.Thread(target=sampler, args=(report, stop), daemon=True)
    thread.start()
    try:
        async for msg in query(prompt=prompt, options=ClaudeAgentOptions(**kwargs)):
            report["messages"].append(f"{type(msg).__name__}: {str(msg)[:300]}")
    finally:
        stop.set()
        thread.join(timeout=2)
        report["stderr_tail"] = stderr_lines[-60:]
        save(report_path, report, stderr_lines)


if __name__ == "__main__":
    guard()
    asyncio.run(run(sys.argv[1:]))
