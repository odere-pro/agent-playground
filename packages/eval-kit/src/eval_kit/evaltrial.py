"""The PoC-7 eval tool trial: one eval tool on the golden set, against one engine at a time.

    uv run python -m eval_kit.evaltrial --tool promptfoo --engine echo-python,echo-langgraph \\
        --out /tmp/evals
    uv run python -m eval_kit.evaltrial --tool deepeval --engine echo-python --offline --out ...

Per engine it starts the bake-off stack (`bakeoff.stack.running_stack`) with the golden model
script, and a second fake model server as the judge (`golden-judge.yaml`). Then it runs the tool
as a child process against the chassis's `/v1/run`, by URL only, and writes three files to
`--out`: `<tool>-<engine>.raw.json` (the tool's own report), `<tool>-<engine>.json` (the same
results as `eval-report.v0`, `eval_kit.evalreport`), and `<tool>-<engine>.log` (the tool's
output). It prints one line per engine: passed cases, wall time of the tool run.

The tool's environment is built from scratch (`bakeoff.envs.build_env`): the URLs, the tools'
telemetry opt-outs, the package caches, and, unless `--offline`, the proxy and CA variables
needed to fetch the tool. `--offline` drops those and sets `UV_OFFLINE` and `npm_config_offline`,
so the run proves the tool works with a warm cache and no network.

Each tool installs into the run's temp dir from a committed, hashed lock before the stack starts
(R7-18): DeepEval into its own venv with `uv pip install --require-hashes --no-deps -r` on
`evals/deepeval/requirements.txt`, promptfoo with `npm ci` from `evals/promptfoo/package-lock.json`.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Literal

import yaml
from bakeoff.envs import build_env, is_secret_name
from bakeoff.procs import Cleanup, free_port, install_signal_handlers
from bakeoff.registry import ROOT, Engine, get_engine
from bakeoff.stack import FakeModel, StackError, running_stack

from eval_kit.dialects import for_engine
from eval_kit.evalreport import normalize, trim_raw
from eval_kit.golden import CASES_FILE
from eval_kit.golden.generate import JUDGE_SCRIPT, MODEL_SCRIPT

__all__ = [
    "DEEPEVAL_LOCK",
    "DEEPEVAL_VERSION",
    "PROMPTFOO_DIR",
    "PROMPTFOO_VERSION",
    "TELEMETRY_OFF",
    "Tool",
    "main",
    "tool_argv",
    "tool_env",
    "tool_install_argv",
]

Tool = Literal["promptfoo", "deepeval"]
TOOLS: tuple[Tool, ...] = ("promptfoo", "deepeval")
PROMPTFOO_VERSION = "0.125.1"
DEEPEVAL_VERSION = "4.3.2"
PROMPTFOO_CONFIG = Path("evals/promptfoo/promptfooconfig.yaml")
PROMPTFOO_DIR = Path("evals/promptfoo")
"""Holds `package.json` and `package-lock.json`: promptfoo and each dependency by integrity hash."""
DEEPEVAL_LOCK = Path("evals/deepeval/requirements.txt")
"""DeepEval and every dependency, each pinned by sha256 (`requirements.in` names the inputs)."""
DEEPEVAL_TESTS = Path("evals/deepeval/test_golden.py")
TOOL_TIMEOUT_S = 900.0
_INSTALL_TAIL = 2000

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


def _deepeval_venv(work: Path) -> Path:
    return work / "deepeval-venv"


def _promptfoo_dir(work: Path) -> Path:
    """Not named `promptfoo`: npm then reads the folder as the package it must install."""
    return work / "promptfoo-tool"


def tool_install_argv(tool: Tool, work: Path) -> list[list[str]]:
    """The commands that install `tool` into `work` from its hashed lock, in order (promptfoo's run
    in its own folder, `_promptfoo_dir`)."""
    if tool == "promptfoo":
        return [["npm", "ci", "--no-audit", "--no-fund"]]
    venv = _deepeval_venv(work)
    return [
        ["uv", "venv", "--quiet", "--python", sys.executable, str(venv)],
        [
            "uv",
            "pip",
            "install",
            "--quiet",
            "--python",
            str(venv / "bin" / "python"),
            "--require-hashes",
            "--no-deps",
            "-r",
            str(ROOT / DEEPEVAL_LOCK),
        ],
    ]


def tool_argv(tool: Tool, work: Path) -> list[str]:
    if tool == "promptfoo":
        return [
            str(_promptfoo_dir(work) / "node_modules" / ".bin" / "promptfoo"),
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
        str(_deepeval_venv(work) / "bin" / "deepeval"),
        "test",
        "run",
        str(ROOT / DEEPEVAL_TESTS),
    ]


def install_tool(tool: Tool, work: Path, env: dict[str, str]) -> None:
    """Install `tool` into `work` from its lock. Raises `StackError` naming the failed step."""
    if tool == "promptfoo":
        target = _promptfoo_dir(work)
        target.mkdir()
        for name in ("package.json", "package-lock.json"):
            shutil.copyfile(ROOT / PROMPTFOO_DIR / name, target / name)
    cwd = _promptfoo_dir(work) if tool == "promptfoo" else work
    for argv in tool_install_argv(tool, work):
        done = subprocess.run(
            argv,
            env=env,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=TOOL_TIMEOUT_S,
            check=False,
        )
        if done.returncode != 0:
            tail = (done.stdout + done.stderr)[-_INSTALL_TAIL:]
            raise StackError(
                f"{tool} install failed: {' '.join(argv[:3])} exit {done.returncode}\n{tail}"
            )


def _caches() -> dict[str, str]:
    home = Path.home()
    return {
        "UV_CACHE_DIR": os.environ.get("UV_CACHE_DIR") or str(home / ".cache" / "uv"),
        # npm reads the lowercase name.
        "npm_config_cache": os.environ.get("npm_config_cache")  # noqa: SIM112
        or str(home / ".npm"),
    }


def tool_env(
    home: Path,
    work: Path,
    *,
    engine: str,
    chassis_url: str,
    judge_url: str,
    offline: bool,
    cases_file: Path = CASES_FILE,
) -> dict[str, str]:
    extra = {
        "CHASSIS_URL": chassis_url,
        "JUDGE_URL": judge_url,
        "EVAL_ENGINE": engine,
        "GOLDEN_CASES": str(cases_file),
        "DEEPEVAL_RESULTS_FOLDER": str(work / "deepeval-results"),
        **TELEMETRY_OFF,
        **_fetch_vars(offline),
    }
    return build_env(home, extra)


def _fetch_vars(offline: bool) -> dict[str, str]:
    """The package caches, and the offline switches or the proxy and CA variables."""
    if offline:
        return {**_caches(), "UV_OFFLINE": "1", "npm_config_offline": "true"}
    network = {n: os.environ[n] for n in NETWORK_VARS if n in os.environ and not is_secret_name(n)}
    return {**_caches(), **network}


def install_env(home: Path, *, offline: bool) -> dict[str, str]:
    """The environment of the install steps: telemetry off, caches, and the fetch variables."""
    return build_env(home, {**TELEMETRY_OFF, **_fetch_vars(offline)})


def _raw_report(tool: Tool, work: Path) -> Path | None:
    if tool == "promptfoo":
        path = work / "raw.json"
        return path if path.is_file() else None
    found = sorted((work / "deepeval-results").glob("**/*.json"))
    return found[-1] if found else None


@dataclass(frozen=True)
class Golden:
    """Which golden set a run uses: the cases file the tool reads, the engines' model script and
    the judge's (paths relative to the repo root, or absolute), and extra `meta` for the report."""

    cases_file: Path = CASES_FILE
    model_script: Path = MODEL_SCRIPT
    judge_script: Path = JUDGE_SCRIPT
    meta: tuple[tuple[str, str], ...] = ()


POC07 = Golden()
"""The PoC-7 trial: 20 cases, `golden.yaml`, `golden-judge.yaml`."""


def _engine_script(engine: Engine, script: Path, work: Path) -> Path:
    """`script` as `engine` reads it: the same file, or its dialect written into `work`."""
    if engine.dialect == "openai":
        return script
    data = yaml.safe_load((ROOT / script).read_text(encoding="utf-8"))
    path = work / f"model-{engine.dialect}.yaml"
    path.write_text(yaml.safe_dump(for_engine(engine, data), sort_keys=False), encoding="utf-8")
    return path


@dataclass(frozen=True)
class _ToolRun:
    """What one tool run against a live stack left: the process result and its counts."""

    done: subprocess.CompletedProcess[str]
    stack_s: float
    wall_s: float
    model_calls: int
    judge_calls: int


def _run_tool(
    tool: Tool,
    engine: Engine,
    lane: str,
    work: Path,
    *,
    offline: bool,
    cleanup: Cleanup,
    golden: Golden,
) -> _ToolRun:
    """Install `tool`, start the judge and the stack, run `tool` against them, and stop both."""
    install_tool(tool, work, install_env(work / "home", offline=offline))
    judge = FakeModel(ROOT / golden.judge_script, free_port())
    judge.start()
    try:
        started = time.perf_counter()
        with running_stack(engine, lane, cleanup) as stack:  # type: ignore[arg-type]
            stack_s = time.perf_counter() - started
            env = tool_env(
                work / "home",
                work,
                engine=engine.name,
                chassis_url=stack.url,
                judge_url=f"http://127.0.0.1:{judge.port}/v1",
                offline=offline,
                cases_file=golden.cases_file,
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
    return _ToolRun(done, stack_s, wall_s, model_calls, judge_calls)


def _write_report(
    tool: Tool,
    engine_name: str,
    lane: str,
    out: Path,
    work: Path,
    *,
    stem: str,
    run: _ToolRun,
    meta: dict[str, object],
) -> dict[str, Any]:
    """Write the log, the trimmed raw report, and the normalized report; return the report."""
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{stem}.log").write_text(run.done.stdout + run.done.stderr, encoding="utf-8")
    raw = _raw_report(tool, work)
    if raw is None:
        raise StackError(f"{tool} wrote no report (exit {run.done.returncode}); see {stem}.log")
    raw_data = json.loads(raw.read_text(encoding="utf-8"))
    report = normalize(tool, engine_name, raw_data)
    trimmed = trim_raw(tool, raw_data, str(ROOT))
    (out / f"{stem}.raw.json").write_text(json.dumps(trimmed, indent=1) + "\n", encoding="utf-8")
    report["meta"] = {
        "lane": lane,
        "tool_exit": run.done.returncode,
        "tool_wall_s": round(run.wall_s, 2),
        "stack_start_s": round(run.stack_s, 2),
        "model_calls": run.model_calls,
        "judge_calls": run.judge_calls,
        **meta,
    }
    (out / f"{stem}.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


def run_one(
    tool: Tool,
    engine_name: str,
    lane: str,
    out: Path,
    *,
    offline: bool,
    cleanup: Cleanup,
    golden: Golden = POC07,
    stem: str | None = None,
) -> dict[str, object]:
    """One tool against one engine: the summary line's data. Raises `StackError`."""
    work = cleanup.tempdir()
    (work / "home").mkdir()
    base = get_engine(engine_name)
    engine = replace(base, script=_engine_script(base, golden.model_script, work))
    meta: dict[str, object] = {"offline": offline, **dict(golden.meta)}
    stem = stem or f"{tool}-{engine_name}"
    try:
        run = _run_tool(tool, engine, lane, work, offline=offline, cleanup=cleanup, golden=golden)
        report = _write_report(tool, engine_name, lane, out, work, stem=stem, run=run, meta=meta)
    finally:
        cleanup.remove_dir(work)
    summary = report["summary"]
    assert isinstance(summary, dict)
    return {"engine": engine_name, "tool": tool, **summary, **report["meta"]}


def _csv(value: str) -> list[str]:
    return [part.strip() for part in value.split(",") if part.strip()]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m eval_kit.evaltrial")
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
