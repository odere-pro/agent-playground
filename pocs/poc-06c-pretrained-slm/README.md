# PoC-6c: Agent pods on a pre-trained SLM

Status: in progress: the Mac command and its offline checks are built; every SLM number waits on the Mac run (`make poc06-mac`)
Planning doc: [006c-PoC-6c-pretrained-slm.md](../../docs/planning/poc/006c-PoC-6c-pretrained-slm.md)
Time box: suggested: 1 week, after PoC-6a

## Question

Can the agent pods run their tasks on a pre-trained SLM, served once behind LiteLLM, with no change to the workload or the chassis? Which engines still do the work on it, and at what cost in quality, tokens, and latency next to the big model?

## Scope

All of these run on the user's Mac. The container that built them has no Docker, no GPU, and no model file. The offline checks are `tests/test_poc06c_mac_static.py` (47 pass).

- [ ] One pre-trained SLM, not fine-tuned: Qwen3-1.7B, served once by the llama.cpp OpenAI-compatible server, with native tool calls on. · built, not run: the download is https-only with validated names (`test_the_model_download_is_https_only_and_the_names_are_validated`). Waits on: the Mac run.
- [ ] The model server sits behind LiteLLM on the existing `local-small` route, outside the agent pods. · configured: `test_the_litellm_config_routes_big_default_hosted_and_local_small_to_the_host`, `test_litellm_reaches_the_llama_server_through_host_docker_internal`. Waits on: the Mac run.
- [ ] Every engine scored in PoC-6a runs both benchmark tasks on `local-small`; only `spec.model.route` changes. · `test_the_script_refuses_an_untrusted_engine_in_hosted_mode`, `test_the_scripts_trusted_list_is_the_registrys` check the engine list. Waits on: the Mac run.
- [ ] Task checks with no judge model. · the checks exist for 6a (`packages/bakeoff/src/bakeoff/tasks.py`); their use on the SLM waits on the Mac run.
- [ ] Per engine on the SLM, next to the big-model numbers: success, tool-call success, token overhead, latency p50 and p95, time to first token, and the fallback share. · waits on: the Mac run.
- [ ] Per engine: the request features the llama.cpp path through LiteLLM rejects or ignores. · waits on: the Mac run. Known offline: the chassis proxy already drops `stream_options` and `tool_choice` ([6a scorecard](../poc-06a-bake-off-sidecar-lane/notes/2026-10-09-scorecard.md), router table).
- [ ] Scale: N agent pairs share one model server (N = 1, 2, 4). · `test_the_scale_overlay_changes_only_the_pairs_litellm_and_the_seed`, `test_scale_slm_refuses_an_untrusted_or_unknown_engine_before_touching_docker` check the plan. Waits on: the Mac run.
- [ ] An SLM section in the bake-off ADR from PoC-6. · [ADR-006](../../docs/planning/adr/006-agent-engines-default-supported-lanes.md) names this as a Revisit trigger; the section is written after the Mac run.

## Exit criteria

Each one has a scenario test in `tests/` or a recorded reason it cannot have one yet.

- [ ] Every PoC-6a engine runs both tasks on the pre-trained SLM through the chassis model proxy and LiteLLM, and only `spec.model.route` changed. · waits on: the Mac run.
- [ ] Per engine, task success, tool-call success, the fallback share, token overhead, and latency are recorded on the SLM, next to the big-model numbers. · waits on: the Mac run.
- [ ] Per engine, the request features the SLM route rejects or ignores are listed. · waits on: the Mac run.
- [ ] The scale run shows where throughput stops growing, and what limits it. · waits on: the Mac run.
- [ ] The model, its file hash, the runtime version, and the server flags are pinned and recorded. · waits on: the Mac run (the script records them; none has run).
- [ ] The bake-off ADR has an SLM section. If the default engine fails on the SLM, the default is reopened. · waits on: the Mac run, then the user.

## How to run

```bash
make test-poc POC=06c      # offline checks of the Mac command
make poc06-mac             # on the Mac; see deploy/compose/README.md, "PoC-6: the one Mac command"
```

## Demo

Not recorded yet. It comes from the Mac run.

## Notes and decisions

Dated files in `notes/`: [Mac command debt](notes/2026-10-09-mac-command-debt.md) (the LiteLLM master key stands in for a scoped key; owner 026 CH-4). [notes/backlog-changes.md](notes/backlog-changes.md) lists what the backlog should change.
