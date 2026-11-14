# shellcheck shell=bash
# The kctl, log, and die helpers of the kind scripts, in one place (review finding R5b-12).
# Sourced, never run: each script under deploy/kind sources this file with its own absolute path.
#
# A script has, above the `source` line, strict mode and two plain assignments:
#   set -euo pipefail
#   CONTEXT=kind-<cluster>   the one context `kctl` may use
#   LOG_PREFIX=<name>        what `log` and `die` print in brackets
#
# This file sets neither name and takes no default for either. A default would let the caller's
# environment pick the cluster. The guard below stops a script that forgot one, at the `source`.
# It cannot tell a script's value from an exported one, so the plain assignments do that work
# (pocs/poc-05-sandboxed/tests/test_poc05_kind_lib.py checks them).
#
# Every credential rule in the scripts still applies: no secret in argv, no secret in a log line.
# Never add `set -x` or any logging of arguments here. `log` and `die` print what they are given,
# and nothing else, and `kctl` passes its arguments to kubectl without showing them.

: "${CONTEXT:?set CONTEXT in the script before sourcing lib.sh}"
: "${LOG_PREFIX:?set LOG_PREFIX in the script before sourcing lib.sh}"

kctl() { kubectl --context "$CONTEXT" "$@"; }
log() { printf '[%s] %s\n' "$LOG_PREFIX" "$*" >&2; }
die() { log "$*"; exit 1; }

# write_key_manifest DIR ALIAS ROUTE...: the key manifest of one minted LiteLLM key (059 H-15),
# DIR/ALIAS.json, which `agentctl new --key-manifest` reads. suggested: version 1, the fields
# version, key_alias, routes (the key's `models`), written_at (UTC). Names only: never pass the
# key. Written to a temp file in DIR, then moved, so a reader never sees half a file.
write_key_manifest() {
  local dir=$1 alias=$2 tmp
  shift 2
  mkdir -p "$dir"
  tmp=$(mktemp "$dir/.$alias.XXXXXX")
  if ! jq -n --arg alias "$alias" --arg at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
    '{version: 1, key_alias: $alias, routes: $ARGS.positional, written_at: $at}' \
    --args "$@" >"$tmp"; then
    rm -f "$tmp"
    die "could not write the key manifest for $alias"
  fi
  mv "$tmp" "$dir/$alias.json"
}
