"""The golden set as a LangSmith dataset, and one experiment per engine on it (CH-12, issue 131).

    LANGSMITH_ENDPOINT=... LANGSMITH_API_KEY=... \\
    uv run python -m eval_kit.langsmith_datasets --env dev \\
        --report pocs/poc-07-cross-cutting-decisions/notes/evals/deepeval-echo-python.json ...

Plain HTTP (httpx) to the LangSmith REST API, the key in `x-api-key`. Tests run it against
`fake-langsmith` (`fake_langsmith.datasets` lists the routes and fields; suggested: they are not
checked against a real LangSmith yet):

- the dataset: found by name (`GET /api/v1/datasets?name=`), else `POST /api/v1/datasets`;
- one example per golden case (`POST /api/v1/examples`); its id is a UUID5 of
  `<dataset>-<case id>`, so the same case keeps the same id. A rerun is not idempotent: a second
  upload to the same dataset finds the dataset, then the first example is 409 and the upload stops
  with a `LangSmithError` before any new experiment (tested on the fake). Use a new dataset name;
- one experiment per `eval-report.v0` report (`POST /api/v1/sessions` with
  `reference_dataset_id`), named `<engine>-<tool>`, the pass counts in its metadata;
- one run per report row (`POST /api/v1/runs`) on the example of its case. The chassis trace id
  (W3C 32-hex) goes in the run's metadata as `chassis_trace_id`.

The cases come from the golden YAML only, as for Langfuse (`eval_kit.langfuse_datasets`). The
environment entry in `evals/datasets.json` must name the `langsmith` store.

Credentials come from the environment only and are never printed or logged; an error names the
HTTP status and the path, never a header.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from chassis.core.trace import trace_id_hex

from eval_kit.evalreport import validate_report
from eval_kit.golden import Case, load_cases
from eval_kit.langfuse_datasets import DATASETS_CONFIG, item_body, load_target

__all__ = [
    "ENV_ENDPOINT",
    "ENV_KEY",
    "LangSmithDatasets",
    "LangSmithError",
    "LangSmithSettings",
    "example_id",
    "main",
    "upload",
]

ENV_ENDPOINT = "LANGSMITH_ENDPOINT"
ENV_KEY = "LANGSMITH_API_KEY"  # pragma: allowlist secret
STORE = "langsmith"


class LangSmithError(RuntimeError):
    """A LangSmith call failed. The message holds the status and path only."""


@dataclass(frozen=True)
class LangSmithSettings:
    endpoint: str
    api_key: str = field(repr=False)

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> LangSmithSettings:
        source = os.environ if env is None else env
        missing = [n for n in (ENV_ENDPOINT, ENV_KEY) if not source.get(n)]
        if missing:
            raise ValueError(f"set {', '.join(missing)}")
        return cls(source[ENV_ENDPOINT].rstrip("/"), source[ENV_KEY])


def example_id(dataset: str, case_id: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"{dataset}-{case_id}"))


class LangSmithDatasets:
    """The REST calls, on one `httpx.AsyncClient` (tests pass an `httpx.ASGITransport`)."""

    def __init__(
        self, settings: LangSmithSettings, transport: httpx.AsyncBaseTransport | None = None
    ):
        self._client = httpx.AsyncClient(
            base_url=settings.endpoint,
            headers={"x-api-key": settings.api_key},
            transport=transport,
            timeout=30.0,
            trust_env=False,
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def _call(
        self, method: str, path: str, body: dict[str, Any] | None = None, **params: str
    ) -> Any:
        try:
            response = await self._client.request(method, path, json=body, params=params)
        except httpx.HTTPError as exc:
            raise LangSmithError(f"{type(exc).__name__} on {method} {path}") from None
        if response.status_code >= 300:
            raise LangSmithError(f"{method} {path} answered HTTP {response.status_code}")
        return response.json() if response.content else {}

    async def dataset(self, name: str) -> str:
        """The id of the dataset `name`, created when it is not there yet."""
        found = await self._call("GET", "/api/v1/datasets", name=name)
        if isinstance(found, list) and found:
            return str(found[0]["id"])
        description = "Golden set: 20 cases, simplifier and lookup"
        created = await self._call(
            "POST", "/api/v1/datasets", {"name": name, "description": description}
        )
        return str(created["id"])

    async def add_examples(self, dataset: str, dataset_id: str, cases: Sequence[Case]) -> None:
        for case in cases:
            item = item_body(dataset, case)
            body = {
                "id": example_id(dataset, case.id),
                "dataset_id": dataset_id,
                "inputs": item["input"],
                "outputs": item["expectedOutput"],
                "metadata": item["metadata"],
            }
            await self._call("POST", "/api/v1/examples", body)

    async def record_run(self, dataset: str, dataset_id: str, report: Mapping[str, Any]) -> str:
        """One experiment from an `eval-report.v0` report: a run per case. Returns its name."""
        validate_report(dict(report))
        name = f"{report['engine']}-{report['tool']}"
        meta = {"engine": report["engine"], "tool": report["tool"], **report["summary"]}
        session = await self._call(
            "POST",
            "/api/v1/sessions",
            {"name": name, "reference_dataset_id": dataset_id, "extra": {"metadata": meta}},
        )
        for row in report["cases"]:
            await self._call("POST", "/api/v1/runs", _run_body(dataset, session["id"], name, row))
        return name


def _run_body(dataset: str, session_id: str, name: str, row: Mapping[str, Any]) -> dict[str, Any]:
    trace = row.get("trace_id")
    if not trace:
        raise ValueError(f"case {row.get('id')} has no trace id")
    run_id = str(uuid.uuid4())
    now = datetime.now(UTC).isoformat()
    return {
        "id": run_id,
        "trace_id": run_id,
        "name": name,
        "run_type": "chain",
        "start_time": now,
        "end_time": now,
        "session_id": session_id,
        "reference_example_id": example_id(dataset, str(row["id"])),
        "inputs": {"case_id": row["id"]},
        "outputs": {"pass": bool(row["pass"]), "assertions": row.get("assertions", [])},
        "extra": {"metadata": {"chassis_trace_id": trace_id_hex(str(trace))}},
    }


async def upload(
    client: LangSmithDatasets,
    dataset: str,
    cases: Sequence[Case],
    reports: Sequence[Mapping[str, Any]],
) -> list[str]:
    """The dataset, its examples, then one experiment per report. Returns the experiment names."""
    dataset_id = await client.dataset(dataset)
    await client.add_examples(dataset, dataset_id, cases)
    return [await client.record_run(dataset, dataset_id, report) for report in reports]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m eval_kit.langsmith_datasets")
    parser.add_argument("--env", default="dev", help="the entry in the dataset config")
    parser.add_argument("--config", type=Path, default=DATASETS_CONFIG)
    parser.add_argument("--report", type=Path, nargs="*", default=[])
    args = parser.parse_args(argv)
    try:
        target = load_target(args.env, args.config)
        if target.store != STORE:
            raise ValueError(f"{args.env}: store is {target.store!r}, not {STORE!r}")
        settings = LangSmithSettings.from_env()
    except ValueError as exc:
        print(f"langsmith_datasets: {exc}", file=sys.stderr)
        return 2

    async def go() -> list[str]:
        client = LangSmithDatasets(settings)
        try:
            reports = [json.loads(p.read_text(encoding="utf-8")) for p in args.report]
            return await upload(client, target.dataset, load_cases(), reports)
        finally:
            await client.close()

    try:
        runs = asyncio.run(go())
    except (LangSmithError, ValueError) as exc:
        print(f"langsmith_datasets: {exc}", file=sys.stderr)
        return 1
    print(f"dataset {target.dataset}: {len(runs)} experiments")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
