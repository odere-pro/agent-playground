#!/usr/bin/env bash
# detect-secrets over every tracked file. `make lint-extra` and CI run it.
# The hook exits 1 on a new secret. It exits 3 when it only moved line numbers in
# .secrets.baseline, which any edit above an allowlisted line causes. A moved line is no
# finding: entries match on file and hash, so the gate passes and the baseline is restored.
# --refresh keeps the new line numbers (`make lint-fix`). It never accepts a new secret.
set -uo pipefail
cd "$(git rev-parse --show-toplevel)" || exit 1

refresh=false
if [ "${1:-}" = "--refresh" ]; then
  refresh=true
fi

files=()
while IFS= read -r -d '' path; do
  files+=("$path")
done < <(git ls-files -z)

uv run detect-secrets-hook --baseline .secrets.baseline "${files[@]}"
status=$?

if [ "$status" -ne 3 ]; then
  exit "$status"
fi
if [ "$refresh" = true ]; then
  echo "check-secrets: no new secret; .secrets.baseline line numbers refreshed"
else
  git checkout -- .secrets.baseline
  echo "check-secrets: no new secret; baseline line numbers moved ('make lint-fix' refreshes them)"
fi
