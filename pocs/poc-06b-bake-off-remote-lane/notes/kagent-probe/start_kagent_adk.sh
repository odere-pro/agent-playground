#!/usr/bin/env bash
# Start the kagent Python ADK runtime locally. Usage: start_kagent_adk.sh LOGFILE [extra env VAR=val ...]
log="$1"; shift
env KAGENT_NAME=probe-agent KAGENT_NAMESPACE=kagent KAGENT_API_URL=http://127.0.0.1:1 \
  KAGENT_GATEWAY_URL=http://127.0.0.1:1 "$@" \
  nohup /tmp/poc06-kprobe/venv-kagent/bin/python /tmp/poc06-kprobe/scripts/run_kagent_adk.py \
  /tmp/poc06-kprobe/run/config 18080 > "$log" 2>&1 &
