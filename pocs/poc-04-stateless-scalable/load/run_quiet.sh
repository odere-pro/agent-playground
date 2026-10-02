#!/usr/bin/env bash
# One clean pass, 60 s per scenario, on a quiet host (2026-10-01 rerun): each engine at 1, 2, 4
# pairs, then the hop (1 user), the fixed-rate CPU runs (10 RPS on 1 pair), and idempotency.
# Results merge into notes/load/passes/quiet/results.json after every scenario; raw CSVs land in
# notes/load/raw/ and move to passes/quiet/raw/ at the end.
set -uo pipefail
ROOT=$(cd "$(dirname "$0")/../../.." && pwd)
LOAD="$ROOT/pocs/poc-04-stateless-scalable/notes/load"
OUT="$LOAD/passes/quiet/results.json"
M() { uv run python "$ROOT/pocs/poc-04-stateless-scalable/load/run_matrix.py" --duration-s 60 \
  --results "$OUT" "$@" 2>&1 | grep -E "^echo-|^hop|run_matrix"; }
for engine in echo-python echo-pydanticai echo-langgraph echo-typescript; do
  echo "== $engine $(date -u +%T) $(uptime | sed 's/.*load/load/')"
  M --engine "$engine" --only main
done
echo "== hop $(date -u +%T)"; M --only hop
echo "== rate $(date -u +%T)"; M --only rate
echo "== idem $(date -u +%T)"; M --only idem
mv "$LOAD/raw" "$LOAD/passes/quiet/raw"
"$ROOT/deploy/compose/scale.sh" down >/dev/null 2>&1
echo "== done $(date -u +%T)"
