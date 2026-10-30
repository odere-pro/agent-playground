# PoC-6: how the bake-off works

Status: written 2026-10-09. The code and the manifests win where this guide and they differ. Section 6 ran in CI: `poc06-kind.yml` run 38010290978, 74 passed (run table in `pocs/poc-06b-bake-off-remote-lane/notes/2026-10-09-lanes-b-kind.md`). Section 8 lists what this guide could not confirm from code.
Contract: [contract v5](../contracts/contract-v5.md). Plan: [the PoC-6 work plan](../plans/2026-10-09-poc-06-bake-off.md). Decision: [ADR-006](../planning/adr/006-agent-engines-default-supported-lanes.md) (Proposed). Tracking: the READMEs of [6a](../../pocs/poc-06a-bake-off-sidecar-lane/README.md), [6b](../../pocs/poc-06b-bake-off-remote-lane/README.md), and [6c](../../pocs/poc-06c-pretrained-slm/README.md). The kit: [packages/bakeoff](../../packages/bakeoff/README.md).

Paths under `chassis/` are `packages/chassis/src/chassis/`. Manifests are under `deploy/kind/poc06/`.

## 1. The question and the answer

**The question.** Can the same chassis serve agents built on different frameworks, in the right lane, with no change to the event contract?

**The answer, in three sentences.** Each framework is one small workload (an "engine") that maps its own run onto the six chassis events. A trusted engine runs next to the chassis (the `sidecar` lane) or inside it (`inprocess`). An untrusted engine runs only in the `remote` lane, in its own gVisor pod. One benchmark kit drives all of them through the same `POST /v1/run`, and one test freezes the contract they all use.

## 2. The engines, the tasks, and the kit

The engine table, the trust rule, the three tasks, the processes the kit starts, and the SKIP reasons are in [packages/bakeoff/README.md](../../packages/bakeoff/README.md). The sources are `packages/bakeoff/src/bakeoff/registry.py` (engines and lanes) and `tasks.py` (tasks and checks). The default engine and the supported set are in [ADR-006](../planning/adr/006-agent-engines-default-supported-lanes.md).

## 3. The `/v1/messages` route on the model proxy

The Claude Agent SDK talks the Anthropic Messages format, not the OpenAI chat format. Contract v5, part A, adds `POST /v1/messages` to the model proxy router. Both proxy listeners mount that router, so both get the route: the loopback one (sidecar) and the remote one.

- The route maps the request onto `ModelPort`, as `/v1/chat/completions` does. The workload sets `ANTHROPIC_BASE_URL` to the chassis and sends its run token as before.
- A hand-written reader parses the body (`adapters/anthropic_compat/model_wire.py`), because the CLI sends things the SDK types refuse, such as `role: "system"` inside `messages`. The route is `server/model_proxy_messages.py`.
- On the remote listener the route sits behind `BearerAuth` and then `RequireRun`, like the other routes. The route list there is exactly `/v1/chat/completions`, `/v1/messages`, and `/mcp`.
- A 401 or 403 on the path `/v1/messages` has an Anthropic-shaped body. On every other path it has the v4 body.
- `/v1/messages/count_tokens` and `/v1/models` stay 404.
- This is not the Anthropic interface on the public port, which calls the agent and is unchanged.

## 4. The plain-A2A mode for kagent-adk

kagent-adk is kagent's Python agent runtime (Google ADK) served over A2A. It emits no chassis events. Contract v5, part B, adds an opt-in field, `spec.engine.protocol: a2a`, allowed only with `spec.engine.connector: remote`.

- In `a2a` mode the connector does not look for `metadata["chassis.event"]`. It reads the agent's own A2A stream and builds chassis events from it (`adapters/a2a/plain.py`). Chassis mode is untouched.
- Artifacts become `delta` events. Token usage is read from the metadata key in `spec.engine.a2a.usage_key` (for kagent, `kagent.dev/a2a/usage`). With no key the usage is zeros, which means unknown.
- The chassis cannot see tool calls or the model's turns inside the runtime. The kind test for the lookup task checks the answer text only.
- The connector pins the card's URL to `spec.engine.url`, so a third-party card cannot redirect the token. Transport errors use fixed text, so the remote's own words never reach a response.
- The runtime checks no inbound bearer. The control is the NetworkPolicy: port 9000 only from its chassis.
- The mode is not part of the freeze. It was read against one agent.

## 5. The contract v1 freeze

Contract v5, part C, freezes the first stable line. "Frozen as v1" means the first stable line, not the string `"1"`: `schema_version` stays `"0"` and no `events.v1.json` exists.

The test is `packages/chassis/tests/test_contract_freeze.py`. It pins:

- the sha256 of the five schema files;
- the `Handle` alias and the `wire` signature, through `inspect`;
- the six events and their fields, the schema versions, and the four A2A metadata keys;
- every frozen error code, with its `retryable` value, checked where it is emitted. A new code is allowed.

A failing assertion says that a change needs a new major, a new contract, or a revert. A new optional field, a new error code, or a new `spec.*` field breaks nothing. The engines needed no event change, so the freeze is the whole of exit criterion 8.

Route choice is a config change only. The 6c test `pocs/poc-06c-pretrained-slm/tests/test_poc06c_route_switch.py` runs every trusted engine on `big-default`, reloads the config to `local-small` with no restart, and reads the `model` each engine sent.

## 6. The kind setup

`deploy/kind/poc06/run.sh` (`make kind-poc06 ARGS="<verb>"`) adds the PoC-6 engines to the PoC-5 cluster. It reuses the cluster, namespaces, gVisor, agent-sandbox, platform, and admission rules, and calls `deploy/kind/poc05/run.sh` for the bring-up. It edits no PoC-5 file. Every `kubectl` call pins the context `kind-poc05`.

Verbs: `up`, `build`, `load`, `seed`, `apply`, `test`, `pods`, `logs`, `redact`, `delete`. Several can run in order.

`up` runs the PoC-5 `up`, builds and loads the images, then applies:

- `admission/params.yaml`: PoC-5's list plus `echo-openai-agents` as a trusted repository;
- `platform/`: the fake model server runs the bake-off script, not the example script;
- `seed.sh keys`: the PoC-6 keys and tokens;
- `remote/` and `agents/`, and `kagent/`, each waited for.

| Engine | Lane | Pod |
| ------ | ---- | --- |
| echo-openai-agents | sidecar | `agent-openai-agents` (runc), chassis as native sidecar |
| echo-typescript | sidecar | `agent-typescript` (runc) |
| echo-smolagents | remote | Sandbox `remote-smolagents` (gVisor), chassis `chassis-smolagents-remote` |
| echo-claude-agent | remote | Sandbox `remote-claude-agent` (gVisor), chassis `chassis-claude-agent-remote` |
| echo-typescript | remote | Sandbox `remote-typescript` (gVisor), chassis `chassis-typescript-remote` |
| kagent-adk | remote, plain A2A | Sandbox `remote-kagent-adk` (gVisor), chassis `chassis-kagent-adk-remote` |

Chassis pods run in `poc05-agents` and remotes in `poc05-remote`.

**kagent-adk is built from pinned source.** `build_kagent` clones `kagent-dev/kagent`, checks out the commit in `kagent/source.commit`, stops unless `git rev-parse HEAD` equals it, and builds `python/Dockerfile` as `kind.local/agent-platform/kagent-adk:kind`. That Dockerfile names its base images by tag. `run.sh` pins both by digest at build time: `KAGENT_UV_VERSION` through a build arg, and `KAGENT_DEBIAN` through a BuildKit `--build-context`. The [lanes-b-kind note](../../pocs/poc-06b-bake-off-remote-lane/notes/2026-10-09-lanes-b-kind.md) records the gap as it was when the kind runs passed. The image runs as uid 65532, the uid its upstream Dockerfile sets. `packages/chassis/tests/test_image_uids.py` has an `UPSTREAM_UIDS` table for images like this one.

**Threads under gVisor.** runsc with `oci-seccomp` turns RuntimeDefault's `clone3 -> ENOSYS` into EPERM (google/gvisor#14688; the fix is google/gvisor#14721 and is not released). So glibc 2.34 and later cannot create threads in a gVisor pod. PoC-6 gives its gVisor pods a Localhost seccomp profile, `profiles/poc06-runsc-clone3.json`. It is RuntimeDefault with only `clone3` allowed. `deploy/kind/poc06/run.sh` derives it from the node's containerd. Details: the lanes-b-kind note.

The fake model server on this cluster runs the PoC-6 script, so PoC-5's own kind suite needs a PoC-5-only cluster.

## 7. The one Mac command

`make poc06-mac` runs `scripts/poc06_mac.sh`. It produces the numbers that need a real model. It is the only place a provider key is used. Its five steps, flags, keys, and cleanup are in [deploy/compose/README.md](../../deploy/compose/README.md), "PoC-6: the one Mac command". `scripts/poc06_mac.sh --dry-run` prints every step and runs nothing. The LiteLLM master key stands in for a scoped key: an accepted debt, recorded in the [Mac command debt note](../../pocs/poc-06c-pretrained-slm/notes/2026-10-09-mac-command-debt.md).

## 8. What this guide could not verify from code

- The Mac steps have not run on a Mac. The guide describes the script, not its output.
- `profiles/poc06-runsc-clone3.json` is not a file in the repo. `deploy/kind/poc06/run.sh seccomp` derives it on each node at `up`, through `deploy/kind/poc06/seccomp/derive_profile.py` (lanes-b-kind note, "gVisor and clone3").
- ADR-006 is Proposed. The default engine in it is not decided yet.
