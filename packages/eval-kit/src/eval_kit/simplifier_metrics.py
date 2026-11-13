"""S-3 (028): `facts_kept` and banned words for a golden case file, per case and in aggregate.

    uv run python -m eval_kit.simplifier_metrics --out /tmp/s3.json         # gate set, offline
    uv run python -m eval_kit.simplifier_metrics --cases cases.yaml --answers a.json --out x.json
    uv run python -m eval_kit.simplifier_metrics --judge litellm --out x.json  # the Mac run

`facts_kept` is the chassis's `JudgeEvaluator` (rubric `RUBRIC_VERSION`, temperature 0) through a
named route. `--judge litellm` is `JudgeEvaluator.from_env()`: the route is `CHASSIS_JUDGE_ROUTE`.
`--judge offline` (the default) puts the same judge on `FactsModel`, a deterministic `ModelPort`
stand-in with no socket and no key: it reads the judge's `<input>` and `<answer>` blocks and
replies with the share of the input's numbers and names that the answer still holds. It is for
tests and dry runs only; it is not the score.

Banned words are the case's `banned:` list, matched case-insensitively as substrings, as in
`eval_kit.evalcheck`. The answer of a case is `--answers` (a JSON object, case id to answer) or,
without it, the case's `reference`. The report (`simplifier-metrics.v0`) names the judge route and
the rubric version, and carries no timing, so scoring the same input twice writes the same file.
`aggregate.label_agreement` is the share of cases where `facts_kept >= FACTS_KEPT_TARGET` matches
the hand label `pass`.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from collections.abc import AsyncIterator, Mapping, Sequence
from pathlib import Path
from typing import Any

import yaml
from chassis.adapters.evaluators import RUBRIC_VERSION, JudgeEvaluator
from chassis.ports.evaluator import EvalCase
from chassis.ports.model import ModelChunk, ModelMessage, ModelResult, ToolSpec, Usage

from eval_kit.golden import Case, gate_data, parse_cases

__all__ = [
    "FACTS_KEPT_TARGET",
    "OFFLINE_ROUTE",
    "SCHEMA_ID",
    "FactsModel",
    "banned_hits",
    "extract_facts",
    "main",
    "score_cases",
]

SCHEMA_ID = "simplifier-metrics.v0"
FACTS_KEPT_TARGET = 0.95
"""The success target of the epic: `facts_kept` >= 0.95."""
OFFLINE_ROUTE = "offline-facts"
"""The route name the offline stand-in judge reports."""

_BLOCK = re.compile(r"<(input|answer)>\n(.*?)\n</\1>", re.DOTALL)
_INSTRUCTION = re.compile(r"^\s*[a-z][a-z_-]*:\s*")
_NUMBER = re.compile(r"(?<![A-Za-z\d])[A-Za-z]?\d+(?:[.,:]\d+)*")
_WORD = re.compile(r"(?<![A-Za-z\d])[A-Za-z][A-Za-z'-]*(?![A-Za-z\d])")
_SENTENCE_END = re.compile(r"[.!?]\s*$")


def extract_facts(text: str) -> list[str]:
    """The numbers and names of `text`, in order, without duplicates. A name is a capitalized
    word that does not start a sentence. The offline judge's notion of a fact, nothing more."""
    body = _INSTRUCTION.sub("", text, count=1)
    facts = list(_NUMBER.findall(body))
    for match in _WORD.finditer(body):
        before = body[: match.start()]
        starts_sentence = not before.strip() or bool(_SENTENCE_END.search(before))
        if match.group()[0].isupper() and not starts_sentence:
            facts.append(match.group())
    return list(dict.fromkeys(facts))


def _kept(fact: str, answer: str) -> bool:
    pattern = rf"(?<![\w.,:]){re.escape(fact)}(?![\w]|[.,:]\d)"
    return re.search(pattern, answer, re.IGNORECASE) is not None


class FactsModel:
    """A deterministic `ModelPort` that answers the judge's prompt as `judge-facts-v1` asks:
    `{"score", "reason"}`, the score the share of the input's facts kept in the answer."""

    name = "offline-facts"

    async def complete(
        self,
        messages: Sequence[ModelMessage],
        *,
        route: str,
        tools: Sequence[ToolSpec] | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
        timeout_s: float | None = None,
    ) -> ModelResult:
        blocks = dict(_BLOCK.findall(messages[-1].content or ""))
        facts = extract_facts(blocks.get("input", ""))
        answer = blocks.get("answer", "")
        lost = [f for f in facts if not _kept(f, answer)]
        score = 1.0 if not facts else (len(facts) - len(lost)) / len(facts)
        reason = f"lost {lost}" if lost else "every fact kept"
        text = json.dumps({"score": score, "reason": reason})
        return ModelResult(text=text, usage=Usage(), model=self.name)

    async def stream(
        self,
        messages: Sequence[ModelMessage],
        *,
        route: str,
        tools: Sequence[ToolSpec] | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
        timeout_s: float | None = None,
    ) -> AsyncIterator[ModelChunk]:
        """The judge never streams; one final chunk with the whole verdict."""
        result = await self.complete(messages, route=route)
        yield ModelChunk(text=result.text, usage=result.usage, finish=True)


def banned_hits(answer: str, banned: Sequence[str]) -> list[str]:
    """The words of `banned` that `answer` holds, case-insensitive substrings."""
    folded = answer.casefold()
    return [word for word in banned if word.casefold() in folded]


async def _score_case(judge: JudgeEvaluator, case: Case, answer: str) -> dict[str, Any]:
    score = await judge.score(EvalCase(input=case.input, output=answer, reference=case.reference))
    hits = banned_hits(answer, case.banned)
    return {
        "id": case.id,
        "task": case.task,
        "label": case.label,
        "facts_kept": score.value,
        "facts_reason": score.reason,
        "banned": len(hits),
        "banned_hits": hits,
        "input_tokens": score.input_tokens,
        "output_tokens": score.output_tokens,
    }


def _aggregate(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    facts = [float(r["facts_kept"]) for r in rows]
    n = len(rows)
    agree = sum(
        (f >= FACTS_KEPT_TARGET) == (r["label"] == "pass") for f, r in zip(facts, rows, strict=True)
    )
    return {
        "cases": n,
        "facts_kept_mean": round(sum(facts) / n, 6) if n else None,
        "facts_kept_min": min(facts) if n else None,
        "facts_kept_at_target": sum(f >= FACTS_KEPT_TARGET for f in facts),
        "banned_total": sum(int(r["banned"]) for r in rows),
        "cases_with_banned": sum(int(r["banned"]) > 0 for r in rows),
        "label_agreement": round(agree / n, 6) if n else None,
    }


async def score_cases(
    cases: Sequence[Case], judge: JudgeEvaluator, answers: Mapping[str, str] | None = None
) -> dict[str, Any]:
    """The `simplifier-metrics.v0` report for `cases`, one judge call per case, in order."""
    given = answers or {}
    rows = [await _score_case(judge, c, given.get(c.id, c.reference)) for c in cases]
    return {
        "schema": SCHEMA_ID,
        "judge": {
            "evaluator": judge.name,
            "model": getattr(judge.model, "name", ""),
            "route": judge.route,
            "rubric_version": RUBRIC_VERSION,
            "temperature": 0.0,
        },
        "target": FACTS_KEPT_TARGET,
        "aggregate": _aggregate(rows),
        "cases": rows,
    }


def _load_cases(path: Path | None) -> list[Case]:
    data = gate_data() if path is None else yaml.safe_load(path.read_text(encoding="utf-8"))
    return parse_cases(data, size=None)


def _load_answers(path: Path | None, cases: Sequence[Case]) -> dict[str, str]:
    if path is None:
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not all(isinstance(v, str) for v in data.values()):
        raise ValueError(f"{path}: answers must be a JSON object of case id to answer text")
    unknown = sorted(set(data) - {c.id for c in cases})
    if unknown:
        raise ValueError(f"{path}: answers for unknown cases: {unknown}")
    return dict(data)


def _judge(kind: str) -> JudgeEvaluator:
    if kind == "litellm":
        return JudgeEvaluator.from_env()
    return JudgeEvaluator(FactsModel(), route=OFFLINE_ROUTE)


async def _run(args: argparse.Namespace) -> dict[str, Any]:
    cases = _load_cases(args.cases)
    answers = _load_answers(args.answers, cases)
    judge = _judge(args.judge)
    try:
        return await score_cases(cases, judge, answers)
    finally:
        await judge.aclose()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m eval_kit.simplifier_metrics")
    parser.add_argument(
        "--cases", type=Path, help="a golden case file (default: the 50 gate cases)"
    )
    parser.add_argument(
        "--answers", type=Path, help="JSON object, case id to answer (default: ref)"
    )
    parser.add_argument("--judge", choices=("offline", "litellm"), default="offline")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        report = asyncio.run(_run(args))
    except (OSError, ValueError, LookupError) as exc:
        print(f"simplifier_metrics: {exc}", file=sys.stderr)
        return 2
    args.out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    agg = report["aggregate"]
    print(
        f"{agg['cases']} cases, facts_kept mean {agg['facts_kept_mean']}, "
        f"banned {agg['banned_total']}, route {report['judge']['route']}, "
        f"rubric {report['judge']['rubric_version']} -> {args.out}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
