#!/usr/bin/env bash
# Run pytest with no network and no keys. `make test` and CI call this, so a leaked
# key or a stray real call can never make a test pass.
set -euo pipefail
cd "$(dirname "$0")/.."
# Every credential suffix this repo uses (docs/guides/testing.md, "Offline is enforced").
strip='(_API_KEY|_TOKEN|_SECRET|_PASSWORD|_SECRET_KEY|_PUBLIC_KEY|_MASTER_KEY|_ACCESS_KEY|_PROVIDER_KEY|_VIRTUAL_KEY|_LITELLM_KEY)$'
for v in $(env | cut -d= -f1 | grep -E "$strip" || true); do
  unset "$v"
done
# --record-mode=none: model cassettes replay only, whatever `addopts` says. A later
# `--record-mode` in "$@" wins, which is how `make record-cassettes` re-records (offline too).
# No --block-network: it also blocks the Unix sockets the sidecar lane uses.
exec uv run pytest --strict-markers --disable-socket --allow-unix-socket --record-mode=none "$@"
