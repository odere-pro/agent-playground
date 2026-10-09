---
name: poc-05-operate
description: Bring up, test, read, rotate, and tear down the PoC-5 kind cluster poc05 (gVisor, NetworkPolicy, admission). Use when running the PoC-5 kind suites, checking the cluster after a change, or fixing a cluster that will not start.
---
# Operate PoC-5 on kind

Every command below was run on the Mac and is pasted in the notes, unless it says "not yet run". Evidence: `pocs/poc-05-sandboxed/notes/2026-10-02-bring-up.md` (bring-up), `2026-10-08-sidecar-suite.md`, `2026-10-08-remote-suite.md`. Problems: `docs/guides/poc-05-runbooks.md`.

## When to use
- Run the kind tier or the remote-lane suite after a change to `deploy/kind/poc05/`, a chassis adapter, or an image.
- Check a pod, a Secret's presence, or a log on `kind-poc05`.
- Not for the offline gate: `make check` and `make test-poc POC=05` need no cluster.

## Rules
- One agent on Docker or kind at a time. Never touch other stacks (`paligo-*`, opensearch).
- Every `kubectl` call pins `--context kind-poc05`; `run.sh` does this for you.
- No secret value in a file, argv, or output. Read a key into the env in a subshell (`run.sh with-gateway`). Print logs only through `run.sh logs`.
- Never edit a test to make a refusal pass. See the runbook "A refusal test passes when it should fail".

## Prerequisites
- Docker Desktop with about 7.75 GiB for the VM. The stack uses about 3.3 GiB working set on the node (bring-up, item 10). `up` prints the free memory first.
- kind v0.33.0 and kubectl v1.36.1 (the 2026-10-08 run). `jq` and `openssl` for `seed.sh`.
- The PoC-4 cluster `poc04` deleted: `run.sh` refuses to create while it runs.
- `PATH=/opt/homebrew/bin:$PATH`, so the Homebrew tools win.
- `UV_NO_SYNC=1` when `uv` cannot install `hadolint-py`; uv then runs the existing env.

## Bring up
```
deploy/kind/poc05/run.sh up
```
From nothing it took 297 s; on a running cluster it converges in about 54 to 87 s. It runs, in order: memory preflight, `create` (gVisor), `smoke`, `admission` (with its canary), `build`, `load`, `seed.sh base`, the platform, the code-runner Sandbox, `seed.sh keys`, the remote Sandbox, the chassis pods, `pods`, and `request`.

Ready looks like this:
- nine `PASS` smoke lines;
- `admission: canary refused by agent-trust-rule, twin admitted`;
- four `request` lines, each `HTTP 200` (`agent-echo` and `chassis-echo-remote`, "hello" and "glossary");
- `up done in N s`;
- `kubectl --context kind-poc05 get pods -A`: 20 pods Running and Ready, `minio-init` Completed.

After `run.sh build`, running pods keep the old image (the tag stays `poc05`). Move them with `rollout restart` for Deployments and `delete pod` for the two Sandboxes, as the deployer (bring-up, "2026-10-09: rerun").

## Run the suites
Offline, no cluster (the kind tests skip):
```
make test-poc POC=05
```

The kind tier, every PoC-5 kind test, through the gateway wrapper:
```
POC05_KIND=1 deploy/kind/poc05/run.sh with-gateway uv run pytest -m network pocs/poc-05-sandboxed/tests -q -rs
```
Last good: `90 passed, 2 skipped` in about 2 minutes (2026-10-09). The two skips are contract-suite fixtures the gateway binding does not provide.

The remote lane and the code runner only (what CI will run):
```
deploy/kind/poc05/run.sh test-remote
```
Last good: `17 passed` in about 96 s.

One file:
```
POC05_KIND=1 uv run pytest -m network pocs/poc-05-sandboxed/tests/test_poc05_kind_hardreq1.py -q -rs
```
One test by name: add `-k <test name>` to the line above (not yet run in this form). A test that calls the gateway needs the `with-gateway` wrapper, or it skips.

## Run the demo
`pocs/poc-05-sandboxed/demo/demo.sh` (added in T26).

## Read results
- A refusal test passes only when its paired allowed control passed in the same test. A skip is not a pass: read the `-rs` lines.
- `run.sh pods`: every pod and Sandbox in the PoC-5 namespaces.
- `run.sh logs`: every PoC-5 pod's last lines, through the one redaction filter. Check a saved log with `grep -c sk-`; it must print 0.
- A Secret's presence, names only: `deploy/kind/poc05/platform/seed.sh status`. Never `kubectl get secret -o yaml`.
- Memory per pod: there is no Metrics API on this cluster. Read the pod cgroup on the node (bring-up, item 10).

## Rotate a remote token
See the runbook "Rotating the remote token". In short: accept the new token everywhere, swap what is sent, drop the old one, restarting both sides at each step. Not yet run on kind: the PoC-5 manifests map no `previous-token` yet.

## Tear down
```
deploy/kind/poc05/run.sh delete
```
Not yet run on this branch. `down` is the same verb.

## Troubleshoot
`docs/guides/poc-05-runbooks.md`, by symptom:
- The cluster will not start.
- A sandbox pod is not Ready.
- A refusal test passes when it should fail.
- Kind tests time out under host CPU load.
- Rotating the remote token.
- Adding an allow-listed tool.
- Admission rejects a deploy.
