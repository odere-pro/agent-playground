"""059 H-15: `agentctl new` renders `evals/cases.yaml` in the `eval_kit` schema with a judged case,
and the generated repo's `make eval` runs the judge on the per-case fake model, offline.

The rendered file must load with `eval_kit.golden.parse_cases`, the platform's own reader. The
`make eval` run is the generated repo's own target, as a developer runs it: `agentctl new` (with
`uv lock`), then `make eval` with `UV_OFFLINE=1`, no key, no socket but Unix and loopback. One case
is added that the judge must fail, so the one run shows a pass, a fail, and the judge's scores.
The file also proves 058 CH-8: the LangGraph default (no `--framework`) passes `make eval`.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml
from agentctl import rules
from agentctl.scaffold import Answers, render
from eval_kit.evalreport import validate_report
from eval_kit.golden import parse_cases

ROOT = Path(__file__).resolve().parents[3]
FAIL_CASE: dict[str, Any] = {
    "id": "g02",
    "task": "simplifier",
    "input": "simplify: the cat sat on the warm mat",
    "expect": {"contains": ["cat"], "tool_calls": []},
    "rubric": "The answer keeps every fact of the input in plain words.",
    "reference": "The cat ran away.",
    "label": "fail",
}
"""A case whose reference the human label calls bad: the model answers with it, the scripted judge
says `fail`. `contains` passes, so only the judge can fail it."""


def _render(tmp_path: Path, framework: str) -> Path:
    answers = Answers(
        agent_name="my-agent",
        kind="transformer",
        framework=framework,
        connector=rules.default_connector(rules.resolve_trust(framework, None)),
        platform_path=ROOT,
    )
    return render(answers, tmp_path)


@pytest.mark.parametrize("framework", rules.FRAMEWORKS)
def test_the_rendered_cases_are_in_the_eval_kit_schema(tmp_path: Path, framework: str) -> None:
    data = yaml.safe_load((_render(tmp_path, framework) / "evals/cases.yaml").read_text())
    cases = parse_cases(data, size=None)
    assert cases, "evals/cases.yaml has no case"
    assert all(case.rubric and case.reference for case in cases)
    assert "pass" in {case.label for case in cases}


def _env() -> dict[str, str]:
    """No user git config, no key, nothing from this shell; uv offline from its own cache."""
    keep = {k: os.environ[k] for k in ("PATH", "HOME", "TMPDIR", "UV_CACHE_DIR") if k in os.environ}
    return keep | {"GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1", "UV_OFFLINE": "1"}


def _new(tmp_path: Path, *flags: str) -> Path:
    """`agentctl new ev-agent --kind transformer` with `flags`, locked offline; the agent folder."""
    made = subprocess.run(
        [
            *(sys.executable, "-m", "agentctl", "new", "ev-agent", "--kind", "transformer"),
            *flags,
            *("--dest", str(tmp_path), "--platform-path", str(ROOT)),
        ],
        env=_env(),
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert made.returncode == 0, made.stderr
    return tmp_path / "ev-agent"


def _make_eval(agent: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["make", "eval"], cwd=agent, env=_env(), capture_output=True, text=True, timeout=900
    )


@pytest.mark.slow
def test_make_eval_passes_on_the_langgraph_default(tmp_path: Path) -> None:
    """058 CH-8: the agent `agentctl new` scaffolds with no `--framework` passes its own evals."""
    agent = _new(tmp_path)
    answers = yaml.safe_load((agent / ".copier-answers.yml").read_text())
    assert answers["framework"] == rules.DEFAULT_FRAMEWORK == "langgraph"
    cases = yaml.safe_load((agent / "evals/cases.yaml").read_text())["cases"]

    done = _make_eval(agent)

    tail = done.stdout[-3000:] + done.stderr[-2000:]
    assert done.returncode == 0, tail
    report = json.loads((agent / "reports/eval.json").read_text())
    validate_report(report)
    assert report["summary"] == {"total": len(cases), "passed": len(cases), "failed": 0}
    assert report["meta"]["judge"] == {case["id"]: 1 for case in cases}


@pytest.mark.slow
def test_make_eval_runs_the_judge_on_the_per_case_fake_model(tmp_path: Path) -> None:
    agent = _new(tmp_path, "--framework", "python")
    cases_file = agent / "evals/cases.yaml"
    data = yaml.safe_load(cases_file.read_text())
    (template_case,) = data["cases"]
    cases_file.write_text(yaml.safe_dump({**data, "cases": [template_case, FAIL_CASE]}))

    done = _make_eval(agent)

    tail = done.stdout[-3000:] + done.stderr[-2000:]
    assert done.returncode != 0, tail
    assert "1 failed" in done.stdout, tail
    report = json.loads((agent / "reports/eval.json").read_text())
    validate_report(report)
    assert report["summary"] == {"total": 2, "passed": 1, "failed": 1}
    rows = {row["id"]: row for row in report["cases"]}
    assert rows[template_case["id"]]["pass"] is True
    assert rows["g02"]["pass"] is False
    failing = [a["kind"] for a in rows["g02"]["assertions"] if not a["pass"]]
    assert failing == ["rubric"], rows["g02"]
    judge = report["meta"]["judge"]
    assert judge == {template_case["id"]: 1, "g02": 0}
