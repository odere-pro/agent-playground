"""The PoC-8 eval run's plan: which engine and lane get a DeepEval run, and with which files.
No process is started here (`test_bakeoff_evalrun_network.py` starts one)."""

from __future__ import annotations

from pathlib import Path

import yaml
from bakeoff.evalrun import gate_golden, plan, report_stem
from bakeoff.golden import GATE_SIZE
from bakeoff.golden.generate import GATE_DROP_FACTS_SCRIPT, GATE_JUDGE_SCRIPT, GATE_MODEL_SCRIPT
from bakeoff.registry import get_engine


def test_bakeoff_evalrun_plans_every_lane_each_engine_may_use() -> None:
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


def test_bakeoff_evalrun_lane_filter() -> None:
    targets, _ = plan(["echo-python", "echo-langgraph"], ["sidecar"])
    assert targets == [("echo-python", "sidecar"), ("echo-langgraph", "sidecar")]


def test_bakeoff_evalrun_gate_golden_per_logic(tmp_path: Path) -> None:
    normal = gate_golden("normal", tmp_path)
    dropped = gate_golden("drop-facts", tmp_path)
    assert normal.model_script == GATE_MODEL_SCRIPT
    assert dropped.model_script == GATE_DROP_FACTS_SCRIPT
    assert normal.judge_script == dropped.judge_script == GATE_JUDGE_SCRIPT
    assert dict(dropped.meta) == {"logic": "drop-facts", "golden": "gate"}
    data = yaml.safe_load(normal.cases_file.read_text())
    assert len(data["cases"]) == GATE_SIZE


def test_bakeoff_evalrun_report_stem_names_engine_lane_and_logic() -> None:
    assert report_stem("echo-python", "sidecar", "normal") == "deepeval-echo-python-sidecar"
    assert (
        report_stem("echo-python", "sidecar", "drop-facts")
        == "deepeval-echo-python-sidecar-drop-facts"
    )
