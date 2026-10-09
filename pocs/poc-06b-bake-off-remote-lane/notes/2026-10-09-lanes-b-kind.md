# PoC-6 engines on kind: design, exceptions, and what is not yet shown (2026-10-09)

Task T-LANES-B. Command: `deploy/kind/poc06/run.sh up` then `run.sh test` (CI: `.github/workflows/poc06-kind.yml`). Nothing here has run on a cluster yet: the container that wrote it has no kind, no kubectl, and no working Docker. The offline half is `pocs/poc-06b-bake-off-remote-lane/tests/test_poc06b_kind_static.py`. The kind half is `test_poc06b_kind_{engines,remote_controls,sidecar_controls}.py`.

## What runs where

| Engine | Lane | Pod | Chassis | Image |
| ------ | ---- | --- | ------- | ----- |
| echo-openai-agents | sidecar | `agent-openai-agents` (runc) | native sidecar in the same pod | `echo-openai-agents:poc06` |
| echo-typescript | sidecar | `agent-typescript` (runc) | native sidecar | `echo-typescript:poc05` (PoC-5's build) |
| echo-smolagents | remote | Sandbox `remote-smolagents` (gVisor) | `chassis-smolagents-remote` | `echo-smolagents:poc06` |
| echo-claude-agent | remote | Sandbox `remote-claude-agent` (gVisor) | `chassis-claude-agent-remote` | `echo-claude-agent:poc06` |
| echo-typescript | remote | Sandbox `remote-typescript` (gVisor) | `chassis-typescript-remote` | `echo-typescript:poc05` |
| kagent-adk | remote, plain A2A | Sandbox `remote-kagent-adk` (gVisor) | `chassis-kagent-adk-remote` | `ghcr.io/kagent-dev/kagent/kagent-adk@sha256:...` |

All in PoC-5's namespaces and cluster: chassis pods in `poc05-agents`, remotes in `poc05-remote`. PoC-5's LiteLLM and Valkey policies admit only `poc05-agents` pods with `agents.platform/role: chassis`, so a new namespace would need PoC-5 edits.

## Decisions

1. **Trusted repositories.** Rule 4 admits a trusted sidecar pod only if its workload repository is on `trustedRepositories`. `echo-openai-agents` was not. `deploy/kind/poc06/admission/params.yaml` is PoC-5's ConfigMap plus that one repository; `run.sh` applies it as the cluster admin after the PoC-5 bring-up. A test fails if it differs from PoC-5's in anything else. This extends a hand-kept allow-list; it removes no control.
2. **The fake model's script.** The in-cluster fake model server runs `example.yaml`. The bake-off tasks need other rules, and the example script's catch-all `after_tool` rule would swallow the lookup loop. `run.sh` mounts `platform/fake-model-script.yaml` through a patch on the live Deployment (no PoC-5 file changes). On a PoC-6 cluster the PoC-5 kind suite's scripted texts (`hello`, `glossary`, `fail`) get the default reply. PoC-5's own suite needs a PoC-5-only cluster. Open question for the orchestrator: if one cluster must serve both suites, the script needs an engine-aware split that the fake server cannot do today.
3. **Gateway tool names.** LiteLLM lists `fake_tools-glossary_lookup`. The OpenAI Agents SDK refuses a call to a name it was not offered, so the scripted tool call carries the gateway's name. The kind tests strip the prefix, and read a text-only result as its JSON object (`poc06b_kind.gateway_view`), then apply `poc06_harness`'s checks unchanged.
4. **Engine markers.** A smolagents `CodeAgent` needs Python in a `<code>` block, and kagent-adk is checked on text. The script cannot see the engine, so the kind test adds ` [code]` or ` [text]` to the end of the task text for those two engines. The harness checks do not read the input text.
5. **Claude scratch volume.** `/tmp` is a 128Mi memory-backed emptyDir; `CLAUDE_AGENT_HOME_BASE=/tmp`, and each run makes its HOME and work dir there. The memory limit is 1Gi (the CLI is 175 to 240 MB resident). The pod sets no other `ANTHROPIC_*` or `CLAUDE_*` variable; a static test checks it.
6. **Proxy addresses.** Each remote's chassis has a fixed-ClusterIP Service (`10.96.85.92` to `.95`; PoC-5 uses `.91`), because the remote lane has no DNS.
7. **kagent-adk.**
   - It is the probe note's "cheaper alternative": the Python ADK runtime alone, not the controller, Substrate, or PostgreSQL. It is not "kagent on Kubernetes".
   - Its image is third-party and must be pinned by digest. No digest could be resolved offline, so `deploy/kind/poc06/kagent/image.sha256` holds none. While it holds none, `run.sh up` skips the folder and says so, and the kagent kind tests are `xfail` with that reason. Put the 64-hex digest in the file to enable it.
   - **Deviation from the task text:** the model `base_url` is the chassis's fixed ClusterIP, not a DNS name. The remote lane has no DNS (PoC-5 H20, tested), and giving kagent a kube-dns edge would weaken that. The DNS-name rule in the probe note comes from Substrate's credential proxy, which is not used here.
   - The runtime checks no inbound bearer (probe note, item 5). The control is the NetworkPolicy (9000 only from its chassis). It holds no Secret: the inbound bearer is its model key (`api_key_passthrough`).
   - The container `command` and flags (`kagent-adk static --filepath /config`) follow the probed `cli.py`. The image's own entrypoint was not read. The first kind run decides.

## Recorded exceptions

- **No Python in the Node image.** `remote-typescript` and `agent-typescript` cannot run the PoC-5 `kubectl exec ... python` probe. `tests/node_probe.js` implements the same check kinds in Node (`tcp`, `http`, `path`, `env_names`, `read`, `write`, `resolve`, `dns`). It does not implement `procs`. The PoC-5 H26 `/proc` scan is a sidecar check that the new sidecar tests do not repeat (they check `shareProcessNamespace` on the spec). The Node probe was run on this container only for `path`, `tcp`, `write`, `read`, and `env_names`; `dns` and `http` were not run.
- **kagent-adk may have no Python.** `probe_python` tries `python`, `python3`, and two venv paths. With none found the test is marked `xfail` with `NO_PYTHON`, and the pod-spec and node checks still stand.
- **Claude lookup.** `xfail(strict=False)`: the CLI offers MCP tools as `mcp__chassis__<name>`, so the scripted call names a tool it did not offer. A fix is coming separately.
- **kagent-adk lookup** asserts the answer text only. In plain-A2A mode the chassis cannot see tool calls; the test asserts no `tool_call` event appears, so the limit is on record.

## What the chassis cannot see or control for kagent-adk (this slice)

- Tool calls and the model's turns inside the runtime (plain mode maps artifacts to deltas and reads token usage from `kagent.dev/a2a/usage`).
- Whether the runtime checks the inbound bearer (it does not).
- The runtime's own telemetry and its state (OTEL is set off in the manifest; in-memory task store).
- Kagent's controller, Substrate, and PostgreSQL are not part of this slice, so nothing here says what they would do to the admission rules or the memory budget.

## What CI will first show (expectation)

Not run. Likely first failures, in order: (1) a gVisor start problem on the runner (as for `remote-lane.yml`); (2) the Claude pod's memory or startup time under gVisor; (3) smolagents or OpenAI Agents SDK tool names against the gateway; (4) the fake-model script's reach for the Claude smoke and simplifier (the CLI's trailing system turns); (5) kagent-adk's entrypoint, if a digest is set.
