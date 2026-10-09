#!/usr/bin/env bash
# PoC-6 on a Mac: the runs that need a host this container does not have. A stub: the real
# commands arrive with a later PoC-6 task.
#
#   scripts/poc06_mac.sh            print this usage and exit 2
set -euo pipefail

usage() {
  cat >&2 <<'USAGE'
usage: scripts/poc06_mac.sh COMMAND [ARGS]

PoC-6 runs on a Mac. Not built yet: no command exists.
Run it with `make poc06-mac ARGS="..."`.
USAGE
}

usage
exit 2
