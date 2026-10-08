#!/usr/bin/env bash
# mc against project poc04's MinIO; the root password comes from .env.poc04 and is never printed.
S=$(dirname "$0")
docker run --rm --name poc04-mc-drill --network poc04 --env-file "$(git rev-parse --show-toplevel)/deploy/compose/.env.poc04" \
  -e MC_CONFIG_DIR=/tmp/mc -v "$S:/drill:ro" --entrypoint sh \
  pgsty/mc:RELEASE.2026-09-16T00-00-00Z@sha256:cfc83108c3abb371f8fb84d99c1fdc88f8c237e022409b0081fb7c0a3be634dd \
  -c 'export MC_HOST_local="http://poc04-admin:${MINIO_ROOT_PASSWORD}@minio:9000"; '"$*"
