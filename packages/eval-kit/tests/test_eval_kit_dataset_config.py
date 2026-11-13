"""CH-12 (131): the dataset name and the result store come from `evals/datasets.json`."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from bakeoff.registry import ROOT
from eval_kit.golden import CASES_FILE, load_cases
from eval_kit.langfuse_datasets import (
    DATASETS_CONFIG,
    DatasetTarget,
    item_body,
    load_target,
)


def _write(tmp_path: Path, environments: dict[str, object]) -> Path:
    path = tmp_path / "datasets.json"
    body = {"schema": "eval-datasets.v0", "environments": environments}
    path.write_text(json.dumps(body), encoding="utf-8")
    return path


def test_eval_kit_dataset_name_and_store_come_from_the_committed_config() -> None:
    data = json.loads(DATASETS_CONFIG.read_text(encoding="utf-8"))
    assert data["environments"]
    for env, entry in data["environments"].items():
        assert load_target(env) == DatasetTarget(env, entry["store"], entry["dataset"])


def test_eval_kit_another_dataset_needs_a_config_change_only(tmp_path: Path) -> None:
    path = _write(tmp_path, {"dev": {"store": "langfuse", "dataset": "golden-v2"}})
    assert load_target("dev", path).dataset == "golden-v2"


@pytest.mark.parametrize(
    ("entry", "error"),
    [
        ({"store": "langfuse"}, "dataset"),
        ({"dataset": "d"}, "store"),
        ({"store": ["langfuse", "langsmith"], "dataset": "d"}, "one store"),
        ({"store": "sqlite", "dataset": "d"}, "store"),
    ],
)
def test_eval_kit_each_environment_names_exactly_one_known_store(
    tmp_path: Path, entry: dict[str, object], error: str
) -> None:
    with pytest.raises(ValueError, match=error):
        load_target("dev", _write(tmp_path, {"dev": entry}))


def test_eval_kit_an_unknown_environment_is_refused(tmp_path: Path) -> None:
    path = _write(tmp_path, {"dev": {"store": "langfuse", "dataset": "d"}})
    with pytest.raises(ValueError, match="no environment 'prod'"):
        load_target("prod", path)


def test_eval_kit_dataset_items_come_from_the_golden_yaml_only() -> None:
    cases = load_cases()
    assert CASES_FILE.name == "cases.yaml" and CASES_FILE.parent.name == "golden"
    bodies = [item_body("d", case) for case in cases]
    assert [b["metadata"]["case_id"] for b in bodies] == [c.id for c in cases]
    assert len(bodies) == 20


def test_eval_kit_main_reads_the_dataset_name_from_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from eval_kit import langfuse_datasets

    path = _write(tmp_path, {"dev": {"store": "langfuse", "dataset": "golden-v9"}})
    seen: list[str] = []

    async def fake_upload(client: object, dataset: str, reports: object) -> None:
        seen.append(dataset)

    monkeypatch.setattr(langfuse_datasets, "_upload", fake_upload)
    env = {"LANGFUSE_HOST": "http://lf.test", "LANGFUSE_PUBLIC_KEY": "p"}
    for name, value in {**env, "LANGFUSE_SECRET_KEY": "s"}.items():
        monkeypatch.setenv(name, value)
    assert langfuse_datasets.main(["--env", "dev", "--config", str(path)]) == 0
    assert seen == ["golden-v9"]


def test_eval_kit_make_eval_datasets_calls_the_upload_with_the_config_env() -> None:
    out = subprocess.run(
        ["make", "-n", "eval-datasets", "EVAL_DATASET_ENV=ci"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert "-m eval_kit.langfuse_datasets --env ci" in out
