#!/usr/bin/env bash
# Count the tokens of one JudgeEvaluator call for a typical simplifier answer.
#   bash pocs/poc-07-cross-cutting-decisions/notes/evals/count_judge_tokens.sh
# Python tiktoken downloads its vocabulary at run time (blocked here: 403), so the count uses
# npm js-tiktoken@1.0.21, which ships the o200k_base and cl100k_base ranks in the package.
# Chat framing: 3 tokens per message plus 3 (OpenAI's documented rule), included.
set -euo pipefail
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
uv run python -c '
import json
from chassis.adapters.evaluators import judge_messages
from chassis.ports.evaluator import EvalCase
case = EvalCase(
    input="simplify: The SLM was released by Acme in 2026, thereby reducing operating costs by approximately 30 percent.",
    output="Acme put out the SLM in 2026. It cut costs by about 30 percent.",
)
reply = json.dumps({"score": 1.0, "reason": "The answer keeps every fact and adds none."})
print(json.dumps({"messages": [m.content for m in judge_messages(case)], "reply": reply}))
' > "$work/judge.json"
(cd "$work" && npm install --silent --no-fund --no-audit js-tiktoken@1.0.21 >/dev/null)
(cd "$work" && node -e '
const { Tiktoken } = require("js-tiktoken/lite");
const d = require("./judge.json");
for (const n of ["o200k_base", "cl100k_base"]) {
  const e = new Tiktoken(require("js-tiktoken/ranks/" + n));
  const p = d.messages.reduce((a, m) => a + e.encode(m).length + 3, 0) + 3;
  console.log(`${n}: prompt ${p} tokens, reply ${e.encode(d.reply).length} tokens`);
}')
