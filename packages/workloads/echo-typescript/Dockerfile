# The echo-typescript workload: `handle` served over A2A by @a2a-js/sdk. Self-contained, so the
# build context is this folder:
#   docker build -t echo-typescript packages/workloads/echo-typescript
# Base pinned by digest (deploy/compose/SECURITY.md, section 2), resolved on 2026-10-01. Bump on
# purpose, in its own commit:
#   docker buildx imagetools inspect node:24-slim
ARG NODE_IMAGE=node:24-slim@sha256:0e0ff40c39bc087845bfb27465a0df4ea419520094bc35842ff83dd8cbe6f9b6

FROM ${NODE_IMAGE} AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --ignore-scripts
COPY tsconfig.json tsconfig.build.json ./
COPY schemas schemas
COPY src src
RUN npm run build && npm prune --omit=dev --ignore-scripts

FROM ${NODE_IMAGE} AS runtime
RUN groupadd --gid 10001 workload && useradd --uid 10001 --gid workload --create-home workload
WORKDIR /app
COPY --from=build --chown=workload:workload /app/package.json ./
COPY --from=build --chown=workload:workload /app/node_modules node_modules
COPY --from=build --chown=workload:workload /app/dist dist
# No key, ever: the model URL is the chassis's proxy, which holds the credential.
# 127.0.0.1: the chassis reaches the workload on localhost (sidecar lane). A Compose file that
# runs it in its own network namespace sets HOST=0.0.0.0 and ALLOW_ANY_HOST=1 there, on purpose;
# without ALLOW_ANY_HOST=1 a non-loopback HOST is refused at start (src/host.ts).
ENV NODE_ENV=production HOST=127.0.0.1 PORT=9000 CHASSIS_MODEL_URL=http://127.0.0.1:8090/v1
USER workload
EXPOSE 9000
HEALTHCHECK --interval=5s --timeout=3s --retries=12 \
    CMD ["node", "-e", "fetch(`http://127.0.0.1:${process.env.PORT}/.well-known/agent-card.json`).then(r => process.exit(r.ok ? 0 : 1), () => process.exit(1))"]
ENTRYPOINT ["node", "dist/src/main.js"]
