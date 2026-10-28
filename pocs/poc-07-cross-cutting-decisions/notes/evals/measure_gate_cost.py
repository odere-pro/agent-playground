"""Measure the evaluator gate's added latency, judge calls, and judge tokens per request, offline.

    uv run python pocs/poc-07-cross-cutting-decisions/notes/evals/measure_gate_cost.py [N]

The chassis app (in process, ASGI, no socket) serves a FakeEngine that answers a simplifier text;
the judge is `JudgeEvaluator` over the real `LiteLLMModel`, against the fake model server (ASGI).
The latencies are of the fakes: they show the gate's own overhead (buffering, one more HTTP model
round trip, the judge parse), not a real judge's latency. The tokens per judge call are the fake's
scripted usage; the real token count of the rubric prompt is printed by `count_judge_tokens.py`.

Rows: the gate off; sync and async at sample rates 1.0, 0.25, 0.1 with a passing answer; and sync
at 1.0 with a failing answer (retry, then fallback: the worst path).
"""

from __future__ import annotations

import asyncio
import json
import statistics
import sys
import time
from collections.abc import AsyncIterator
from typing import Any

import httpx
from chassis.adapters.evaluators import JudgeEvaluator
from chassis.adapters.litellm import LiteLLMModel
from chassis.core.envelope import Context, TaskInput
from chassis.core.events import Delta, End, Event, Metrics, Start
from chassis.fakes import FakeEngine, InMemoryConfig, InMemoryTelemetry, ScriptedModel
from chassis.ports.bundle import PortBundle
from chassis.server import ChassisConfig, create_app
from chassis.server.evaluator_gate import GatedEngine
from fake_model_server import Script
from fake_model_server import create_app as create_model_app

INPUT = (
    "simplify: The SLM was released by Acme in 2026, thereby reducing operating costs by "
    "approximately 30 percent."
)
GOOD = "Acme put out the SLM in 2026. It cut costs by about 30 percent."
BAD = "Acme made a model."
ENGINE_TOKENS = (60, 18)  # suggested: a typical simplifier call (prompt, answer)
JUDGE_USAGE = {"prompt_tokens": 300, "completion_tokens": 25}


def judge_script() -> Script:
    return Script.model_validate(
        {
            "rules": [
                {
                    "match": f"<answer>\n{BAD}\n</answer>",
                    "reply": json.dumps({"score": 0.1, "reason": "drops facts"}),
                    "usage": JUDGE_USAGE,
                },
                {
                    "match": "</answer>",
                    "reply": json.dumps({"score": 0.95, "reason": "keeps the facts"}),
                    "usage": JUDGE_USAGE,
                },
            ]
        }
    )


def engine(answer: str) -> FakeEngine:
    async def handle(input: TaskInput, ctx: Context) -> AsyncIterator[Event]:
        yield Start(request_id=ctx.request_id)
        text = GOOD if ctx.model_route == "fallback-route" else answer
        yield Delta(text=text)
        yield Metrics(input_tokens=ENGINE_TOKENS[0], output_tokens=ENGINE_TOKENS[1])
        yield End()

    return FakeEngine(handle=handle)


def config(gate: bool, mode: str = "sync", rate: float = 1.0) -> ChassisConfig:
    return ChassisConfig.model_validate(
        {
            "version": "cfg-measure",
            "profile": "fake",
            "agent": {"name": "simplifier", "version": "0.0.1"},
            "spec": {
                "engine": {"connector": "inprocess", "handle": "chassis.core.handle:echo_wire"},
                "model": {"route": "big-default", "fallback_route": "fallback-route"},
                "harness": {
                    "evaluator_gate": gate,
                    "evaluator_mode": mode,
                    "evaluator_sample_rate": rate,
                    "retries": 1,
                },
            },
        }
    )


async def measure(label: str, cfg: ChassisConfig, answer: str, n: int) -> dict[str, Any]:
    fake = create_model_app(judge_script())
    judge = JudgeEvaluator(
        LiteLLMModel("http://judge/v1", transport=httpx.ASGITransport(app=fake)), route="judge"
    )
    bundle = PortBundle(
        model=ScriptedModel(),
        engine=engine(answer),
        config=InMemoryConfig(),
        telemetry=InMemoryTelemetry(),
        evaluator=judge,
    )
    app = create_app(cfg, bundle)
    latencies: list[float] = []
    tokens: list[int] = []
    async with (
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://c") as client,
        app.router.lifespan_context(app),
    ):
        for i in range(n + 20):
            started = time.perf_counter()
            res = await client.post(
                "/v1/run", json={"input": {"text": INPUT}, "request_id": f"req-{i}"}
            )
            elapsed = (time.perf_counter() - started) * 1000
            if i >= 20:  # warm-up
                latencies.append(elapsed)
                m = res.json()["metrics"]
                tokens.append(m["input_tokens"] + m["output_tokens"])
        gate = app.state.ports.engine
        if isinstance(gate, GatedEngine):
            await gate.wait(30)
    latencies.sort()
    return {
        "row": label,
        "n": n,
        "judge_calls_per_req": round(fake.state.calls_total / (n + 20), 3),
        "mean_ms": round(statistics.fmean(latencies), 2),
        "p95_ms": round(latencies[int(0.95 * (len(latencies) - 1))], 2),
        "metrics_tokens_per_req": round(statistics.fmean(tokens), 1),
    }


async def main(n: int) -> None:
    rows = [await measure("gate off", config(False), GOOD, n)]
    for mode in ("sync", "async"):
        for rate in (1.0, 0.25, 0.1):
            label = f"{mode} rate {rate}"
            rows.append(await measure(label, config(True, mode, rate), GOOD, n))
    rows.append(await measure("sync rate 1.0, failing answer", config(True), BAD, n))
    base = rows[0]
    print(
        "| Row | Judge calls / request | Mean ms | p95 ms | Added mean ms | Added p95 ms "
        "| Tokens in response metrics / request |"
    )
    print("| --- | ---: | ---: | ---: | ---: | ---: | ---: |")
    for r in rows:
        print(
            f"| {r['row']} | {r['judge_calls_per_req']} | {r['mean_ms']} | {r['p95_ms']} | "
            f"{r['mean_ms'] - base['mean_ms']:.2f} | {r['p95_ms'] - base['p95_ms']:.2f} | "
            f"{r['metrics_tokens_per_req']} |"
        )


if __name__ == "__main__":
    asyncio.run(main(int(sys.argv[1]) if len(sys.argv) > 1 else 400))
