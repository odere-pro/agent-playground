"""The Langfuse dataset uploader against a small ASGI stub of the public API (no socket)."""

from __future__ import annotations

import base64
import json
from collections.abc import Awaitable, Callable, MutableMapping
from typing import Any

import httpx
import pytest
from bakeoff.golden import load_cases
from bakeoff.langfuse_datasets import (
    LangfuseDatasets,
    LangfuseError,
    Settings,
)
from bakeoff.registry import ROOT

REPORT = ROOT / "pocs/poc-07-cross-cutting-decisions/notes/evals/deepeval-echo-python.json"
SECRET = "sk-lf-test-secret"  # pragma: allowlist secret
SETTINGS = Settings("http://langfuse.test", "pk-lf-test", SECRET)


class Stub:
    """Records each request; answers 401 without the right basic auth, `status` otherwise."""

    def __init__(self, status: int = 200) -> None:
        self.status = status
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def __call__(
        self,
        scope: MutableMapping[str, Any],
        receive: Callable[[], Awaitable[MutableMapping[str, Any]]],
        send: Callable[[MutableMapping[str, Any]], Awaitable[None]],
    ) -> None:
        body = b""
        while True:
            message = await receive()
            body += message.get("body", b"")
            if not message.get("more_body"):
                break
        headers = dict(scope["headers"])
        want = base64.b64encode(f"pk-lf-test:{SECRET}".encode())
        status = self.status if headers.get(b"authorization") == b"Basic " + want else 401
        self.calls.append((f"{scope['method']} {scope['path']}", json.loads(body or b"{}")))
        payload = json.dumps({"id": "x"}).encode()
        await send(
            {
                "type": "http.response.start",
                "status": status,
                "headers": [(b"content-type", b"application/json")],
            }
        )
        await send({"type": "http.response.body", "body": payload})


async def test_bakeoff_langfuse_creates_the_dataset_and_one_run_per_report() -> None:
    stub = Stub()
    client = LangfuseDatasets(SETTINGS, transport=httpx.ASGITransport(app=stub))
    try:
        ids = await client.create_dataset("poc07-golden", load_cases())
        report = json.loads(REPORT.read_text(encoding="utf-8"))
        run = await client.record_run("poc07-golden", report)
    finally:
        await client.close()
    assert len(ids) == 20 and ids[0] == "poc07-golden-g01"
    assert run == "echo-python-deepeval"
    paths = [path for path, _ in stub.calls]
    assert paths == (
        ["POST /api/public/v2/datasets"]
        + ["POST /api/public/dataset-items"] * 20
        + ["POST /api/public/dataset-run-items"] * 20
    )
    item = stub.calls[1][1]
    assert set(item) == {"datasetName", "id", "input", "expectedOutput", "metadata"}
    assert item["metadata"]["label"] in ("pass", "fail")
    run_item = stub.calls[21][1]
    assert set(run_item) == {"runName", "runDescription", "metadata", "datasetItemId", "traceId"}
    assert run_item["datasetItemId"] == "poc07-golden-g01"
    assert len(run_item["traceId"]) == 32
    assert run_item["traceId"] == report["cases"][0]["trace_id"]


async def test_bakeoff_langfuse_error_names_status_and_path_never_the_key() -> None:
    client = LangfuseDatasets(SETTINGS, transport=httpx.ASGITransport(app=Stub(status=500)))
    try:
        with pytest.raises(LangfuseError) as caught:
            await client.create_dataset("poc07-golden", load_cases())
    finally:
        await client.close()
    assert str(caught.value) == "POST /api/public/v2/datasets answered HTTP 500"
    assert SECRET not in repr(SETTINGS)


def test_bakeoff_langfuse_settings_come_from_the_environment_only() -> None:
    with pytest.raises(ValueError, match="LANGFUSE_SECRET_KEY"):
        Settings.from_env({"LANGFUSE_HOST": "h", "LANGFUSE_PUBLIC_KEY": "p"})
    full = {"LANGFUSE_HOST": "h/", "LANGFUSE_PUBLIC_KEY": "p", "LANGFUSE_SECRET_KEY": "s"}
    assert Settings.from_env(full).host == "h"
