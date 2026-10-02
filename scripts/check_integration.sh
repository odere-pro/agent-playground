#!/usr/bin/env bash
# The `network` tests (PoC-4): real adapters against testcontainers, and the PoC-4 scenario tests
# that need sockets. Sockets on, keys still stripped. Needs Docker; a test skips when Docker is
# not reachable. Compose and kind drills also need POC04_STACK=1 or POC04_KIND=1.
set -euo pipefail
cd "$(dirname "$0")/.."
for v in $(env | cut -d= -f1 | grep -E '(_API_KEY|_TOKEN|_SECRET|_PASSWORD)$' || true); do
  unset "$v"
done
paths=(packages/chassis/tests/integration pocs/poc-04-stateless-scalable/tests)
status=0
uv run pytest -m network --record-mode=none "${paths[@]}" "$@" || status=$?
if [ "$status" -eq 5 ]; then
  echo "check_integration: no network tests collected yet"
  exit 0
fi
exit "$status"
