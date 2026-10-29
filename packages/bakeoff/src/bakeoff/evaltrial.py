"""The PoC-7 eval tool trial: one eval tool on the golden set, against one engine at a time.

    uv run python -m bakeoff.evaltrial --tool promptfoo --engine echo-python,echo-langgraph \\
        --out /tmp/evals
    uv run python -m bakeoff.evaltrial --tool deepeval --engine echo-python --offline --out ...

Per engine it starts the bake-off stack (`bakeoff.stack.running_stack`) with the golden model
script, and a second fake model server as the judge (`golden-judge.yaml`). Then it runs the tool
as a child process against the chassis's `/v1/run`, by URL only, and writes three files to
`--out`: `<tool>-<engine>.raw.json` (the tool's own report), `<tool>-<engine>.json` (the same
results as `eval-report.v0`, `bakeoff.evalreport`), and `<tool>-<engine>.log` (the tool's
output). It prints one line per engine: passed cases, wall time of the tool run.

The tool's environment is built from scratch (`bakeoff.envs.build_env`): the URLs, the tools'
telemetry opt-outs, the package caches, and, unless `--offline`, the proxy and CA variables
needed to fetch the tool. `--offline` drops those and sets `UV_OFFLINE` and `npm_config_offline`,
so the run proves the tool works with a warm cache and no network.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path
from typing import Literal

from bakeoff.envs import build_env, is_secret_name
from bakeoff.evalreport import normalize, trim_raw
from bakeoff.golden import CASES_FILE
from bakeoff.golden.generate import JUDGE_SCRIPT, MODEL_SCRIPT
from bakeoff.procs import Cleanup, free_port, install_signal_handlers
from bakeoff.registry import ROOT, get_engine
from bakeoff.stack import FakeModel, StackError, running_stack

__all__ = [
    "DEEPEVAL_VERSION",
    "PROMPTFOO_VERSION",
    "TELEMETRY_OFF",
    "Tool",
    "main",
    "tool_argv",
    "tool_env",
]

Tool = Literal["promptfoo", "deepeval"]
TOOLS: tuple[Tool, ...] = ("promptfoo", "deepeval")
PROMPTFOO_VERSION = "0.125.1"
DEEPEVAL_VERSION = "4.3.2"
PROMPTFOO_CONFIG = Path("evals/promptfoo/promptfooconfig.yaml")
DEEPEVAL_TESTS = Path("evals/deepeval/test_golden.py")
TOOL_TIMEOUT_S = 900.0

TELEMETRY_OFF: dict[str, str] = {
    # promptfoo: usage telemetry, the update check, and result sharing.
    "PROMPTFOO_DISABLE_TELEMETRY": "1",
    "PROMPTFOO_DISABLE_UPDATE": "1",
    "PROMPTFOO_DISABLE_SHARING": "1",
    "PROMPTFOO_DISABLE_REMOTE_GENERATION": "1",
    # DeepEval: deepeval/telemetry/__init__.py, "Opt out with DEEPEVAL_TELEMETRY_OPT_OUT=1".
    "DEEPEVAL_TELEMETRY_OPT_OUT": "1",
    "DEEPEVAL_UPDATE_WARNING_OPT_IN": "0",
}
"""Every telemetry and phone-home switch the trial found in each tool's installed source."""

NETWORK_VARS = (
    "HTTPS_PROXY",
    "HTTP_PROXY",
    "NO_PROXY",
    "https_proxy",
    "http_proxy",
    "no_proxy",
    "NODE_EXTRA_CA_CERTS",
    "SSL_CERT_FILE",
    "REQUESTS_CA_BUNDLE",
    "npm_config_https_proxy",
    "npm_config_noproxy",
    "NPM_CONFIG_USERCONFIG",
    "UV_NATIVE_TLS",
    "UV_SYSTEM_CERTS",
    "UV_INDEX_URL",
    "UV_DEFAULT_INDEX",
)
"""Copied from this process only to fetch the tool (never with `--offline`). No secret names."""


def tool_argv(tool: Tool, work: Path) -> list[str]:
    if tool == "promptfoo":
        return [
            "npx",
            "-y",
            f"promptfoo@{PROMPTFOO_VERSION}",
            "eval",
            "-c",
            str(ROOT / PROMPTFOO_CONFIG),
            "-o",
            str(work / "raw.json"),
            "--no-cache",
            "--no-write",
            "--no-table",
            "--no-progress-bar",
        ]
    return [
        "uv",
        "run",
        "--no-project",
        "--python",
        sys.executable,
        "--with",
        f"deepeval=={DEEPEVAL_VERSION}",
        "--with",
        "httpx",
        "--with",
        "pyyaml",
        "deepeval",
        "test",
        "run",
        str(ROOT / DEEPEVAL_TESTS),
    ]


def _caches() -> dict[str, str]:
    home = Path.home()
    return {
        "UV_CACHE_DIR": os.environ.get("UV_CACHE_DIR") or str(home / ".cache" / "uv"),
        # npm reads the lowercase name.
        "npm_config_cache": os.environ.get("npm_config_cache")  # noqa: SIM112
        or str(home / ".npm"),
    }


def tool_env(
    home: Path, work: Path, *, engine: str, chassis_url: str, judge_url: str, offline: bool
) -> dict[str, str]:
    extra = {
        "CHASSIS_URL": chassis_url,
        "JUDGE_URL": judge_url,
        "EVAL_ENGINE": engine,
        "GOLDEN_CASES": str(CASES_FILE),
        "DEEPEVAL_RESULTS_FOLDER": str(work / "deepeval-results"),
        **TELEMETRY_OFF,
        **_caches(),
    }
    if offline:
        extra |= {"UV_OFFLINE": "1", "npm_config_offline": "true"}
    else:
        extra |= {
            name: os.environ[name]
            for name in NETWORK_VARS
            if name in os.environ and not is_secret_name(name)
        }
    return build_env(home, extra)


def _raw_report(tool: Tool, work: Path) -> Path | None:
    if tool == "promptfoo":
        path = work / "raw.json"
        return path if path.is_file() else None
    found = sorted((work / "deepeval-results").glob("**/*.json"))
    return found[-1] if found else None


def run_one(
    tool: Tool, engine_name: str, lane: str, out: Path, *, offline: bool, cleanup: Cleanup
) -> dict[str, object]:
    """One tool against one engine: the summary line's data. Raises `StackError`."""
    engine = replace(get_engine(engine_name), script=MODEL_SCRIPT)
    work = cleanup.tempdir()
    home = work / "home"
    home.mkdir()
    judge = FakeModel(ROOT / JUDGE_SCRIPT, free_port())
    judge.start()
    try:
        started = time.perf_counter()
        with running_stack(engine, lane, cleanup) as stack:  # type: ignore[arg-type]
            stack_s = time.perf_counter() - started
            env = tool_env(
                home,
                work,
                engine=engine_name,
                chassis_url=stack.url,
                judge_url=f"http://127.0.0.1:{judge.port}/v1",
                offline=offline,
            )
            began = time.perf_counter()
            done = subprocess.run(
                tool_argv(tool, work),
                env=env,
                cwd=work,
                capture_output=True,
                text=True,
                timeout=TOOL_TIMEOUT_S,
                check=False,
            )
            wall_s = time.perf_counter() - began
            model_calls = stack.fake.calls_total if stack.fake else 0
        judge_calls = judge.calls_total
    finally:
        judge.stop()
    stem = f"{tool}-{engine_name}"
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{stem}.log").write_text(done.stdout + done.stderr, encoding="utf-8")
    raw = _raw_report(tool, work)
    if raw is None:
        cleanup.remove_dir(work)
        raise StackError(f"{tool} wrote no report (exit {done.returncode}); see {stem}.log")
    raw_data = json.loads(raw.read_text(encoding="utf-8"))
    report = normalize(tool, engine_name, raw_data)
    trimmed = trim_raw(tool, raw_data, str(ROOT))
    (out / f"{stem}.raw.json").write_text(json.dumps(trimmed, indent=1) + "\n", encoding="utf-8")
    report["meta"] = {
        "lane": lane,
        "tool_exit": done.returncode,
        "tool_wall_s": round(wall_s, 2),
        "stack_start_s": round(stack_s, 2),
        "model_calls": model_calls,
        "judge_calls": judge_calls,
        "offline": offline,
    }
    (out / f"{stem}.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    cleanup.remove_dir(work)
    summary = report["summary"]
    assert isinstance(summary, dict)
    return {"engine": engine_name, "tool": tool, **summary, **report["meta"]}


def _csv(value: str) -> list[str]:
    return [part.strip() for part in value.split(",") if part.strip()]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m bakeoff.evaltrial")
    parser.add_argument("--tool", choices=TOOLS, required=True)
    parser.add_argument("--engine", type=_csv, default=["echo-python", "echo-langgraph"])
    parser.add_argument("--lane", default="sidecar")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--offline", action="store_true", help="no proxy, warm caches only")
    args = parser.parse_args(argv)
    install_signal_handlers()
    cleanup = Cleanup()
    failed = False
    for engine in args.engine:
        try:
            row = run_one(
                args.tool, engine, args.lane, args.out, offline=args.offline, cleanup=cleanup
            )
        except (StackError, subprocess.TimeoutExpired, ValueError) as exc:
            print(f"FAIL {args.tool} {engine}: {exc}", file=sys.stderr)
            failed = True
            continue
        print(json.dumps(row), flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
