#!/usr/bin/env bash
# PoC-8 stack: three chassis, the fake model server, Valkey, Prometheus, Grafana.
# Usage: stack.sh up|wait|down|digests|env|logs
# Every credential is generated into .env.poc08 (mode 600, git-ignored) and never printed:
# the run token of the static auth adapter, the Valkey password, the Grafana admin password.
# `up` builds our two images, then starts the project `poc08`. `wait` polls every endpoint from
# the host: the Prometheus and Grafana images carry no healthcheck here. `logs` prints log tails
# with every env file value masked, for CI on failure. `down` removes project poc08 only.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
env_file="$here/.env.poc08"
compose=(docker compose -p poc08 --env-file "$env_file" -f "$here/docker-compose.yaml")

make_env() {
  [[ -f "$env_file" ]] && return 0
  (
    umask 077
    {
      echo "POC08_RUN_TOKEN=poc08-$(openssl rand -hex 24)"
      echo "VALKEY_PASSWORD=$(openssl rand -hex 24)"
      echo "GRAFANA_ADMIN_PASSWORD=$(openssl rand -hex 24)"
    } >"$env_file"
  )
}

# Polls each URL from the host until it answers 2xx, or fails after POC08_WAIT_S (default 300).
wait_ready() {
  local deadline=$((SECONDS + ${POC08_WAIT_S:-300}))
  local url
  for url in \
    http://127.0.0.1:18088/ready \
    http://127.0.0.1:18089/ready \
    http://127.0.0.1:18090/ready \
    http://127.0.0.1:19090/-/ready \
    http://127.0.0.1:13000/api/health; do
    until curl -fsS -o /dev/null -m 5 "$url" 2>/dev/null; do
      if ((SECONDS >= deadline)); then
        echo "stack.sh wait: $url did not answer in time" >&2
        return 1
      fi
      sleep 2
    done
    echo "ready: $url"
  done
}

# Masks every value of the env file, and any bearer value, in stdin. The values are read inside
# Python and never echoed.
redact() {
  python3 -I -c '
import re, sys
secrets = []
try:
    with open(sys.argv[1]) as f:
        for line in f:
            _, sep, value = line.strip().partition("=")
            if sep and len(value) >= 8:
                secrets.append(value)
except FileNotFoundError:
    pass
shapes = re.compile(r"(Bearer|Basic) [A-Za-z0-9+/=._-]+")
for line in sys.stdin:
    for value in secrets:
        line = line.replace(value, "***")
    sys.stdout.write(shapes.sub(r"\1 ***", line))
' "$env_file"
}

case "${1:-}" in
  up)
    make_env
    "${compose[@]}" build --quiet
    "${compose[@]}" up -d --wait --quiet-pull
    ;;
  wait)
    wait_ready
    ;;
  logs)
    # Tails of every service's log, redacted. Never prints the env file.
    "${compose[@]}" logs --no-color --tail "${POC08_LOG_TAIL:-200}" 2>&1 | redact
    ;;
  down)
    # Compose needs every `${VAR:?}` set even to remove; a fresh file is fine for that.
    make_env
    "${compose[@]}" down -v --remove-orphans
    rm -f "$env_file"
    ;;
  digests)
    # Prints image@digest for each pulled image, to pin them in docker-compose.yaml. Our own
    # images (agent-platform/*) are built here and have no registry digest.
    make_env
    "${compose[@]}" config --images | sort -u | grep -v '^agent-platform/' | while read -r image; do
      docker buildx imagetools inspect "$image" --format '{{.Name}}@{{.Manifest.Digest}}'
    done
    ;;
  env)
    # The name the drill and the latency script read, with the path of the file that holds the
    # values. Never the values.
    echo "POC08_ENV_FILE=$env_file"
    ;;
  *)
    echo "usage: $0 up|wait|down|digests|env|logs" >&2
    exit 2
    ;;
esac
