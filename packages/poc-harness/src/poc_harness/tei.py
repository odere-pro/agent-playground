"""An in-process stub of the Hugging Face TEI `POST /predict` API. No model, no socket."""

from __future__ import annotations

import re
from typing import Any

from fastapi import FastAPI, Request

_FACT = re.compile(r"\b(?:[A-Z][\w-]+|\d[\d.,:]*)\b")


def tei_stub(seen: list[dict[str, Any]] | None = None) -> FastAPI:
    """`POST /predict` with `{"inputs": [[premise, hypothesis]]}`: per pair, the three NLI labels,
    sorted by score as TEI does. Entailment is the share of the hypothesis's capitalized words
    and numbers found in the premise: a toy, so a contract has a good and a bad answer. Each
    request body is appended to `seen` when it is given."""
    app = FastAPI()

    @app.post("/predict")
    async def predict(request: Request) -> Any:
        body = await request.json()
        if seen is not None:
            seen.append(body)
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
