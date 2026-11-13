"""A `fake`-profile test scripts the model's answer per case, with no chassis import (015 H-8).

`echo-python` sends the input text to the model as the last user message and streams the answer
back. The chassis runs in its own process; its fake model answers from `MODEL_SCRIPT`, one rule
per case, matched on that case's text. This module imports only `agent_testkit`.
"""

from __future__ import annotations

import ast
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from agent_testkit.processes import (
    Chassis,
    ModelScript,
    StartError,
    Workload,
    start_chassis,
    start_workload,
    write_model_script,
)
from agent_testkit.settings import CURRENT_PYTHON, AgentRef, source_for

ECHO = AgentRef(handle="echo_python:handle", name="echo-python")
START_S = 120.0
"""suggested: the workload start budget here; a loaded, shared Mac can take over 30 s."""

CASES: dict[str, dict[str, Any]] = {
    "alpha": {"reply": "Alpha is the first answer."},
    "beta": {"reply": "Beta is another answer."},
    "gamma": {"error": "rate_limited", "retryable": True},
}
"""Per case: the text sent, and what the fake model does when it sees it."""
MODEL_SCRIPT: ModelScript = [{"match": f"case {name}", **rule} for name, rule in CASES.items()]


@pytest.fixture(scope="module")
def echo_workload() -> Iterator[Workload]:
    with start_workload(ECHO, timeout_s=START_S) as started:
        yield started


@pytest.fixture(scope="module")
def scripted(echo_workload: Workload) -> Iterator[Chassis]:
    source = source_for(CURRENT_PYTHON, Path.cwd())
    with start_chassis(echo_workload, source, model_script=MODEL_SCRIPT) as started:
        yield started


@pytest.mark.slow
@pytest.mark.parametrize("case", ["alpha", "beta"])
def test_each_case_gets_its_scripted_answer(scripted: Chassis, case: str) -> None:
    envelope = scripted.run({"text": f"case {case}"})
    assert envelope["status"] == "ok", envelope
    output = json.dumps(envelope["output"])
    assert CASES[case]["reply"] in output, envelope
    others = [CASES[o]["reply"] for o in ("alpha", "beta") if o != case]
    assert not any(other in output for other in others), envelope


@pytest.mark.slow
def test_a_case_gets_its_scripted_model_error(scripted: Chassis) -> None:
    envelope = scripted.run({"text": "case gamma"})
    assert envelope["status"] == "error", envelope
    assert envelope["output"]["error"]["code"], envelope


@pytest.mark.slow
def test_an_unscripted_case_gets_the_default_answer(scripted: Chassis) -> None:
    envelope = scripted.run({"text": "no rule matches this"})
    assert envelope["status"] == "ok", envelope
    assert not any(
        rule.get("reply", "") in json.dumps(envelope["output"])
        for rule in CASES.values()
        if rule.get("reply")
    )


@pytest.mark.slow
def test_a_rule_with_an_unknown_key_stops_the_start(echo_workload: Workload) -> None:
    source = source_for(CURRENT_PYTHON, Path.cwd())
    with (
        pytest.raises(StartError, match="unknown keys \\['answer'\\]"),
        start_chassis(echo_workload, source, model_script=[{"answer": "x"}], timeout_s=START_S),
    ):
        pass


def test_a_script_that_is_not_a_list_of_rules_is_refused(tmp_path: Path) -> None:
    with pytest.raises(TypeError, match="list of rule mappings"):
        write_model_script({"match": "x"}, tmp_path / "s.json")  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="list of rule mappings"):
        write_model_script(["x"], tmp_path / "s.json")  # type: ignore[list-item]


def test_this_module_imports_no_chassis() -> None:
    tree = ast.parse(Path(__file__).read_text())
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names += [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            names.append(node.module or "")
    assert [n for n in names if n.split(".")[0] in ("chassis", "chassis_contracts")] == []
