#!/usr/bin/env bash
# Run pytest with no network and no keys. `make test` and CI call this, so a leaked
# key or a stray real call can never make a test pass.
set -euo pipefail
cd "$(dirname "$0")/.."
for v in $(env | cut -d= -f1 | grep -E '(_API_KEY|_TOKEN|_SECRET|_PASSWORD)$' || true); do
  unset "$v"
done
# --record-mode=none: model cassettes replay only, whatever `addopts` says. A later
# `--record-mode` in "$@" wins, which is how `make record-cassettes` re-records (offline too).
# No --block-network: it also blocks the Unix sockets the sidecar lane uses.
exec uv run pytest --disable-socket --allow-unix-socket --record-mode=none "$@"
