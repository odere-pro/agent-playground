# 2026-10-28: Snyk Agent Scan in CI, run in a sandbox (W5)

Scope line: Security, "Snyk Agent Scan in CI, run in a sandbox". It supports the evidence for exit criteria 5 and 7: every engine sees the same tool descriptions, from the repo's MCP servers, and CI scans them. It closes no criterion by itself.

## What was built

- `deploy/agent-scan/mcp.json`: the scan config, in the `mcpServers` format the tool reads. It names `fake-mcp-server` and `code-runner`, each started over stdio by `deploy/agent-scan/stdio_server.py`.
- `deploy/agent-scan/stdio_server.py`: serves each package's own `create_server()` object over stdio. Both servers are HTTP only (uvicorn), so the scan needs this launcher. The scanner never calls a tool. It sends `initialize`, `tools/list`, `prompts/list`, and `resources/list` (`agent_scan/mcp_client.py`; no `call_tool` anywhere in the package).
- `deploy/agent-scan/Dockerfile`, `Dockerfile.dockerignore`, `requirements.in`, `requirements.txt`: the sandbox image. The scanner is in `/opt/scan`, every wheel pinned by sha256. The servers' third-party deps are in `/app/.venv`, from `uv.lock`. The image holds no repo source. The build context is an allowlist of manifests and locks.
- `deploy/agent-scan/scan.sh`: builds the image, then runs it twice (see Isolation).
- `.github/workflows/poc07-agent-scan.yml`: runs on push, pull_request, and workflow_dispatch, never `pull_request_target`. It has `permissions: contents: read`, actions pinned by SHA, and `persist-credentials: false`. `SNYK_TOKEN` is set only in the scan step's env. The reports are uploaded with `if: always()`.
- `tests/test_poc07_agent_scan_static.py`: 13 offline checks.

## What the tool needs (read from the 0.6.9 source)

- **A token, for `scan`.** `verify_api.py` reads `SNYK_TOKEN` and sends `Authorization: token <SNYK_TOKEN>`. With no token (and no push key, and no `SNYK_CLI_USE`), it raises `SnykTokenError` and exits 1. The old Invariant path is gone.
- **Network, for `scan`.** The descriptions are scored remotely, not locally. The default is `--analysis-url https://api.snyk.io/hidden/mcp-scan/analysis-machine?version=2026-07-10`. With a token, it is rewritten to `/hidden/mcp-scan/cli/analysis-machine`. The only host is `api.snyk.io`.
- **Nothing remote, for `inspect`.** `inspect` starts the servers and lists what they serve. It sends nothing to the analysis API (`run_scan(mode="inspect")` stops before `analyze`). So `inspect` runs with `--network=none` and no token.
- **Stdio from a config file: yes.** `StdioServer` takes `command`, `args`, and `env`. The mcp stdio client gives the subprocess only HOME, LOGNAME, PATH, SHELL, TERM, and USER (`mcp.client.stdio.get_default_environment`). That is why the launcher sets its own `sys.path`.
- **Consent.** A run with no prompt needs `--dangerously-run-mcp-servers`, and `--ci` requires it. Without it, the scanner asks per server on stdin, and EOF means decline.
- **What the payload carries.** The `scan` payload carries the server config (command and args, no env here), the tool, prompt, and resource descriptions, and the hostname and username. `scan.sh` sets `--hostname agent-scan`. The user is uid 65534.

## What ran here

No Docker daemon in this environment (`docker info`: no `/var/run/docker.sock`). So `scan.sh` and the image build did not run. The checks below ran outside a container.

The scanner, installed in a scratch venv (`snyk-agent-scan==0.6.9`), without `--dangerously-run-mcp-servers` and with stdin closed:

```text
$ snyk-agent-scan inspect --json --no-skills --suppress-mcpserver-io true deploy/agent-scan/mcp.json </dev/null
  [1] fake-mcp-server
      command: python deploy/agent-scan/stdio_server.py fake-mcp-server
      Allow Agent Scan to start 'fake-mcp-server'? [y/N]:       Declined: 'fake-mcp-server' will not be started.
  [2] code-runner
      command: python deploy/agent-scan/stdio_server.py code-runner
      Allow Agent Scan to start 'code-runner'? [y/N]:       Declined: 'code-runner' will not be started.
Proceeding with 0 of 2 MCP servers. Skipped: 2.
rc=0   (JSON: both servers parsed from mcp.json, error category "user_declined")

$ snyk-agent-scan scan --no-skills --suppress-mcpserver-io true deploy/agent-scan/mcp.json </dev/null
Snyk Agent Scan v0.6.9
To use Agent Scan, set the SNYK_TOKEN environment variable. To get a token, go
to https://app.snyk.io/account (API Token -> KEY -> click to show).
rc=1
```

The config parses, and both servers are found. A run with `--dangerously-run-mcp-servers` outside a sandbox was not allowed in this session, so the scanner never started the servers here.

The launcher over real stdio, with a fastmcp client and the same minimal env the scanner gives:

```text
fake-mcp-server ['glossary_lookup', 'acronym_expand', 'note_write', 'unlisted_probe'] desc chars: [74, 61, 169, 78]
code-runner ['run_python'] desc chars: [310]
```

## Isolation

Both `docker run`s in `scan.sh` use `--rm --read-only --cap-drop=ALL --security-opt no-new-privileges --user 65534:65534`. They have a 64 MB `noexec,nosuid,nodev` tmpfs at `/tmp` (HOME), `--pids-limit=256`, and `--memory=1g`. The repo is mounted read-only at `/repo`. No env file goes in. The only variable passed is `SNYK_TOKEN`, by name.

1. `inspect`: `--network=none`, no token. The repo's server code and its deps run here. Report: `inspect.json`. It runs on fork PRs too.
2. `scan`: `--network=bridge` plus `SNYK_TOKEN`. Docker cannot limit egress to one host, so this run can reach any host, not only `api.snyk.io`. suggested: an egress proxy that allows only `api.snyk.io`, if this becomes a gate. Report: `scan.json`. `--ci` makes it exit non-zero on a finding or a server that fails to start.

Residual risk: in run 2 the servers run as the same uid as the scanner. So a server, or a compromised dependency, could read the token from `/proc/<scanner pid>/environ` and send it out. Three things limit this. GitHub gives no secret to a fork PR. The checkout keeps no GitHub token. The job has `contents: read` only. Run 1 exists so that every PR still gets the server inspection with no network and no token. Not gVisor: `runsc` on a hosted runner is still open (`remote-lane.yml`).

## Needs CI or a token (unverified)

- The image build and both `docker run`s. They need Docker with BuildKit, which reads `Dockerfile.dockerignore`. The `!packages/*/pyproject.toml` re-includes under `*` are untested.
- A real `scan` result: it needs `SNYK_TOKEN` as a repo secret. Until it is set, the job prints `::notice title=agent-scan::SNYK_TOKEN is not set ...` and exits 0 after `inspect`.
- Whether `--ci` fails on today's descriptions. `note_write` is a write tool, and `run_python` runs code; either may be flagged. `unlisted_probe` exists to test allow lists and may read as suspicious.

## Findings

None yet. No scan has reached the analysis API.

## Not covered

- `chassis` builds a FastMCP server (`adapters/mcp/server.py`), but it re-serves the `ToolPort` list, whose descriptions come from the scanned servers. The test lists it under `NOT_SCANNED` with that reason.
- The code-runner dispatcher (`create_dispatch_server`) serves the same tool, `run_python`, but `dispatch.py` keeps its own copy of the instructions and field descriptions, which can drift from `server.py`. It needs a Kubernetes client to build, so it is not scanned. suggested: one shared description constant, or a scan entry with a stub dispatcher.
- The upstream servers behind the MCP gateway on kind are only the two above. Third-party servers added later go in `mcp.json`. The static test then fails until they are listed.
