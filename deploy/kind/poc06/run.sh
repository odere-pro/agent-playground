#!/usr/bin/env bash
# PoC-6 on kind: the remote-lane bake-off engines (echo-claude-agent, echo-smolagents, kagent).
# A stub: the real verbs arrive with tasks B2 to B5 of docs/plans/2026-10-09-poc-06-bake-off.md.
#
#   deploy/kind/poc06/run.sh        print this usage and exit 2
set -euo pipefail

usage() {
  cat >&2 <<'USAGE'
usage: deploy/kind/poc06/run.sh VERB [ARGS]

PoC-6 on kind. Not built yet: no verb exists. The manifests so far are in deploy/kind/poc06/remote/.
Run it with `make kind-poc06 ARGS="..."`.
USAGE
}

usage
exit 2
