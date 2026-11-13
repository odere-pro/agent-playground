"""CH-12 (131): the golden set and one run per engine in a LangSmith dataset, on `fake-langsmith`.

In process over an ASGI transport: no socket, no Docker, no key.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from bakeoff.registry import ROOT
from chassis.core.trace import trace_id_hex
from eval_kit.golden import load_cases
from eval_kit.langfuse_datasets import STORES, load_target
from eval_kit.langsmith_datasets import (
    LangSmithDatasets,
    LangSmithError,
    LangSmithSettings,
    example_id,
    upload,
)
from fake_langsmith import FakeLangSmith, create_app

KEY = "lsv2-test-key"  # pragma: allowlist secret
SETTINGS = LangSmithSettings("http://langsmith.test", KEY)
REPORTS = ROOT / "pocs/poc-07-cross-cutting-decisions/notes/evals"
ENGINE_REPORTS = [REPORTS / "deepeval-echo-python.json", REPORTS / "deepeval-echo-langgraph.json"]


def _client(state: FakeLangSmith, settings: LangSmithSettings = SETTINGS) -> LangSmithDatasets:
    return LangSmithDatasets(settings, transport=httpx.ASGITransport(app=create_app(state)))


async def test_eval_kit_langsmith_golden_cases_and_one_run_per_engine_land_in_the_fake() -> None:
    state = FakeLangSmith(api_key=KEY)
    reports = [json.loads(p.read_text(encoding="utf-8")) for p in ENGINE_REPORTS]
    client = _client(state)
    try:
        runs = await upload(client, "poc07-golden", load_cases(), reports)
    finally:
        await client.close()

    dataset = state.dataset_by_name("poc07-golden")
    assert dataset is not None
    examples = state.examples_of(dataset.id)
    assert len(examples) == 20
    assert [e.metadata["case_id"] for e in examples] == [c.id for c in load_cases()]
    assert runs == ["echo-python-deepeval", "echo-langgraph-deepeval"]
    experiments = state.experiments_of(dataset.id)
    assert [x.name for x in experiments] == runs
    for experiment, report in zip(experiments, reports, strict=True):
        rows = state.runs_of(experiment.id)
        assert len(rows) == 20
        assert {r.reference_example_id for r in rows} == {e.id for e in examples}
        passed = sum(1 for r in rows if r.outputs["pass"])
        assert passed == report["summary"]["passed"]
        assert experiment.metadata["engine"] == report["engine"]
        traces = {example_id("poc07-golden", str(c["id"])): c["trace_id"] for c in report["cases"]}
        for r in rows:
            expected = trace_id_hex(str(traces[r.reference_example_id]))
            assert r.metadata["chassis_trace_id"] == expected
            assert len(expected) == 32


async def test_eval_kit_langsmith_a_second_upload_to_the_same_dataset_fails_on_409() -> None:
    state = FakeLangSmith(api_key=KEY)
    reports = [json.loads(ENGINE_REPORTS[0].read_text(encoding="utf-8"))]
    client = _client(state)
    try:
        await upload(client, "poc07-golden", load_cases(), reports)
        with pytest.raises(LangSmithError, match="POST /api/v1/examples answered HTTP 409"):
            await upload(client, "poc07-golden", load_cases(), reports)
    finally:
        await client.close()
    assert len(state.datasets) == 1
    assert len(state.examples) == 20
    assert len(state.experiments) == 1
    assert len(state.runs) == 20


async def test_eval_kit_langsmith_a_wrong_key_stores_nothing_and_never_names_the_key() -> None:
    state = FakeLangSmith(api_key=KEY)
    wrong = LangSmithSettings("http://langsmith.test", "lsv2-wrong")  # pragma: allowlist secret
    client = _client(state, wrong)
    try:
        with pytest.raises(LangSmithError, match="401") as caught:
            await upload(client, "d", load_cases(), [])
    finally:
        await client.close()
    assert "lsv2-wrong" not in str(caught.value)
    assert state.dataset_by_name("d") is None


def test_eval_kit_langsmith_is_a_known_store(tmp_path: Path) -> None:
    assert "langsmith" in STORES
    path = tmp_path / "datasets.json"
    body = {"dev": {"store": "langsmith", "dataset": "golden-ls"}}
    path.write_text(json.dumps({"schema": "eval-datasets.v0", "environments": body}), "utf-8")
    assert load_target("dev", path).store == "langsmith"


def test_eval_kit_langfuse_main_refuses_a_langsmith_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from eval_kit import langfuse_datasets

    # With keys set, only the store guard stops the upload (without it, main tries the network).
    monkeypatch.setenv(langfuse_datasets.ENV_HOST, "http://langfuse.test")
    monkeypatch.setenv(langfuse_datasets.ENV_PUBLIC, "pk-made-up")
    monkeypatch.setenv(langfuse_datasets.ENV_SECRET, "sk-made-up")  # pragma: allowlist secret
    path = tmp_path / "datasets.json"
    body = {"dev": {"store": "langsmith", "dataset": "golden-ls"}}
    path.write_text(json.dumps({"schema": "eval-datasets.v0", "environments": body}), "utf-8")
    assert langfuse_datasets.main(["--env", "dev", "--config", str(path)]) == 2
    assert "store is 'langsmith'" in capsys.readouterr().err
