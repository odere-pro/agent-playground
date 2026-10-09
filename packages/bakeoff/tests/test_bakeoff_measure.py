"""`measure_task` against a stub chassis on an in-memory transport. Offline."""

from __future__ import annotations

import json
from typing import Any

import httpx
from bakeoff.measure import measure_task
from bakeoff.tasks import get_tasks

ANSWER = "SLM means a small language model. RAG stands for retrieval-augmented generation."


class Calls:
    """A call source that says every run made one model call."""

    def __init__(self) -> None:
        self.total = 0

    def mark(self) -> int:
        return self.total

    def since(self, mark: int) -> list[dict[str, Any]]:
        return [{"model": "m", "messages": [{"role": "user", "content": "x"}]}]


def _chassis(request: httpx.Request) -> httpx.Response:
    body = json.loads(request.content)
    text = body["input"]["text"]
    calls: list[dict[str, Any]] = [
        {"name": "glossary_lookup", "arguments": {"term": "SLM"}},
        {"name": "acronym_expand", "arguments": {"acronym": "RAG"}},
    ]
    answer = ANSWER if text.startswith("lookup") else "Hello."
    tools = calls if text.startswith("lookup") else []
    if not body["stream"]:
        output: dict[str, Any] = {"text": answer}
        if tools:
            output["tool_calls"] = tools
        data = {
            "status": "ok",
            "output": output,
            "metrics": {"input_tokens": 10, "output_tokens": 5},
        }
        return httpx.Response(200, json=data)
    frames: list[tuple[str, dict[str, Any]]] = [("start", {"type": "start"})]
    frames += [("tool_call", {"type": "tool_call", **c}) for c in tools]
    frames += [("delta", {"type": "delta", "text": answer}), ("end", {"type": "end"})]
    frames.append(("response", {"status": "ok"}))
    sse = "".join(f"event: {n}\ndata: {json.dumps(d)}\n\n" for n, d in frames)
    return httpx.Response(200, content=sse.encode(), headers={"content-type": "text/event-stream"})


def test_a_good_chassis_passes_every_task_and_records_the_numbers() -> None:
    client = httpx.Client(base_url="http://chassis", transport=httpx.MockTransport(_chassis))
    tasks = {t.name: t for t in get_tasks(["smoke", "lookup"])}
    smoke = measure_task(client, tasks["smoke"], 3, Calls())
    assert (smoke.runs, smoke.passed, smoke.failures) == (3, 3, [])
    assert len(smoke.latencies_ms) == 3
    assert len(smoke.ttft_ms) == 3
    assert smoke.input_tokens == [10.0] * 3
    assert smoke.model_calls == [{"prompt_bytes": 31, "body_keys": ["messages", "model"]}]
    lookup = measure_task(client, tasks["lookup"], 2, None)
    assert (lookup.passed, lookup.tools_passed) == (2, 2)
    assert lookup.model_calls == []
    assert lookup.to_json()["tool_call_pass_rate"] == 1.0


def test_a_wrong_answer_and_an_http_error_are_failures() -> None:
    def wrong(request: httpx.Request) -> httpx.Response:
        if json.loads(request.content)["stream"]:
            return httpx.Response(503)
        return httpx.Response(200, json={"status": "ok", "output": {"text": "x"}, "metrics": {}})

    client = httpx.Client(base_url="http://chassis", transport=httpx.MockTransport(wrong))
    (task,) = get_tasks(["simplifier"])
    result = measure_task(client, task, 2, None)
    assert result.passed == 0
    assert result.failures == ["streaming /v1/run answered HTTP 503"] * 2
    assert result.to_json()["pass_rate"] == 0.0
