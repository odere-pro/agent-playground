"""The golden set as a Langfuse dataset, and one experiment run per engine on it (PoC-7, W6).

    LANGFUSE_HOST=... LANGFUSE_PUBLIC_KEY=... LANGFUSE_SECRET_KEY=... \\
    uv run python -m bakeoff.langfuse_datasets --dataset poc07-golden \\
        --report pocs/poc-07-cross-cutting-decisions/notes/evals/deepeval-echo-python.json ...

Plain HTTP (httpx) to Langfuse's public API, basic auth with the project's key pair. The field
names follow the generated client of the `langfuse` package 4.18.0 (the line for the v4 server
`deploy/compose/poc07` runs), checked in its source:

- `POST /api/public/v2/datasets`: `api/datasets/raw_client.py`, `CreateDatasetRequest`
  (`name`, `description`, `metadata`).
- `POST /api/public/dataset-items`: `api/dataset_items/types/create_dataset_item_request.py`,
  `CreateDatasetItemRequest` (`datasetName`, `id`, `input`, `expectedOutput`, `metadata`).
- `POST /api/public/dataset-run-items`:
  `api/dataset_run_items/types/create_dataset_run_item_request.py`,
  `CreateDatasetRunItemRequest` (`runName`, `runDescription`, `metadata`, `datasetItemId`,
  `traceId`). The newer `langfuse` 5.x client has no such route; it moved to experiments.

Item ids are `<dataset>-<case id>`: Langfuse upserts an item on its id, and the id is unique per
project. A run item links the item to the trace of the `/v1/run` that answered it: the W3C
32-hex form of the response's `trace_id` (`chassis.core.trace.trace_id_hex`), the id the chassis
spans carry. The run's name is `<engine>-<tool>`, its metadata the pass counts.

Credentials come from the environment only and are never printed or logged; an error names the
HTTP status and the path, never a header.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
from chassis.core.trace import trace_id_hex

from bakeoff.evalreport import validate_report
from bakeoff.golden import Case, load_cases

__all__ = [
    "ENV_HOST",
    "ENV_PUBLIC",
    "ENV_SECRET",
    "LangfuseDatasets",
    "LangfuseError",
    "Settings",
    "item_body",
    "item_id",
    "main",
    "run_item_body",
]

ENV_HOST = "LANGFUSE_HOST"
ENV_PUBLIC = "LANGFUSE_PUBLIC_KEY"
ENV_SECRET = "LANGFUSE_SECRET_KEY"  # pragma: allowlist secret


class LangfuseError(RuntimeError):
    """A Langfuse call failed. The message holds the status and path only."""


@dataclass(frozen=True)
class Settings:
    host: str
    public_key: str = field(repr=False)
    secret_key: str = field(repr=False)

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        source = os.environ if env is None else env
        missing = [n for n in (ENV_HOST, ENV_PUBLIC, ENV_SECRET) if not source.get(n)]
        if missing:
            raise ValueError(f"set {', '.join(missing)}")
        return cls(source[ENV_HOST].rstrip("/"), source[ENV_PUBLIC], source[ENV_SECRET])


def item_id(dataset: str, case: Case) -> str:
    return f"{dataset}-{case.id}"


def item_body(dataset: str, case: Case) -> dict[str, Any]:
    """`CreateDatasetItemRequest` for one golden case."""
    return {
        "datasetName": dataset,
        "id": item_id(dataset, case),
        "input": {"text": case.input},
        "expectedOutput": {
            "contains": list(case.contains),
            "banned": list(case.banned),
            "tool_calls": [c.as_dict() for c in case.tool_calls],
            "reference": case.reference,
        },
        "metadata": {"case_id": case.id, "task": case.task, "label": case.label},
    }


def run_item_body(
    dataset: str, run_name: str, row: Mapping[str, Any], run_meta: Mapping[str, Any]
) -> dict[str, Any]:
    """`CreateDatasetRunItemRequest` for one row of an `eval-report.v0` report."""
    trace = row.get("trace_id")
    if not trace:
        raise ValueError(f"case {row.get('id')} has no trace id")
    return {
        "runName": run_name,
        "runDescription": "PoC-7 eval tool trial on the golden set",
        "metadata": dict(run_meta),
        "datasetItemId": f"{dataset}-{row['id']}",
        "traceId": trace_id_hex(str(trace)),
    }


class LangfuseDatasets:
    """The three calls, on one `httpx.AsyncClient` (tests pass an `httpx.ASGITransport`)."""

    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None):
        self._client = httpx.AsyncClient(
            base_url=settings.host,
            auth=(settings.public_key, settings.secret_key),
            transport=transport,
            timeout=30.0,
            trust_env=False,
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        try:
            response = await self._client.post(path, json=body)
        except httpx.HTTPError as exc:
            raise LangfuseError(f"{type(exc).__name__} on POST {path}") from None
        if response.status_code >= 300:
            raise LangfuseError(f"POST {path} answered HTTP {response.status_code}")
        data: dict[str, Any] = response.json() if response.content else {}
        return data

    async def create_dataset(self, name: str, cases: Sequence[Case]) -> list[str]:
        """Create (or update) the dataset and upsert one item per case. Returns the item ids."""
        await self._post(
            "/api/public/v2/datasets",
            {
                "name": name,
                "description": "PoC-7 golden set: 20 cases, simplifier and lookup",
                "metadata": {"source": "packages/bakeoff/src/bakeoff/golden/cases.yaml"},
            },
        )
        ids = []
        for case in cases:
            await self._post("/api/public/dataset-items", item_body(name, case))
            ids.append(item_id(name, case))
        return ids

    async def record_run(self, dataset: str, report: Mapping[str, Any]) -> str:
        """One experiment run from an `eval-report.v0` report: a run item per case."""
        validate_report(dict(report))
        run_name = f"{report['engine']}-{report['tool']}"
        meta = {"engine": report["engine"], "tool": report["tool"], **report["summary"]}
        for row in report["cases"]:
            await self._post(
                "/api/public/dataset-run-items", run_item_body(dataset, run_name, row, meta)
            )
        return run_name


async def _upload(client: LangfuseDatasets, dataset: str, reports: Sequence[Path]) -> None:
    ids = await client.create_dataset(dataset, load_cases())
    print(f"dataset {dataset}: {len(ids)} items")
    for path in reports:
        report = json.loads(path.read_text(encoding="utf-8"))
        name = await client.record_run(dataset, report)
        print(f"run {name}: {len(report['cases'])} items")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m bakeoff.langfuse_datasets")
    parser.add_argument("--dataset", default="poc07-golden")
    parser.add_argument("--report", type=Path, nargs="*", default=[])
    args = parser.parse_args(argv)
    try:
        settings = Settings.from_env()
    except ValueError as exc:
        print(f"langfuse_datasets: {exc}", file=sys.stderr)
        return 2

    async def go() -> None:
        client = LangfuseDatasets(settings)
        try:
            await _upload(client, args.dataset, args.report)
        finally:
            await client.close()

    try:
        asyncio.run(go())
    except (LangfuseError, ValueError) as exc:
        print(f"langfuse_datasets: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
