"""PoC-6c exit criteria, read from the recorded run. Offline: these tests only read files.

The evidence comes from the `poc06-slm` CI job (`.github/workflows/poc06-slm.yml`), which runs
`scripts/poc06_mac.sh --only slm --only scale`. Its `write_note` writes, in `notes/` here:

- `YYYY-MM-DD-slm-run.md` and `.results.json`: `bakeoff run --route local-small` (the JSON is
  `bakeoff.report.render_json`: `meta` and one row per engine and lane).
- `YYYY-MM-DD-scale-run.md` and `.results.json`: `deploy/compose/poc06/load_driver.py`, the PoC-4
  `run_matrix.py` format (`engines.<engine>.<pairs>` with Locust numbers).

and `deploy/compose/poc06/model.sha256` (the model file pin). The newest dated note wins. Until a
note is committed, its tests skip ("waits on the poc06-slm CI run").

On an SLM a failed task is a result, not a test failure: these tests check that every cell ran
and its numbers are recorded, never that a task passed. The `*_fixture_*` tests run the readers on
output of the real `bakeoff.report.render_json` and `run_matrix.merge`, so the readers are checked
before the real run exists.
"""

from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from bakeoff.measure import TaskResult
from bakeoff.registry import ENGINES, lanes_for
from bakeoff.report import render_json
from bakeoff.runner import Cell

ROOT = Path(__file__).resolve().parents[3]
NOTES = ROOT / "pocs" / "poc-06c-pretrained-slm" / "notes"
POC06 = ROOT / "deploy" / "compose" / "poc06"
PIN_FILE = POC06 / "model.sha256"
DRIVER = POC06 / "load_driver.py"
WORKFLOW = ROOT / ".github" / "workflows" / "poc06-slm.yml"
ADR = ROOT / "docs" / "planning" / "adr" / "006-agent-engines-default-supported-lanes.md"

ROUTE = "local-small"
GGUF_FILE = "Qwen3-1.7B-Q8_0.gguf"
TASKS = ("smoke", "simplifier", "lookup")
RUN_LANES = ("inprocess", "sidecar")  # step_slm: `--lane inprocess,sidecar`
SCALE_ENGINES = ("echo-python", "echo-openai-agents")  # step_scale: SCALE_ENGINES
SCALE_PAIRS = (1, 2, 4)
PLACEHOLDER = "To be written after review"
WAITS = "waits on the poc06-slm CI run"
SHA256 = re.compile(r"^[0-9a-f]{64}$")
COMMIT = re.compile(r"^[0-9a-f]{40}$")

TRUSTED = tuple(e.name for e in ENGINES if e.trusted)
EXPECTED_CELLS = frozenset(
    (e.name, lane) for e in ENGINES if e.trusted for lane in lanes_for(e) if lane in RUN_LANES
)
"""The registry's lane rule: echo-typescript (a node engine) has no inprocess cell."""


# ---- readers ----------------------------------------------------------------------------------


def newest_note(stem: str) -> Path | None:
    """The newest `YYYY-MM-DD-<stem>.md` in notes/, or None."""
    found = sorted(NOTES.glob(f"[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]-{stem}.md"))
    return found[-1] if found else None


def results_of(note: Path) -> Path:
    return note.with_name(note.name.removesuffix(".md") + ".results.json")


def section(text: str, heading: str) -> str | None:
    """The body under the first `## <heading...>` line, up to the next `## `, or None."""
    match = re.search(rf"^## {re.escape(heading)}[^\n]*\n(.*?)(?=^## |\Z)", text, re.M | re.S)
    return match.group(1).strip() if match else None


def setup_value(text: str, label: str) -> str | None:
    """The value of `- <label>: <value>` in the note's Setup section (`env_block`)."""
    setup = section(text, "Setup") or ""
    match = re.search(rf"^- {re.escape(label)}: (.*)$", setup, re.M)
    return match.group(1).strip() if match else None


def bakeoff_problems(data: dict[str, Any]) -> list[str]:
    """Criterion 1: the route is local-small, every trusted engine has every task in every lane
    it supports, and every cell ran (status ok, not SKIP or FAIL to start)."""
    problems: list[str] = []
    meta = data.get("meta", {})
    if meta.get("route") != ROUTE:
        problems.append(f"meta.route is {meta.get('route')!r}, not {ROUTE!r}")
    if meta.get("hosted") is not True:
        problems.append("meta.hosted is not true: the run used the fake model")
    if ROUTE not in str(meta.get("model", "")):
        problems.append(f"meta.model {meta.get('model')!r} does not name {ROUTE}")
    missing_tasks = [t for t in TASKS if t not in meta.get("tasks", [])]
    if missing_tasks:
        problems.append(f"meta.tasks lacks {missing_tasks}")
    cells = {(c["engine"], c["lane"]): c for c in data.get("cells", [])}
    problems += [f"no cell for {e} {lane}" for e, lane in sorted(EXPECTED_CELLS - cells.keys())]
    for key in sorted(EXPECTED_CELLS & cells.keys()):
        cell = cells[key]
        if cell["status"] != "ok":
            problems.append(f"{key[0]} {key[1]} did not run: {cell['status']} {cell['reason']}")
            continue
        for task in TASKS:
            result = cell["tasks"].get(task)
            if result is None:
                problems.append(f"{key[0]} {key[1]} has no {task} result")
            elif result["runs"] != meta.get("repeat") or result["runs"] < 1:
                problems.append(f"{key[0]} {key[1]} {task}: {result['runs']} runs")
    return problems


def metric_problems(data: dict[str, Any]) -> list[str]:
    """Criterion 2: for every cell that ran, success and tool-call counts, latency p50 and p95,
    and the token means are recorded. A task with no completed run (every request failed at the
    HTTP level) has no latency to record; then its failures must hold the reason instead."""
    problems: list[str] = []
    for cell in data.get("cells", []):
        if cell["status"] != "ok":
            continue
        where = f"{cell['engine']} {cell['lane']}"
        for task, r in cell["tasks"].items():
            if not isinstance(r.get("passed"), int) or r.get("pass_rate") is None:
                problems.append(f"{where} {task}: no task success count")
            if task == "lookup" and r.get("tool_call_pass_rate") is None:
                problems.append(f"{where} lookup: no tool-call success")
            numbers = ("latency_p50_ms", "latency_p95_ms", "input_tokens", "output_tokens")
            absent = [k for k in numbers if r.get(k) is None]
            if absent and not (len(absent) == len(numbers) and r["failures"]):
                problems.append(f"{where} {task}: {', '.join(absent)} null")
    return problems


def pass_counts(data: dict[str, Any]) -> dict[str, str]:
    """`engine lane task` -> `passed/runs`: recorded, never gated on."""
    return {
        f"{c['engine']} {c['lane']} {task}": f"{r['passed']}/{r['runs']}"
        for c in data.get("cells", [])
        for task, r in c["tasks"].items()
    }


def scale_problems(data: dict[str, Any]) -> list[str]:
    """Criterion 4: every scale engine has a row for 1, 2, and 4 pairs with requests > 0."""
    problems: list[str] = []
    engines = data.get("engines", {})
    for engine in SCALE_ENGINES:
        for pairs in SCALE_PAIRS:
            row = engines.get(engine, {}).get(str(pairs))
            if row is None:
                problems.append(f"no scale row for {engine} at {pairs} pairs")
            elif not row.get("requests", 0) > 0:
                problems.append(f"{engine} at {pairs} pairs: {row.get('requests')} requests")
            elif any(row.get(k) is None for k in ("rps", "ok_rps", "p50_ms", "p95_ms")):
                problems.append(f"{engine} at {pairs} pairs: a number is missing")
    return problems


def need(stem: str) -> tuple[Path, str]:
    """The newest note and its text, or skip."""
    note = newest_note(stem)
    if note is None:
        pytest.skip(f"no notes/*-{stem}.md yet: {WAITS}")
    return note, note.read_text(encoding="utf-8")


def need_results(stem: str) -> dict[str, Any]:
    note, _ = need(stem)
    path = results_of(note)
    assert path.is_file(), f"{note.name} exists but {path.name} does not"
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return data


# ---- criterion 1: every engine runs both tasks on the SLM -------------------------------------


def test_every_trusted_engine_ran_every_task_on_local_small() -> None:
    """Exit criterion 1: every PoC-6a engine runs both tasks on the pre-trained SLM through the
    chassis model proxy and LiteLLM, and only `spec.model.route` changed.

    Checks the slm-run results: `meta.route` is `local-small`, each trusted engine has a cell in
    each lane it supports (inprocess and sidecar; echo-typescript sidecar only), and each cell ran
    smoke, simplifier, and lookup. Pass counts are printed, not gated: an SLM may fail tasks.
    """
    data = need_results("slm-run")
    assert bakeoff_problems(data) == []
    for key, count in sorted(pass_counts(data).items()):
        print(f"{key}: {count}")


# ---- criterion 2: the numbers, next to the big model ------------------------------------------


def test_every_cell_that_ran_records_success_tool_calls_tokens_and_latency() -> None:
    """Exit criterion 2: per engine, task success, tool-call success, the share of runs that
    would need a fallback (1 - pass rate), token overhead, and latency are recorded on the SLM.

    Checks the slm-run results for non-null pass counts, the lookup tool-call rate, latency p50
    and p95, and the token means in every cell that ran.
    """
    data = need_results("slm-run")
    assert metric_problems(data) == []


def test_the_slm_numbers_sit_next_to_the_big_model_numbers() -> None:
    """Exit criterion 2, second half: the SLM numbers next to the big-model numbers from 6a."""
    pytest.skip("hosted big-model numbers come from the Mac run (`make poc06-mac`, step hosted)")


# ---- criterion 3: request features ------------------------------------------------------------


def test_the_slm_note_lists_the_request_features_per_engine() -> None:
    """Exit criterion 3: per engine, the request features the SLM route rejects or ignores are
    listed. The slm-run note gains a `## Request features` section during review."""
    _, text = need("slm-run")
    body = section(text, "Request features")
    assert body, "the slm-run note has no non-empty `## Request features` section"
    missing = [e for e in TRUSTED if e not in body]
    assert missing == [], f"`## Request features` does not name {missing}"


# ---- criterion 4: scale -----------------------------------------------------------------------


def test_the_scale_run_has_every_pair_count_for_every_scale_engine() -> None:
    """Exit criterion 4: the scale run shows where throughput stops growing as 1, 2, and 4 agent
    pairs share one model server. Checks the scale-run results have a row with requests > 0
    for each scale engine and pair count."""
    data = need_results("scale-run")
    assert scale_problems(data) == []


def test_the_scale_note_reads_the_limit() -> None:
    """Exit criterion 4: ... and what limits it. The scale-run note's `## Reading` is written
    after review: not empty and not the script's placeholder."""
    _, text = need("scale-run")
    reading = section(text, "Reading")
    assert reading, "the scale-run note has no `## Reading` section"
    assert PLACEHOLDER not in reading, "`## Reading` is still the placeholder"


# ---- criterion 5: pins ------------------------------------------------------------------------


def read_pin() -> tuple[str, str]:
    if not PIN_FILE.is_file():
        pytest.skip(f"no deploy/compose/poc06/model.sha256 yet: {WAITS}")
    fields = PIN_FILE.read_text(encoding="utf-8").split()
    assert len(fields) == 2, f"model.sha256 is not `<sha256>  <file>`: {fields}"
    return fields[0], fields[1]


def test_the_model_file_is_pinned_by_sha256() -> None:
    """Exit criterion 5: the model and its file hash are pinned. model.sha256 holds a 64-hex
    sha256 and the file name Qwen3-1.7B-Q8_0.gguf."""
    sha, name = read_pin()
    assert SHA256.match(sha), sha
    assert name == GGUF_FILE


def test_the_slm_note_records_the_hash_runtime_and_flags() -> None:
    """Exit criterion 5: the file hash, the runtime version, and the server flags are recorded.
    The slm-run note's Setup has the pinned GGUF sha256, a checked llama.cpp version, and the
    llama-server flags with thinking off."""
    _, text = need("slm-run")
    sha, _ = read_pin()
    assert setup_value(text, "GGUF sha256") == sha
    version = setup_value(text, "llama.cpp")
    assert version, "Setup has no `- llama.cpp:` line"
    assert version not in ("not checked", "unknown"), version
    flags = setup_value(text, "llama-server flags")
    assert flags, "Setup has no `- llama-server flags:` line"
    assert 'enable_thinking":false' in flags, flags


def workflow_env(name: str) -> str:
    match = re.search(rf"^\s+{name}:\s*(.*)$", WORKFLOW.read_text(encoding="utf-8"), re.M)
    assert match, f"{name} is not in {WORKFLOW.name}"
    return match.group(1).split("#", 1)[0].strip().strip("\"'")


def test_the_ci_job_pins_llama_cpp_and_the_model_revision() -> None:
    """Exit criterion 5: the runtime and the model are pinned, so every run can be repeated.
    poc06-slm.yml sets LLAMA_CPP_TAG, a 64-hex LLAMA_CPP_SHA256, and POC06_GGUF_REV as a 40-hex
    commit (not `main`). The CI run prints the values to pin; checked once its note is in."""
    need("slm-run")
    assert workflow_env("LLAMA_CPP_TAG"), "LLAMA_CPP_TAG is empty"
    assert SHA256.match(workflow_env("LLAMA_CPP_SHA256")), "LLAMA_CPP_SHA256 is not a sha256"
    rev = workflow_env("POC06_GGUF_REV")
    assert COMMIT.match(rev), f"POC06_GGUF_REV is {rev!r}, not a commit"


# ---- criterion 6: the ADR ---------------------------------------------------------------------


def test_the_bakeoff_adr_has_an_slm_section() -> None:
    """Exit criterion 6: the bake-off ADR (ADR-006) has an SLM section. It is written after the
    SLM run, so this waits on the slm-run note."""
    need("slm-run")
    headings = re.findall(r"^#{2,} .*$", ADR.read_text(encoding="utf-8"), re.M)
    assert any("SLM" in h for h in headings), headings


# ---- the readers on real bakeoff and run_matrix output ----------------------------------------


def task_result(name: str, passed: int, runs: int = 2) -> TaskResult:
    result = TaskResult(name, runs=runs, passed=passed, tools_passed=passed)
    result.latencies_ms = [100.0, 300.0]
    result.ttft_ms = [50.0]
    result.input_tokens = [120.0, 130.0]
    result.output_tokens = [10.0, 12.0]
    if passed < runs:
        result.failures = ["lookup: no tool call"]
    return result


def fixture_results(tmp_path: Path) -> dict[str, Any]:
    """What `bakeoff run --route local-small --repeat 2` writes, from the real `render_json`."""
    cells = [
        Cell(engine, lane, "ok", tasks={t: task_result(t, passed=1) for t in TASKS})
        for engine, lane in sorted(EXPECTED_CELLS)
    ]
    meta = {
        "tasks": list(TASKS),
        "repeat": 2,
        "model": "hosted via LiteLLM at http://127.0.0.1:14000, route local-small",
        "generated": "2026-10-28T00:00:00Z",
        "target_mode": False,
        "hosted": True,
        "route": ROUTE,
    }
    path = tmp_path / "2026-10-28-slm-run.results.json"
    path.write_text(render_json(cells, meta), encoding="utf-8")
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return data


def test_the_readers_accept_real_bakeoff_output_with_failed_tasks(tmp_path: Path) -> None:
    """The criterion 1 and 2 readers pass a full local-small run in which half the tasks fail."""
    data = fixture_results(tmp_path)
    assert {(c["engine"], c["lane"]) for c in data["cells"]} == EXPECTED_CELLS
    assert ("echo-typescript", "inprocess") not in EXPECTED_CELLS
    assert ("echo-typescript", "sidecar") in EXPECTED_CELLS
    assert bakeoff_problems(data) == []
    assert metric_problems(data) == []
    assert set(pass_counts(data).values()) == {"1/2"}


def test_the_readers_reject_a_missing_cell_a_skip_a_wrong_route_and_null_latency(
    tmp_path: Path,
) -> None:
    """Each break of the criterion 1 and 2 evidence gives a problem."""
    data = fixture_results(tmp_path)
    data["meta"]["route"] = "big-default"
    data["cells"] = [c for c in data["cells"] if c["engine"] != "echo-typescript"]
    first = data["cells"][0]
    first["status"], first["reason"] = "skip", "echo-python is not built"
    second = data["cells"][1]
    del second["tasks"]["lookup"]
    third = data["cells"][2]
    third["tasks"]["smoke"]["latency_p95_ms"] = None
    third["tasks"]["lookup"]["tool_call_pass_rate"] = None
    problems = bakeoff_problems(data)
    assert any("meta.route" in p for p in problems)
    assert "no cell for echo-typescript sidecar" in problems
    assert any("did not run: skip" in p for p in problems)
    assert any("has no lookup result" in p for p in problems)
    numbers = metric_problems(data)
    assert any("latency_p95_ms null" in p for p in numbers)
    assert any("no tool-call success" in p for p in numbers)


def test_a_task_with_no_completed_run_needs_its_failure_reason(tmp_path: Path) -> None:
    """A task whose every request failed has null numbers; it passes only with its reasons."""
    data = fixture_results(tmp_path)
    task = data["cells"][0]["tasks"]["lookup"]
    for key in ("latency_p50_ms", "latency_p95_ms", "input_tokens", "output_tokens"):
        task[key] = None
    assert metric_problems(data) == []
    task["failures"] = []
    assert metric_problems(data) != []


def load_matrix() -> ModuleType:
    spec = importlib.util.spec_from_file_location("poc06c_load_driver", DRIVER)
    assert spec is not None and spec.loader is not None
    driver = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(driver)
    matrix: ModuleType = driver.load_matrix()
    return matrix


def test_the_scale_reader_accepts_real_run_matrix_output() -> None:
    """The criterion 4 reader passes results built by the PoC-4 `run_matrix.merge`, and fails
    one with a pair count missing or with no requests."""
    matrix = load_matrix()
    results: dict[str, Any] = {"version": 1}
    for engine in SCALE_ENGINES:
        for pairs in SCALE_PAIRS:
            s = matrix.Scenario(f"main-{engine}-{pairs}", "main", engine, pairs, 16)
            row = {"requests": 40, "failures": 2, "rps": 0.7, "p50_ms": 9000.0, "p95_ms": 21000.0}
            row["ok_rps"] = matrix.ok_rps(40, 2, 0.7)
            matrix.merge(results, s, row)
    data: dict[str, Any] = json.loads(json.dumps(results))
    assert scale_problems(data) == []
    data["engines"]["echo-python"]["4"]["requests"] = 0
    del data["engines"]["echo-openai-agents"]["2"]
    assert scale_problems(data) == [
        "echo-python at 4 pairs: 0 requests",
        "no scale row for echo-openai-agents at 2 pairs",
    ]


def test_the_note_readers_parse_write_note_output() -> None:
    """`section` and `setup_value` read the layout `write_note` and `env_block` produce."""
    text = (
        "# PoC-6c pre-trained SLM run, 2026-10-28\n\n## Why\n\nx\n\n## Setup\n\n"
        "- GGUF sha256: " + "a" * 64 + "\n- llama.cpp: version: 6500 (abc1234)\n"
        "- llama-server flags: `--jinja --chat-template-kwargs "
        '{"enable_thinking":false} -c 16384`\n\n## Reading\n\n' + PLACEHOLDER + ": x.\n"
    )
    assert setup_value(text, "GGUF sha256") == "a" * 64
    assert setup_value(text, "llama.cpp") == "version: 6500 (abc1234)"
    assert 'enable_thinking":false' in (setup_value(text, "llama-server flags") or "")
    assert PLACEHOLDER in (section(text, "Reading") or "")
    assert section(text, "Request features") is None
