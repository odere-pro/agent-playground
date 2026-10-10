# PoC-6: how the bake-off works

Status: written 2026-10-09. The code and the manifests win where this guide and they differ. Nothing in section 8 has run on a cluster yet; section 9 lists what this guide could not confirm from code.
Contract: [contract v5](../contracts/contract-v5.md). Plan: [the PoC-6 work plan](../plans/2026-10-09-poc-06-bake-off.md). Decision: [ADR-006](../planning/adr/006-agent-engines-default-supported-lanes.md) (Proposed). Tracking: the READMEs of [6a](../../pocs/poc-06a-bake-off-sidecar-lane/README.md), [6b](../../pocs/poc-06b-bake-off-remote-lane/README.md), and [6c](../../pocs/poc-06c-pretrained-slm/README.md). The kit: [packages/bakeoff](../../packages/bakeoff/README.md).

Paths under `chassis/` are `packages/chassis/src/chassis/`. Manifests are under `deploy/kind/poc06/`.

## 1. The question and the answer

**The question.** Can the same chassis serve agents built on different frameworks, in the right lane, with no change to the event contract?

**The answer, in three sentences.** Each framework is one small workload (an "engine") that maps its own run onto the six chassis events. A trusted engine runs next to the chassis (the `sidecar` lane) or inside it (`inprocess`). An untrusted engine runs only in the `remote` lane, in its own gVisor pod. One benchmark kit drives all of them through the same `POST /v1/run`, and one test freezes the contract they all use.

## 2. The engines and their lanes

| Engine | Trust | Lanes | Where it runs |
| ------ | ----- | ----- | ------------- |
| `echo-python` | trusted | inprocess, sidecar, remote | Python, `workload-a2a serve` |
| `echo-pydanticai` | trusted | inprocess, sidecar, remote | Python, `workload-a2a serve` |
| `echo-langgraph` | trusted | inprocess, sidecar, remote | Python, `workload-a2a serve` |
| `echo-openai-agents` | trusted | inprocess, sidecar, remote | Python, `workload-a2a serve` |
| `echo-typescript` | trusted | sidecar, remote | `node packages/workloads/echo-typescript/dist/src/main.js` |
| `echo-smolagents` | untrusted | remote only | Python, `workload-a2a serve` |
| `echo-claude-agent` | untrusted | remote only | Python, needs the chassis `POST /v1/messages` route |
| `kagent-adk` | untrusted | remote only | kind only, plain A2A |

The table is `packages/bakeoff/src/bakeoff/registry.py`. `lanes_for` and `check_lane` apply the trust rule before any process starts: an untrusted engine runs only in `remote` (PoC-5, ADR-001 item 8). The chassis config enforces the same rule at load (`spec.trust`). The Mac script keeps a copy of the trusted and untrusted lists, and a test compares them with the registry.

Why smolagents and the Claude Agent SDK are untrusted: they run model-written code or shell commands. With a real model that must happen only in a sandbox. So `registry.check_hosted` refuses an untrusted engine when the kit is given a model URL, and `scripts/poc06_mac.sh` refuses it in every step.

The default engine and the supported set are decided in [ADR-006](../planning/adr/006-agent-engines-default-supported-lanes.md). It is Proposed, not accepted.

## 3. The three tasks

Defined in `packages/bakeoff/src/bakeoff/tasks.py`. Each task is one input text and one pass check.

| Task | Input text | Passes when |
| ---- | ---------- | ----------- |
| `smoke` | `simplify: Hello.` | `end{status: ok}` after at least one `delta` |
| `simplifier` | `simplify: The SLM, released in 2026 by Acme, cut costs by 30 percent.` | the output holds `2026`, `Acme`, and `30` |
| `lookup` | `lookup: Define SLM and expand RAG.` | `glossary_lookup{term: SLM}`, then `acronym_expand{acronym: RAG}`, each valid against the tool's schema, and an answer that holds both expansions |

The tool schemas are the chassis's own fake tools (`chassis.fakes.tool`), so the check and the tools cannot drift apart.

## 4. How the bake-off kit drives them

The entry point is `python -m bakeoff smoke | run` (`make bakeoff ARGS="..."`). It declares no dependency of its own and the chassis never imports it.

For each engine and lane the kit starts real processes on localhost, on free ports:

```
fake model server   a uvicorn thread of the kit, with the engine's script
  <- chassis serve  a generated config: profile local, model litellm (dummy key), fake tools on /mcp
  <- workload       workload-a2a serve --handle ..., or node dist/src/main.js; none in `inprocess`
```

- It sends `POST /v1/run` to the chassis, once complete and once streaming. Both must pass the task's check.
- `smoke` runs the smoke task once per engine and lane and prints `PASS`, `FAIL`, or `SKIP`. It exits 1 on any `FAIL`.
- `run` repeats the tasks and writes `results.json` and `results.md`: pass rate, latency p50 and p95, time to first token, tokens, prompt bytes per model call, request body keys, and mapping size in code lines. It exits 1 only when a stack does not start. A low pass rate is a result.
- `--target NAME=URL` measures chassis instances that already run. `--model-url` and `--model-key-env` replace the fake model with a LiteLLM; only trusted engines in `inprocess` and `sidecar` run this way.
- Every child process gets an environment built from scratch (`envs.build_env`): `PATH`, a temp `HOME`, and the variables it needs. A name that starts with `ANTHROPIC_` or `CLAUDE_` is refused. Each child has its own process group, and a cleanup kills the groups at exit.

SKIP reasons, from the registry: `echo-typescript` has no `dist/` (run `make ts-check`); `echo-claude-agent` when `chassis.server.model_proxy_messages` cannot be imported; `kagent-adk` always (it runs only on kind); `remote` when the host has no non-loopback IP.

## 5. The `/v1/messages` route on the model proxy

The Claude Agent SDK talks the Anthropic Messages format, not the OpenAI chat format. Contract v5, part A, adds `POST /v1/messages` to the model proxy router. Both proxy listeners mount that router, so both get the route: the loopback one (sidecar) and the remote one.

- The route maps the request onto `ModelPort`, as `/v1/chat/completions` does. The workload sets `ANTHROPIC_BASE_URL` to the chassis and sends its run token as before.
- A hand-written reader parses the body (`adapters/anthropic_compat/model_wire.py`), because the CLI sends things the SDK types refuse, such as `role: "system"` inside `messages`. The route is `server/model_proxy_messages.py`.
- On the remote listener the route sits behind `BearerAuth` and then `RequireRun`, like the other routes. The route list there is exactly `/v1/chat/completions`, `/v1/messages`, and `/mcp`.
- A 401 or 403 on the path `/v1/messages` has an Anthropic-shaped body. On every other path it has the v4 body.
- `/v1/messages/count_tokens` and `/v1/models` stay 404.
- This is not the Anthropic interface on the public port, which calls the agent and is unchanged.

## 6. The plain-A2A mode for kagent-adk

kagent-adk is kagent's Python agent runtime (Google ADK) served over A2A. It emits no chassis events. Contract v5, part B, adds an opt-in field, `spec.engine.protocol: a2a`, allowed only with `spec.engine.connector: remote`.

- In `a2a` mode the connector does not look for `metadata["chassis.event"]`. It reads the agent's own A2A stream and builds chassis events from it (`adapters/a2a/plain.py`). Chassis mode is untouched.
- Artifacts become `delta` events. Token usage is read from the metadata key in `spec.engine.a2a.usage_key` (for kagent, `kagent.dev/a2a/usage`). With no key the usage is zeros, which means unknown.
- The chassis cannot see tool calls or the model's turns inside the runtime. The kind test for the lookup task checks the answer text only.
- The connector pins the card's URL to `spec.engine.url`, so a third-party card cannot redirect the token. Transport errors use fixed text, so the remote's own words never reach a response.
- The runtime checks no inbound bearer. The control is the NetworkPolicy: port 9000 only from its chassis.
- The mode is not part of the freeze. It was read against one agent.

## 7. The contract v1 freeze

Contract v5, part C, freezes the first stable line. "Frozen as v1" means the first stable line, not the string `"1"`: `schema_version` stays `"0"` and no `events.v1.json` exists.

The test is `packages/chassis/tests/test_contract_freeze.py` (it lands on the integration branch, not on the branch that wrote this guide). It pins:

- the sha256 of the five schema files;
- the `Handle` alias and the `wire` signature, through `inspect`;
- the six events and their fields, the schema versions, and the four A2A metadata keys;
- every frozen error code, with its `retryable` value, checked where it is emitted. A new code is allowed.

A failing assertion says that a change needs a new major, a new contract, or a revert. A new optional field, a new error code, or a new `spec.*` field breaks nothing. The engines needed no event change, so the freeze is the whole of exit criterion 8.

Route choice is a config change only. The 6c test `pocs/poc-06c-pretrained-slm/tests/test_poc06c_route_switch.py` runs every trusted engine on `big-default`, reloads the config to `local-small` with no restart, and reads the `model` each engine sent.

## 8. The kind setup

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

**kagent-adk is built from pinned source.** `build_kagent` clones `kagent-dev/kagent`, checks out the commit in `kagent/source.commit`, stops unless `git rev-parse HEAD` equals it, and builds `python/Dockerfile` as `kind.local/agent-platform/kagent-adk:poc06`. That Dockerfile does not pin its base images by digest. The gap is recorded in the [lanes-b-kind note](../../pocs/poc-06b-bake-off-remote-lane/notes/2026-10-09-lanes-b-kind.md). The image runs as uid 65532, the uid its upstream Dockerfile sets. `packages/chassis/tests/test_image_uids.py` has an `UPSTREAM_UIDS` table for images like this one.

**Threads under gVisor.** runsc with `oci-seccomp` turns RuntimeDefault's `clone3 -> ENOSYS` into EPERM (google/gvisor#14688; the fix is google/gvisor#14721 and is not released). So glibc 2.34 and later cannot create threads in a gVisor pod. PoC-6 gives its gVisor pods a Localhost seccomp profile, `profiles/poc06-runsc-clone3.json`. It is RuntimeDefault with only `clone3` allowed. `deploy/kind/poc06/run.sh` derives it from the node's containerd. Details: the lanes-b-kind note. The 6b README box for the kind run stays open until CI is green.

The fake model server on this cluster runs the PoC-6 script, so PoC-5's own kind suite needs a PoC-5-only cluster.

## 9. The one Mac command

`make poc06-mac ARGS="[--dry-run] [--push] [--only STEP] [--engine A,B]"` runs `scripts/poc06_mac.sh`. It produces the numbers that need a real model, and it is the only place a provider key is used.

| Step | What it runs | Model | Where |
| ---- | ------------ | ----- | ----- |
| `hosted` | `bakeoff run` with `--route big-default`, tasks smoke, simplifier, lookup, five repeats, `inprocess` and `sidecar` lanes | the hosted model | LiteLLM in `deploy/compose/poc06/litellm-hosted.yaml` (project `poc06mac`); chassis and workloads as host processes |
| `slm` | the same bake-off with `--route local-small` | Qwen3-1.7B on `llama-server` on the host, thinking off | the same LiteLLM |
| `scale` | the PoC-4 matrix with 1, 2, and 4 pairs of `echo-python` and `echo-openai-agents`, all on the one `llama-server` | `local-small` | the PoC-4 scale stack (project `poc04`) plus the SLM overlay |
| `load` | the PoC-4 matrix for `echo-openai-agents` and `echo-typescript` | the fake model server | the PoC-4 scale stack |

- `--dry-run` prints every step, checks that every file exists, and runs nothing. It needs no Docker, no key, and no network.
- `--push` commits only the notes the run wrote and pushes the current branch. It is refused on `main` and `master`.
- The provider key goes from `deploy/compose/.env` into one shell variable that is not exported. Only the one `docker compose` command that starts the hosted LiteLLM sees it. The chassis gets a LiteLLM key generated for the run. The key is the LiteLLM master key, an accepted debt recorded in the [Mac command debt note](../../pocs/poc-06c-pretrained-slm/notes/2026-10-09-mac-command-debt.md).
- Only trusted engines run here. The script refuses the others in every step.
- Teardown on any exit: `llama-server`, the Compose projects `poc06mac` and `poc04`, and the temp files.

## 10. What this guide could not verify from code

- The kind half has not run. The lanes-b-kind note says no cluster ran it, so every statement in section 8 comes from the script and the manifests, not from a kind run.
- The Mac steps have not run on a Mac. The guide describes the script, not its output.
- `test_contract_freeze.py` and the route-switch test were read on the integration branch. Their exact assertions can change before merge.
- `profiles/poc06-runsc-clone3.json` is not a file in the repo. `deploy/kind/poc06/run.sh seccomp` derives it on each node at `up`, through `deploy/kind/poc06/seccomp/derive_profile.py` (lanes-b-kind note, "gVisor and clone3").
- ADR-006 is Proposed. The default engine in it is not decided yet.
