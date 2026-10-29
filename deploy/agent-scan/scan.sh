#!/usr/bin/env bash
# Snyk Agent Scan over the repo's MCP servers, in a sandbox (PoC-7, W5).
#
#   deploy/agent-scan/scan.sh            # reports go to $AGENT_SCAN_OUT (default /tmp/agent-scan)
#
# Two runs of one image (Dockerfile here), each with a read-only root, no capabilities, no new
# privileges, uid 65534, a small noexec /tmp, and PID and memory limits. Only the scan inputs are
# mounted, read-only, at their paths under /repo: deploy/agent-scan (mcp.json, the launcher) and
# the src folders of fake-mcp-server and code-runner. Never the whole checkout, so no git-ignored
# file (a generated .env, for one) is in reach. No repo secret and no env file goes in.
#
# 1. inspect: starts each server in mcp.json over stdio and lists its tools, prompts, and
#    resources. --network=none and no token: the repo's server code never runs with either.
#    Report: inspect.json. Runs everywhere, fork PRs included.
# 2. scan: the same servers, then the descriptions go to Snyk's analysis API for scoring
#    (https://api.snyk.io/hidden/mcp-scan/cli/analysis-machine). Needs SNYK_TOKEN and egress to
#    api.snyk.io. Docker cannot limit egress to one host, so this run has the default bridge
#    network. Report: scan.json. With --ci the scanner exits non-zero on a finding or a server
#    that fails to start; this script keeps the report and passes that exit code on.
#    Without SNYK_TOKEN it prints a notice and exits 0 after step 1.
#
# The token is passed by name (-e SNYK_TOKEN), never on the command line. The scanner hands a
# stdio server only HOME, LOGNAME, PATH, SHELL, TERM, and USER (the mcp stdio client), but the
# servers run as the same uid as the scanner, so a server could read the scanner's environment
# from /proc. That is why step 1 exists, and why CI never gives the token to a fork PR.
#
# `--dangerously-run-mcp-servers` is the scanner's own flag for a run with no prompt; it lets
# the scanner start the stdio servers named in mcp.json, here only inside the container.
set -euo pipefail

repo=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
image=${AGENT_SCAN_IMAGE:-agent-platform/agent-scan:poc07}
out=${AGENT_SCAN_OUT:-${TMPDIR:-/tmp}/agent-scan}
config=deploy/agent-scan/mcp.json
mkdir -p "$out"

DOCKER_BUILDKIT=1 docker build -q -f "$repo/deploy/agent-scan/Dockerfile" -t "$image" "$repo" >/dev/null

echo "agent-scan: inspect (no network, no token) -> $out/inspect.json"
docker run --rm --network=none --read-only --cap-drop=ALL --security-opt no-new-privileges \
  --user 65534:65534 --tmpfs /tmp:rw,noexec,nosuid,nodev,size=64m --pids-limit=256 --memory=1g \
  -v "$repo/deploy/agent-scan:/repo/deploy/agent-scan:ro" \
  -v "$repo/packages/fake-mcp-server/src:/repo/packages/fake-mcp-server/src:ro" \
  -v "$repo/packages/code-runner/src:/repo/packages/code-runner/src:ro" \
  -w /repo --hostname agent-scan \
  "$image" inspect --json --no-skills --dangerously-run-mcp-servers \
  --suppress-mcpserver-io true --server-timeout 30 "$config" >"$out/inspect.json"

if [ -z "${SNYK_TOKEN:-}" ]; then
  echo "::notice title=agent-scan::SNYK_TOKEN is not set (a fork PR, or no secret yet): skipping the remote scan. The inspect report is in $out/inspect.json."
  exit 0
fi

echo "agent-scan: scan (egress for api.snyk.io, SNYK_TOKEN) -> $out/scan.json"
rc=0
docker run --rm --network=bridge --read-only --cap-drop=ALL --security-opt no-new-privileges \
  --user 65534:65534 --tmpfs /tmp:rw,noexec,nosuid,nodev,size=64m --pids-limit=256 --memory=1g \
  -v "$repo/deploy/agent-scan:/repo/deploy/agent-scan:ro" \
  -v "$repo/packages/fake-mcp-server/src:/repo/packages/fake-mcp-server/src:ro" \
  -v "$repo/packages/code-runner/src:/repo/packages/code-runner/src:ro" \
  -w /repo --hostname agent-scan -e SNYK_TOKEN \
  "$image" scan --json --ci --no-skills --dangerously-run-mcp-servers \
  --suppress-mcpserver-io true --server-timeout 30 "$config" >"$out/scan.json" || rc=$?
echo "agent-scan: scan exit code $rc"
exit "$rc"
