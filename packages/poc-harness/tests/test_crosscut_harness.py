"""The cross-cutting harness: one TEI stub, one config builder, and demos that import no test."""

from __future__ import annotations

import ast
from pathlib import Path

import httpx
import pytest
from poc_harness.apps import chassis_config
from poc_harness.paths import ROOT
from poc_harness.promtext import families, value
from poc_harness.tei import tei_stub

DEMOS = sorted((ROOT / "pocs").glob("poc-0[78]-*/demo/*.py"))
LONGEST_DEMO_FUNCTION = 50
TRACE_DEMO = ROOT / "pocs/poc-07-cross-cutting-decisions/demo/demo.py"


def _imported_modules(path: Path) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.split(".")[0])
    return names


async def test_tei_stub_scores_the_share_of_facts_kept_and_records_the_body() -> None:
    seen: list[dict[str, object]] = []
    transport = httpx.ASGITransport(app=tei_stub(seen))
    body = {"inputs": [["Dana signed on 4 May.", "Dana signed."], ["Dana signed.", "Ruiz left."]]}
    async with httpx.AsyncClient(transport=transport, base_url="http://tei") as client:
        answer = (await client.post("/predict", json=body)).json()
    assert seen == [body]
    assert answer[0][0] == {"label": "entailment", "score": 1.0}
    assert answer[1][0]["label"] != "entailment"


def test_tei_stub_needs_no_recorder() -> None:
    assert tei_stub().routes


def test_chassis_config_keeps_its_default_version_and_takes_another() -> None:
    engine = {"connector": "inprocess", "handle": "chassis.core.handle:echo_wire"}
    assert chassis_config(engine).version == "cfg-poc2"
    assert chassis_config(engine, version="cfg-other").version == "cfg-other"


def test_promtext_reads_a_sample_and_its_family() -> None:
    text = '# TYPE chassis_requests_total counter\nchassis_requests_total{agent="a"} 2\n'
    assert value(text, "chassis_requests_total", agent="a") == 2
    assert "chassis_requests_total" in families(text)


@pytest.mark.parametrize("demo", DEMOS, ids=lambda path: path.name)
def test_a_demo_imports_no_test_module_and_no_poc_harness_file(demo: Path) -> None:
    """R7-07: a renamed test cannot break a demo, because the demo imports from the package."""
    local = {name for name in _imported_modules(demo) if name.startswith(("test_", "poc0"))}
    assert not local
    assert "sys.path" not in demo.read_text(encoding="utf-8")


def test_the_trace_demo_has_no_function_over_the_limit() -> None:
    """R7-07: `_trace_and_feedback` was 143 lines."""
    tree = ast.parse(TRACE_DEMO.read_text(encoding="utf-8"))
    functions = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)]
    over = {f.name: f.end_lineno - f.lineno + 1 for f in functions if f.end_lineno}
    assert not {name: n for name, n in over.items() if n > LONGEST_DEMO_FUNCTION}
