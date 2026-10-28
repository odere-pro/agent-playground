#!/usr/bin/env bash
# PoC-7 stack: Langfuse v4, the OTel Collector, Jaeger. Usage: stack.sh up|down|digests|env
# Every credential is generated into .env.poc07 (mode 600, git-ignored) and never printed.
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

case "${1:-}" in
  up)
    make_env
    "${compose[@]}" up -d --wait --quiet-pull
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
    echo "usage: $0 up|down|digests|env" >&2
    exit 2
    ;;
esac
