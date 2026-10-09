"""PoC-6a extra offline measurements that `bakeoff` does not make.

Per engine, on localhost with the fake model (no network, no key):

- RSS of the workload process tree: after ready (idle), after one warm-up run, and after
  10 runs of each task (peak of the tree sampled every 20 ms during those runs).
- Cold start: spawn of the workload process alone to the first 200 on its agent card.
- Router keys: the request-body keys each Python workload sends (its `handle` run in this
  process against a recording fake model, tools over the stack's chassis `/mcp`), and the keys
  the chassis model proxy would drop.
- Streaming fidelity: deltas per answer, time to first token, and the mean gap between deltas,
  from the streamed `POST /v1/run`; plus the event types the stream carried.

Run from the repo root:

    uv run python pocs/poc-06a-bake-off-sidecar-lane/demo/measure_extra.py --out DIR

It imports `bakeoff` as a library and starts the same stacks as `bakeoff run`.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import importlib
import json
import os
import statistics
import sys
import threading
import time
from pathlib import Path
from typing import Any

import httpx
from bakeoff.config import workload_argv, workload_env_vars
from bakeoff.envs import build_env
from bakeoff.measure import RunError, run_complete
from bakeoff.procs import Cleanup, free_port, install_signal_handlers
from bakeoff.registry import ENGINES, ROOT, TS_MAIN, Engine, Lane, lanes_for
from bakeoff.stack import FakeModel, running_stack
from bakeoff.tasks import Task, get_tasks

TASKS = ("smoke", "simplifier", "lookup")
CARD = "/.well-known/agent-card.json"


def _tasks() -> dict[str, Task]:
    return {task.name: task for task in get_tasks(TASKS)}


def tree_rss_kb(pgid: int) -> int:
    """Sum of VmRSS (kB) over every process whose process group is `pgid`."""
    total = 0
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            stat = (entry / "stat").read_text()
            fields = stat[stat.rindex(")") + 2 :].split()
            if int(fields[2]) != pgid:  # pgrp is the 5th stat field, index 2 after the name
                continue
            for line in (entry / "status").read_text().splitlines():
                if line.startswith("VmRSS:"):
                    total += int(line.split()[1])
        except (OSError, ValueError):
            continue
    return total


class Sampler(threading.Thread):
    def __init__(self, pgid: int):
        super().__init__(daemon=True)
        self.pgid, self.peak, self._halt = pgid, 0, threading.Event()

    def run(self) -> None:
        while not self._halt.is_set():
            self.peak = max(self.peak, tree_rss_kb(self.pgid))
            time.sleep(0.02)

    def finish(self) -> int:
        self._halt.set()
        self.join()
        return self.peak


def pick_lane(engine: Engine) -> Lane:
    lanes = lanes_for(engine)
    return "sidecar" if "sidecar" in lanes else "remote"


def stream_events(client: httpx.Client, task: Task) -> list[tuple[str, float]]:
    """(event name, ms since the request) for each SSE frame of a streamed run."""
    started = time.perf_counter()
    out: list[tuple[str, float]] = []
    name = ""
    with client.stream(
        "POST", "/v1/run", json={"input": {"text": task.text}, "stream": True}
    ) as response:
        response.raise_for_status()
        for line in response.iter_lines():
            if line.startswith("event:"):
                name = line[6:].strip()
            elif line.startswith("data:") and name:
                out.append((name, (time.perf_counter() - started) * 1000))
    return out


def stream_stats(client: httpx.Client, task: Task, repeat: int) -> dict[str, Any]:
    deltas: list[int] = []
    ttft: list[float] = []
    gaps: list[float] = []
    totals: list[float] = []
    kinds: set[str] = set()
    for _ in range(repeat):
        frames = stream_events(client, task)
        kinds.update(n for n, _ in frames)
        times = [t for n, t in frames if n == "delta"]
        deltas.append(len(times))
        if times:
            ttft.append(times[0])
            if len(times) > 1:
                gaps.append((times[-1] - times[0]) / (len(times) - 1))
        totals.append(frames[-1][1])
    return {
        "deltas_min": min(deltas),
        "deltas_median": statistics.median(deltas),
        "deltas_max": max(deltas),
        "ttft_p50_ms": statistics.median(ttft) if ttft else None,
        "gap_mean_ms": statistics.mean(gaps) if gaps else None,
        "total_p50_ms": statistics.median(totals),
        "event_types": sorted(kinds),
    }


PROXY_KEEPS = frozenset({"model", "messages", "temperature", "max_tokens", "tools", "stream"})
"""The body keys `chassis.server.model_proxy.ChatCompletionRequest` reads; others are ignored."""


def _flag(argv: list[str], name: str) -> str:
    return str(argv[argv.index(name) + 1])


def workload_keys(engine: Engine, cleanup: Cleanup, task: Task) -> dict[str, Any] | None:
    """Run the engine's `handle` here against a recording fake model; return the keys it sent.

    Tools come from the stack's chassis `/mcp`. Python engines only, and not the Claude one
    (its CLI speaks the Messages API; see its README)."""
    if engine.handle is None or engine.name == "echo-claude-agent":
        return None
    module_name, attr = engine.handle.split(":")
    handle = getattr(importlib.import_module(module_name), attr)
    lane = pick_lane(engine)
    with running_stack(engine, lane, cleanup):
        chassis = next(p for p in cleanup.procs if p.name == "chassis")
        args = chassis.proc.args
        assert isinstance(args, list)
        proxy_port = _flag([str(a) for a in args], "--proxy-port")
        tool_url = f"http://127.0.0.1:{proxy_port}/mcp"
        rec = FakeModel(ROOT / engine.script, free_port())
        rec.start()
        os.environ["CHASSIS_MODEL_URL"] = f"http://127.0.0.1:{rec.port}/v1"
        os.environ["CHASSIS_TOOL_URL"] = tool_url
        sent: set[str] = set()
        per_call: list[list[str]] = []

        async def go() -> None:
            async for _ in handle({"text": task.text}, {}):
                pass

        try:
            asyncio.run(go())
            for call in rec.since(0):
                per_call.append(sorted(call))
                sent.update(call)
        finally:
            rec.stop()
            for name in ("CHASSIS_MODEL_URL", "CHASSIS_TOOL_URL"):
                os.environ.pop(name, None)
    return {
        "calls": len(per_call),
        "sent_keys": sorted(sent),
        "dropped_by_proxy": sorted(sent - PROXY_KEEPS),
        "kept_by_proxy": sorted(sent & PROXY_KEEPS),
    }


def measure_engine(engine: Engine, cleanup: Cleanup, repeat: int) -> dict[str, Any]:
    lane = pick_lane(engine)
    tasks = _tasks()
    result: dict[str, Any] = {"engine": engine.name, "lane": lane}
    with running_stack(engine, lane, cleanup) as stack:
        workload = next((p for p in cleanup.procs if p.name == engine.name), None)
        pgid = workload.proc.pid if workload else 0
        with httpx.Client(base_url=stack.url, trust_env=False, timeout=120.0) as client:
            result["rss_idle_kb"] = tree_rss_kb(pgid) if pgid else None
            run_complete(client, tasks["smoke"])
            result["rss_warm_kb"] = tree_rss_kb(pgid) if pgid else None
            sampler = Sampler(pgid)
            sampler.start()
            for task in tasks.values():
                for _ in range(repeat):
                    with contextlib.suppress(RunError):
                        run_complete(client, task)
            result["rss_peak_kb"] = sampler.finish()
            result["rss_after_kb"] = tree_rss_kb(pgid) if pgid else None
            result["stream"] = {
                name: stream_stats(client, tasks[name], repeat) for name in ("simplifier", "lookup")
            }
    return result


def cold_start(engine: Engine, cleanup: Cleanup, samples: int) -> list[float]:
    """ms from spawn to the first 200 on the agent card, workload alone, sidecar argv."""
    out: list[float] = []
    for _ in range(samples):
        work = cleanup.tempdir()
        home = work / "home"
        home.mkdir()
        port = free_port()
        env = build_env(
            home,
            workload_env_vars(
                engine,
                "sidecar",
                port=port,
                model_url="http://127.0.0.1:9/v1",
                tool_url="http://127.0.0.1:9/mcp",
                token="",
            ),
        )
        argv = workload_argv(engine, "sidecar", port=port, main_js=str(ROOT / TS_MAIN))
        cwd = ROOT / TS_MAIN.parents[2] if engine.runner == "node" else ROOT
        with httpx.Client(trust_env=False, timeout=1.0) as client:
            started = time.perf_counter()
            child = cleanup.spawn(engine.name, argv, env, cwd, work / "w.log")
            ms: float | None = None
            while time.perf_counter() - started < 60:
                if not child.alive():
                    break
                try:
                    if client.get(f"http://127.0.0.1:{port}{CARD}").status_code == 200:
                        ms = (time.perf_counter() - started) * 1000
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(0.005)
        cleanup.release(child)
        cleanup.remove_dir(work)
        if ms is not None:
            out.append(ms)
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--repeat", type=int, default=10)
    parser.add_argument("--cold-samples", type=int, default=5)
    args = parser.parse_args()
    install_signal_handlers()
    cleanup = Cleanup()
    rows: list[dict[str, Any]] = []
    for engine in ENGINES:
        reason = engine.skip_reason()
        if reason:
            rows.append({"engine": engine.name, "skip": reason})
            print(f"SKIP {engine.name}: {reason}", flush=True)
            continue
        row = measure_engine(engine, cleanup, args.repeat)
        row["cold_start_ms"] = cold_start(engine, cleanup, args.cold_samples)
        row["router_keys"] = workload_keys(engine, cleanup, _tasks()["lookup"])
        rows.append(row)
        print(json.dumps(row), flush=True)
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "extra.json").write_text(json.dumps(rows, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
