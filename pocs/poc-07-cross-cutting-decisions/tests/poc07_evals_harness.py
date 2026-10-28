"""Shared harness for the PoC-7 evals scenarios. Not a test module.

- `gate_script()`: a fake model script with one gate case (a bad answer on the default route, a
  good one on the fallback route) and a scripted judge in the chassis `JudgeEvaluator` format. The
  judge's scores are scripted, so they show the mechanics only, never judge quality.
- `tei_stub()`: an in-process stub of the Hugging Face TEI `POST /predict` API.
- `serve_gate_backends()`: the fake model server on `gate_script()` and the two-tool fake MCP
  server, each on a Unix socket (the PoC-6a `Backends`), so a sidecar workload and the judge reach
  them with no TCP.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from fake_mcp_server.server import FakeMcpState
from fake_mcp_server.server import create_app as create_tool_app
from fake_model_server import Script
from fake_model_server import create_app as create_model_app
from fastapi import FastAPI, Request
from poc02_harness import on_unix_socket
from poc06a_harness import Backends, Recorder, Seen

FALLBACK_ROUTE = "fallback-route"
JUDGE_ROUTE = "judge"
GATE_INPUT = "simplify: gate-check The report was signed by Dana Ruiz on 4 May 2025."
BAD = "The report was signed."
GOOD = "Dana Ruiz signed the report on 4 May 2025."
JUDGE_USAGE = {"prompt_tokens": 300, "completion_tokens": 25}
"""Scripted judge usage; the real count of the rubric prompt is in the gate cost note."""
CONTRACT_BAD = "It is a model. It is cheap."


def judge_rules(bad: list[str]) -> list[dict[str, Any]]:
    rules = [
        {
            "match": f"<answer>\n{answer}\n</answer>",
            "reply": json.dumps({"score": 0.1, "reason": "drops facts"}),
            "usage": dict(JUDGE_USAGE),
        }
        for answer in bad
    ]
    rules.append(
        {
            "match": "</answer>",
            "reply": json.dumps({"score": 0.95, "reason": "keeps the facts"}),
            "usage": dict(JUDGE_USAGE),
        }
    )
    return rules


def gate_script() -> Script:
    return Script.model_validate(
        {
            "rules": [
                *judge_rules([BAD, CONTRACT_BAD]),
                {"model": FALLBACK_ROUTE, "match": GATE_INPUT, "reply": GOOD},
                {"match": GATE_INPUT, "reply": BAD},
            ],
            "default_reply": "ok",
        }
    )


_FACT = re.compile(r"\b(?:[A-Z][\w-]+|\d[\d.,:]*)\b")


def tei_stub() -> FastAPI:
    """`POST /predict`: per `[premise, hypothesis]` pair, the three NLI labels sorted by score.
    Entailment is the share of the hypothesis's capitalized words and numbers in the premise."""
    app = FastAPI()

    @app.post("/predict")
    async def predict(request: Request) -> Any:
        body = await request.json()
        out = []
        for premise, hypothesis in body["inputs"]:
            facts = set(_FACT.findall(hypothesis))
            kept = len([f for f in facts if f in premise]) / max(len(facts), 1)
            labels: list[dict[str, Any]] = [
                {"label": "entailment", "score": kept},
                {"label": "neutral", "score": (1 - kept) / 2},
                {"label": "contradiction", "score": (1 - kept) / 2},
            ]
            out.append(sorted(labels, key=lambda x: -float(x["score"])))
        return out

    return app


@contextmanager
def serve_gate_backends() -> Iterator[Backends]:
    model_app = create_model_app(gate_script())
    state = FakeMcpState()
    model_seen: list[Seen] = []
    tool_seen: list[Seen] = []
    with (
        on_unix_socket(Recorder(model_app, model_seen), "model.sock") as model_uds,
        on_unix_socket(Recorder(create_tool_app(state), tool_seen), "tools.sock") as tool_uds,
    ):
        yield Backends(model_uds, tool_uds, model_app, state, model_seen, tool_seen)
