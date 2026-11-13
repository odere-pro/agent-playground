"""The PoC-8 eval run's plan: which engine and lane get a DeepEval run, and with which files.
No process is started here (`test_eval_kit_evalrun_network.py` starts one)."""

from __future__ import annotations

import json
from pathlib import Path

import yaml
from bakeoff.registry import get_engine
from eval_kit.evalgate import BASELINE, load_baseline
from eval_kit.evalrun import gate_golden, plan, report_stem
from eval_kit.golden import CASES_FILE, GATE_SIZE
from eval_kit.golden.generate import GATE_DROP_FACTS_SCRIPT, GATE_JUDGE_SCRIPT, GATE_MODEL_SCRIPT


def test_eval_kit_evalrun_plans_every_lane_each_engine_may_use() -> None:
    targets, skipped = plan(["all"], ["all"])
    assert ("echo-python", "inprocess") in targets
    assert ("echo-openai-agents", "remote") in targets
    assert ("echo-smolagents", "remote") in targets
    assert ("echo-smolagents", "sidecar") not in targets, "untrusted runs remote only"
    assert ("echo-claude-agent", "inprocess") not in targets
    assert "kagent-adk" in skipped
    if get_engine("echo-typescript").skip_reason():
        assert "echo-typescript" in skipped
    else:
        assert ("echo-typescript", "inprocess") not in targets
        assert ("echo-typescript", "sidecar") in targets


def test_eval_kit_evalrun_lane_filter() -> None:
    targets, _ = plan(["echo-python", "echo-langgraph"], ["sidecar"])
    assert targets == [("echo-python", "sidecar"), ("echo-langgraph", "sidecar")]


def test_eval_kit_evalrun_gate_golden_per_logic(tmp_path: Path) -> None:
    normal = gate_golden("normal", tmp_path)
    dropped = gate_golden("drop-facts", tmp_path)
    assert normal.model_script == GATE_MODEL_SCRIPT
    assert dropped.model_script == GATE_DROP_FACTS_SCRIPT
    assert normal.judge_script == dropped.judge_script == GATE_JUDGE_SCRIPT
    assert dict(dropped.meta) == {"logic": "drop-facts", "golden": "gate"}
    data = yaml.safe_load(normal.cases_file.read_text())
    assert len(data["cases"]) == GATE_SIZE


def test_eval_kit_evalrun_gate_golden_runs_the_dataset_named_in_the_baseline(
    tmp_path: Path,
) -> None:
    """The run's input follows the baseline's `dataset`, so the eval run and the gate read the
    same cases with no code change (037 M-2)."""
    rows = yaml.safe_load(CASES_FILE.read_text(encoding="utf-8"))["cases"][:3]
    cases = tmp_path / "cases-v2.yaml"
    cases.write_text(yaml.safe_dump({"version": 1, "cases": rows}), encoding="utf-8")
    baseline = tmp_path / "baseline.json"
    baseline.write_text(
        json.dumps({**load_baseline(BASELINE), "dataset": [str(cases)], "cases": 3}),
        encoding="utf-8",
    )
    work = tmp_path / "work"
    work.mkdir()
    golden = gate_golden("normal", work, baseline=baseline)
    data = yaml.safe_load(golden.cases_file.read_text(encoding="utf-8"))
    assert [c["id"] for c in data["cases"]] == ["g01", "g02", "g03"]


def test_eval_kit_evalrun_report_stem_names_engine_lane_and_logic() -> None:
    assert report_stem("echo-python", "sidecar", "normal") == "deepeval-echo-python-sidecar"
    assert (
        report_stem("echo-python", "sidecar", "drop-facts")
        == "deepeval-echo-python-sidecar-drop-facts"
    )
