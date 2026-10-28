"""Files generated from the golden set, and the check that they are in sync.

    uv run python -m bakeoff.golden.generate --write    # rewrite the three files
    uv run python -m bakeoff.golden.generate --check    # exit 1 when one is stale

| File                                                   | What                                   |
| packages/fake-model-server/scripts/golden.yaml         | the engines' model: each case's answer |
| packages/fake-model-server/scripts/golden-judge.yaml   | the judge: a JSON verdict per answer   |
| evals/promptfoo/tests.yaml                             | the promptfoo test cases               |

The model script: a plain rule per case matches its input. A case whose model calls tools gets
`after_tool` rules keyed on a distinctive text of each tool result (the definition or the
expansion the chassis's fake tools return; the asked-for term when the result is null). The
first match wins, so `after_tool` rules go longest match first. `simulate` replays every case
through `fake_model_server.Script.pick` with the real fake tools and checks the script gives
exactly the case's calls and reference answer, for both tool-result encodings the engines send
(`json.dumps` default and compact).

The judge script: a rule per case matches the case's reference answer anywhere in the judge's
prompt and replies `{"reason", "pass", "score"}` (promptfoo's `llm-rubric` reads `pass` and
`score`; DeepEval's `GEval` reads `score` on a 0-1 rubric and `reason`). Any other answer gets the
default reply, a failing verdict, so an engine that changes the answer fails the rubric.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import yaml
from chassis.fakes.tool import acronym_expand, glossary_lookup
from fake_model_server import Script

from bakeoff.golden import Case, ToolCallSpec, load_cases
from bakeoff.registry import ROOT

__all__ = [
    "JUDGE_DEFAULT",
    "JUDGE_SCRIPT",
    "MODEL_SCRIPT",
    "PROMPTFOO_TESTS",
    "judge_script",
    "main",
    "model_script",
    "promptfoo_tests",
    "rendered",
    "simulate",
    "tool_result",
]

MODEL_SCRIPT = Path("packages/fake-model-server/scripts/golden.yaml")
JUDGE_SCRIPT = Path("packages/fake-model-server/scripts/golden-judge.yaml")
PROMPTFOO_TESTS = Path("evals/promptfoo/tests.yaml")
JUDGE_DEFAULT = {"reason": "The judge has no verdict for this answer.", "pass": False, "score": 0}

_TOOLS: dict[str, Callable[..., dict[str, str | None]]] = {
    "glossary_lookup": glossary_lookup,
    "acronym_expand": acronym_expand,
}
_HEADER = "# Generated from packages/bakeoff/src/bakeoff/golden/cases.yaml. Do not edit.\n"
_REGEN = "# Regenerate: uv run python -m bakeoff.golden.generate --write\n"


def tool_result(call: ToolCallSpec) -> dict[str, str | None]:
    """What the chassis's fake tool returns for `call`."""
    return _TOOLS[call.name](**call.arguments)


def _result_key(call: ToolCallSpec) -> str:
    """The text an `after_tool` rule matches for the result of `call`."""
    result = tool_result(call)
    value = result.get("definition", result.get("expansion"))
    return value if value else next(iter(call.arguments.values()))


def model_script(cases: Sequence[Case]) -> dict[str, Any]:
    plain: list[dict[str, Any]] = []
    after: dict[str, dict[str, Any]] = {}
    for case in cases:
        calls = case.model_calls
        if calls:
            plain.append({"match": case.input, "reply": "", "tool_call": calls[0].as_dict()})
        else:
            plain.append({"match": case.input, "reply": case.reference})
        for i, call in enumerate(calls):
            key = _result_key(call)
            rule: dict[str, Any] = {"after_tool": True, "match": key}
            if i + 1 < len(calls):
                rule |= {"reply": "", "tool_call": calls[i + 1].as_dict()}
            else:
                rule["reply"] = case.reference
            if key in after and after[key] != rule:
                raise ValueError(f"{case.id}: tool result {key!r} already leads elsewhere")
            after[key] = rule
    ordered = sorted(after.values(), key=lambda r: -len(r["match"]))
    return {"model": "fake-model", "rules": plain + ordered, "default_reply": "ok"}


def judge_script(cases: Sequence[Case]) -> dict[str, Any]:
    rules = [
        {
            "match": case.reference,
            "reply": json.dumps(
                {
                    "reason": case.judge_reason,
                    "pass": case.judge_verdict == "pass",
                    "score": 1 if case.judge_verdict == "pass" else 0,
                }
            ),
        }
        for case in sorted(cases, key=lambda c: -len(c.reference))
    ]
    return {"model": "fake-judge", "rules": rules, "default_reply": json.dumps(JUDGE_DEFAULT)}


def promptfoo_tests(cases: Sequence[Case]) -> list[dict[str, Any]]:
    tests: list[dict[str, Any]] = []
    for case in cases:
        asserts: list[dict[str, Any]] = [
            {"type": "icontains", "value": fact, "metric": "contains"} for fact in case.contains
        ]
        asserts += [
            {"type": "not-icontains", "value": word, "metric": "banned"} for word in case.banned
        ]
        asserts.append({"type": "javascript", "value": "file://tool_calls.js", "metric": "tools"})
        asserts.append({"type": "llm-rubric", "value": case.rubric, "metric": "rubric"})
        tests.append(
            {
                "description": f"{case.id} {case.task}",
                "vars": {
                    "input": case.input,
                    "expected_tool_calls": [c.as_dict() for c in case.tool_calls],
                },
                "metadata": {"case_id": case.id, "task": case.task, "label": case.label},
                "assert": asserts,
            }
        )
    return tests


def simulate(case: Case, script: Script, *, compact: bool = False) -> tuple[list[Any], str]:
    """Replay `case` through `script`: the tool calls it asks for and its final reply."""
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": "Rewrite in plain words."},
        {"role": "user", "content": case.input},
    ]
    calls: list[Any] = []
    for _ in range(8):
        rule = script.pick(messages)
        if rule.tool_call is None:
            return calls, rule.reply
        call = ToolCallSpec(rule.tool_call.name, dict(rule.tool_call.arguments))
        calls.append(call.as_dict())
        result = tool_result(call)
        content = json.dumps(result, separators=(",", ":")) if compact else json.dumps(result)
        messages += [
            {"role": "assistant", "content": "", "tool_calls": [{"id": f"c{len(calls)}"}]},
            {"role": "tool", "tool_call_id": f"c{len(calls)}", "content": content},
        ]
    raise ValueError(f"{case.id}: the script loops")


def _dump(data: Any) -> str:
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=100)


def rendered(cases: Sequence[Case]) -> dict[Path, str]:
    """The text of each generated file, keyed by its path relative to the repo root."""
    model = model_script(cases)
    script = Script.model_validate(model)
    for case in cases:
        for compact in (False, True):
            calls, reply = simulate(case, script, compact=compact)
            want = [c.as_dict() for c in case.model_calls]
            if calls != want or reply != case.reference:
                raise ValueError(f"{case.id}: the script gives {calls} {reply!r}")
    return {
        MODEL_SCRIPT: _HEADER
        + _REGEN
        + "# The engines' model for the PoC-7 golden set.\n"
        + _dump(model),
        JUDGE_SCRIPT: _HEADER
        + _REGEN
        + "# The scripted judge for the PoC-7 golden set.\n"
        + _dump(judge_script(cases)),
        PROMPTFOO_TESTS: _HEADER + _REGEN + _dump(promptfoo_tests(cases)),
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m bakeoff.golden.generate")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true")
    mode.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    stale = []
    for rel, text in rendered(load_cases()).items():
        path = ROOT / rel
        if args.write:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
            print(f"wrote {rel}")
        elif not path.is_file() or path.read_text(encoding="utf-8") != text:
            stale.append(str(rel))
    if stale:
        print(f"stale: {', '.join(stale)}; run --write", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
