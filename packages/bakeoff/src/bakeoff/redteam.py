"""The PoC-7 red-team set on every engine and every lane, through real processes (PoC-8).

    uv run python -m bakeoff.redteam --out redteam.json                         # check
    uv run python -m bakeoff.redteam --engine echo-python --lane sidecar --out x.json
    uv run python -m bakeoff.redteam --out redteam.json --record                # new baseline

Per guardrail config (`litellm`: the LiteLLM guardrail stub and the guardrail server with the
pattern stand-in, the PoC-7 stack; `keyword`: the port's fake), engine, and lane (the trust rule,
`registry.lanes_for`) it starts the bake-off stack with:

- the chassis's `spec.adapters.guardrails` set to the config and `tools: mcp`, pointed at an MCP
  tool server in a thread of this process (`fake_mcp_server.redteam`, pages from `live.tool_pages`);
- the fake model server answering the red-team script (`live.model_script`, in the engine's
  dialect); for `litellm` the guardrail stub shares its base URL, as LiteLLM does.

Then it sends every case to `POST /v1/run` and applies `live.stopped`. It writes the outcomes
and the summary to `--out`, prints one line per target, and checks them against the committed
baseline `evals/redteam/baseline.json` (`redteam-baseline.v0`): exit 1 when a target's block
rate drops below the recorded one, a case it stopped is no longer stopped, or a target has no
result; 2 on a bad flag or file. No key, no network beyond localhost.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import threading
import time
from collections.abc import Sequence
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any, Literal

import httpx
import uvicorn
import yaml
from chassis_contracts.redteam import load_cases
from chassis_contracts.redteam.live import (
    Evidence,
    LiveOutcome,
    check_baseline,
    model_script,
    record_baseline,
    request_body,
    stopped,
    summarize,
    tool_pages,
)
from fake_mcp_server.redteam import create_redteam_server

from bakeoff.config import DUMMY_MODEL_KEY
from bakeoff.dialects import for_engine
from bakeoff.evalrun import plan
from bakeoff.procs import Cleanup, free_port, install_signal_handlers
from bakeoff.registry import ROOT, Engine, Lane, get_engine
from bakeoff.stack import Extras, StackError, running_stack

__all__ = ["BASELINE", "CONFIGS", "main", "run_target"]

BASELINE = ROOT / "evals/redteam/baseline.json"
CONFIGS: dict[str, str] = {"litellm": "litellm", "keyword": "fake"}
"""Config name -> `spec.adapters.guardrails`."""
RUN_TIMEOUT_S = 120.0
GUARDRAIL_PREFIX = "/guardrails/"


class ThreadServer:
    """An ASGI app on 127.0.0.1 in a uvicorn thread of this process."""

    def __init__(self, app: Any, *, lifespan: Literal["on", "off"] = "on") -> None:
        self.port = free_port()
        config = uvicorn.Config(
            app, host="127.0.0.1", port=self.port, log_level="warning", lifespan=lifespan
        )
        self._server = uvicorn.Server(config)
        self._thread = threading.Thread(target=self._server.run, daemon=True)

    def __enter__(self) -> ThreadServer:
        self._thread.start()
        deadline = time.monotonic() + 15
        while not self._server.started:
            if not self._thread.is_alive() or time.monotonic() > deadline:
                raise StackError("a thread server did not start")
            time.sleep(0.02)
        return self

    def __exit__(self, *exc: object) -> None:
        self._server.should_exit = True
        self._thread.join(timeout=10)


def _with_guardrail_stub(model_app: Any) -> Any:
    """The fake model's app with LiteLLM's `/guardrails/*` in front, on one base URL."""
    from guardrail_server import PatternClassifier
    from guardrail_server import create_app as create_guard
    from guardrail_server.litellm_stub import StubSettings, create_stub

    guard = create_guard(PatternClassifier())
    stub = create_stub(
        StubSettings(master_key=DUMMY_MODEL_KEY), guardrail_transport=httpx.ASGITransport(app=guard)
    )

    async def app(scope: Any, receive: Any, send: Any) -> None:
        target = stub if str(scope.get("path", "")).startswith(GUARDRAIL_PREFIX) else model_app
        await target(scope, receive, send)

    return app


def _error_code(response: httpx.Response) -> str | None:
    try:
        error = (response.json().get("output") or {}).get("error") or {}
    except ValueError:
        return None
    code = error.get("code")
    return str(code) if code else None


async def _send(url: str, body: dict[str, Any]) -> tuple[str, str | None]:
    async with httpx.AsyncClient(base_url=url, timeout=RUN_TIMEOUT_S, trust_env=False) as client:
        response = await client.post("/v1/run", json=body)
        return response.text, _error_code(response)


def run_target(config: str, engine: Engine, lane: Lane, cleanup: Cleanup) -> list[LiveOutcome]:
    """Every red-team case on one config, engine, and lane. Raises `StackError`."""
    cases = load_cases()
    work = cleanup.tempdir()
    script = work / "redteam-model.yaml"
    script.write_text(yaml.safe_dump(for_engine(engine, model_script(cases)), sort_keys=False))
    server, calls = create_redteam_server(tool_pages(cases))
    extras = Extras(
        adapters=(("guardrails", CONFIGS[config]), ("tools", "mcp")),
        wrap=_with_guardrail_stub if config == "litellm" else None,
    )
    outcomes = []
    with ThreadServer(server.http_app(path="/mcp")) as tools:
        extras = replace(
            extras, chassis_env=(("LITELLM_MCP_URL", f"http://127.0.0.1:{tools.port}/mcp"),)
        )
        with running_stack(replace(engine, script=script), lane, cleanup, extras=extras) as stack:
            assert stack.fake is not None
            for case in cases:
                evidence = Evidence(text="", error_code=None)
                if case.kind != "workload_env":
                    mark, before = stack.fake.mark(), len(calls)
                    text, code = asyncio.run(_send(stack.url, request_body(case)))
                    evidence = Evidence(text, code, stack.fake.since(mark), calls[before:])
                outcomes.append(
                    LiveOutcome(
                        case.id,
                        case.category,
                        case.kind,
                        config,
                        engine.name,
                        lane,
                        stopped(case, evidence),
                    )
                )
    cleanup.remove_dir(work)
    return outcomes


def _csv(value: str) -> list[str]:
    return [part.strip() for part in value.split(",") if part.strip()]


def _collect(args: argparse.Namespace, cleanup: Cleanup) -> tuple[list[LiveOutcome], bool]:
    targets, skipped = plan(args.engine, args.lane)
    for name, reason in skipped.items():
        print(f"SKIP {name}: {reason}", flush=True)
    outcomes: list[LiveOutcome] = []
    broken = False
    for config in args.config:
        for name, lane in targets:
            try:
                got = run_target(config, get_engine(name), lane, cleanup)  # type: ignore[arg-type]
            except (StackError, httpx.HTTPError, ValueError) as exc:
                print(f"ERROR {config} {name} {lane}: {exc}", file=sys.stderr, flush=True)
                broken = True
                continue
            outcomes += got
            done = sum(o.stopped for o in got)
            missed = [o.case for o in got if not o.stopped]
            print(f"RAN {config} {name} {lane} {done}/{len(got)} missed: {missed}", flush=True)
    return outcomes, broken


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m bakeoff.redteam")
    parser.add_argument("--engine", type=_csv, default=["all"])
    parser.add_argument("--lane", type=_csv, default=["all"])
    parser.add_argument("--config", type=_csv, default=list(CONFIGS))
    parser.add_argument("--out", type=Path, required=True, help="the results JSON")
    parser.add_argument("--baseline", type=Path, default=BASELINE)
    parser.add_argument("--record", action="store_true", help="write the baseline instead")
    args = parser.parse_args(argv)
    if unknown := set(args.config) - set(CONFIGS):
        print(f"red team: unknown config {sorted(unknown)}", file=sys.stderr)
        return 2
    install_signal_handlers()
    outcomes, broken = _collect(args, Cleanup())
    args.out.parent.mkdir(parents=True, exist_ok=True)
    data = {"summary": summarize(outcomes), "outcomes": [asdict(o) for o in outcomes]}
    args.out.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    _, skipped = plan(args.engine, args.lane)
    if args.record:
        if broken:
            print("red team: a target did not run; not recording", file=sys.stderr)
            return 1
        baseline = record_baseline(outcomes, skipped=skipped)
        args.baseline.parent.mkdir(parents=True, exist_ok=True)
        args.baseline.write_text(json.dumps(baseline, indent=2) + "\n", encoding="utf-8")
        print(f"wrote {args.baseline}")
        return 0
    try:
        baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
        engines = None if "all" in args.engine else set(args.engine)
        lanes = None if "all" in args.lane else set(args.lane)
        results = check_baseline(baseline, outcomes, engines=engines, lanes=lanes)
    except (OSError, ValueError) as exc:
        print(f"red team: baseline {args.baseline}: {exc}", file=sys.stderr)
        return 2
    results = [r for r in results if r[0].split()[0] in args.config]
    seen = summarize(outcomes)
    for key, problems in results:
        config, target = key.split()
        rate = seen.get(config, {}).get(target, {}).get("rate")
        head = f"{'PASS' if not problems else 'FAIL'} {key} block rate {rate}"
        print(head if not problems else f"{head}: {'; '.join(problems)}")
    failed = sum(bool(p) for _, p in results)
    print(f"red team: {len(results) - failed} passed, {failed} failed")
    return 1 if failed or broken else 0


if __name__ == "__main__":
    raise SystemExit(main())
