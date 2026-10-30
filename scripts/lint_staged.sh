#!/usr/bin/env bash
# The fast part of `make lint-extra`, on staged files only. The pre-commit hook runs it, so a
# typo, a secret, or a shell or YAML error fails the commit instead of the CI job after a push.
# `make lint-fix` repairs what can be repaired.
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"

staged=()
while IFS= read -r -d '' path; do
  staged+=("$path")
done < <(git diff --cached --name-only --diff-filter=ACMR -z)
[ "${#staged[@]}" -eq 0 ] && exit 0

# Keep each linter to the paths `make lint-extra` gives it.
pick() {
  local pattern=$1
  local path
  for path in "${staged[@]}"; do
    if [[ $path =~ $pattern ]]; then
      printf '%s\0' "$path"
    fi
  done
}

status=0
run() {
  local pattern=$1
  shift
  local files=()
  while IFS= read -r -d '' path; do
    files+=("$path")
  done < <(pick "$pattern")
  if [ "${#files[@]}" -gt 0 ]; then
    uv run "$@" "${files[@]}" || status=1
  fi
}

run '^(docs|packages|pocs|deploy|scripts|\.github)/' codespell
run '\.sh$' shellcheck -x -S warning
run '^(\.github|deploy)/.*\.ya?ml$|^pocs/[^/]+/tests/fixtures/.*\.ya?ml$' \
  yamllint --strict -c .yamllint.yaml
run '.' detect-secrets-hook --baseline .secrets.baseline

if [ "$status" -ne 0 ]; then
  echo "lint-staged: fix the files above; 'make lint-fix' repairs typos and formatting" >&2
fi
exit "$status"
