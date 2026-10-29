#!/usr/bin/env bash
# PoC-7 stack: Langfuse v4, the OTel Collector, Jaeger.
# Usage: stack.sh up|wait|down|digests|env|logs
# Every credential is generated into .env.poc07 (mode 600, git-ignored) and never printed.
# `wait` polls the three endpoints from the host: the jaeger and collector images have no shell,
# so they carry no in-container healthcheck. `logs` prints log tails with every env file value
# masked, for CI on failure.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
env_file="$here/.env.poc07"
compose=(docker compose -p poc07 --env-file "$env_file" -f "$here/docker-compose.yaml")

make_env() {
  [[ -f "$env_file" ]] && return 0
  umask 077
  local pk sk
  pk="pk-lf-$(openssl rand -hex 16)"
  sk="sk-lf-$(openssl rand -hex 16)"
  {
    echo "POSTGRES_PASSWORD=$(openssl rand -hex 16)"
    echo "CLICKHOUSE_PASSWORD=$(openssl rand -hex 16)"
    echo "MINIO_ROOT_PASSWORD=$(openssl rand -hex 16)"
    echo "REDIS_AUTH=$(openssl rand -hex 16)"
    echo "NEXTAUTH_SECRET=$(openssl rand -hex 32)"
    echo "LANGFUSE_SALT=$(openssl rand -hex 16)"
    echo "LANGFUSE_ENCRYPTION_KEY=$(openssl rand -hex 32)"
    echo "LANGFUSE_PUBLIC_KEY=$pk"
    echo "LANGFUSE_SECRET_KEY=$sk"
    echo "LANGFUSE_AUTH=$(printf '%s:%s' "$pk" "$sk" | base64 -w0)"
  } >"$env_file"
}

# Polls each URL from the host until it answers 2xx, or fails after POC07_WAIT_S (default 600).
wait_ready() {
  local deadline=$((SECONDS + ${POC07_WAIT_S:-600}))
  local url
  for url in \
    http://127.0.0.1:3000/api/public/health \
    http://127.0.0.1:16686/ \
    http://127.0.0.1:13133/; do
    until curl -fsS -o /dev/null -m 5 "$url" 2>/dev/null; do
      if ((SECONDS >= deadline)); then
        echo "stack.sh wait: $url did not answer in time" >&2
        return 1
      fi
      sleep 3
    done
    echo "ready: $url"
  done
}

# Masks every value of the env file, and anything shaped like a Langfuse key or a Basic header,
# in stdin. The values are read inside Python and never echoed.
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
shapes = re.compile(r"(pk|sk)-lf-[0-9A-Za-z-]+|Basic [A-Za-z0-9+/=]+")
for line in sys.stdin:
    for value in secrets:
        line = line.replace(value, "***")
    sys.stdout.write(shapes.sub("***", line))
' "$env_file"
}

case "${1:-}" in
  up)
    make_env
    "${compose[@]}" up -d --wait --quiet-pull
    ;;
  wait)
    wait_ready
    ;;
  logs)
    # Tails of every service's log, redacted. Never prints the env file.
    "${compose[@]}" logs --no-color --tail "${POC07_LOG_TAIL:-200}" 2>&1 | redact
    ;;
  down)
    "${compose[@]}" down -v --remove-orphans
    rm -f "$env_file"
    ;;
  digests)
    # Prints image@digest for each image, to pin them in docker-compose.yaml.
    make_env
    "${compose[@]}" config --images | sort -u | while read -r image; do
      docker buildx imagetools inspect "$image" --format '{{.Name}}@{{.Manifest.Digest}}'
    done
    ;;
  env)
    # The names the network tests read, with the path of the file that holds the values.
    echo "POC07_ENV_FILE=$env_file"
    ;;
  *)
    echo "usage: $0 up|wait|down|digests|env|logs" >&2
    exit 2
    ;;
esac
