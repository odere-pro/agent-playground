# PoC-6c: Agent pods on a pre-trained SLM

This part runs the trusted engines on a pre-trained SLM, with only the model route changed. It exists to show what a small model costs next to the big one. Run its tests offline with `scripts/check_offline.sh pocs/poc-06c-pretrained-slm/tests`.

Status: in progress ([planning doc](../../docs/planning/poc/006c-PoC-6c-pretrained-slm.md)).
Time box: suggested: 1 week, after PoC-6a.
Builds on: 6a.
Hands to: ADR-006's SLM section.

The engines and the bake-off are [PoC-6a](../poc-06a-bake-off-sidecar-lane/README.md) and [PoC-6b](../poc-06b-bake-off-remote-lane/README.md). The hosted side by side and the Mac latency wait on the Mac run (`make poc06-mac`). Accepting ADR-006 waits on the user.

## Question

Can the agent pods run their tasks on a pre-trained SLM, served once behind LiteLLM, with no change to the workload or the chassis? Which engines still do the work on it, and at what cost in quality, tokens, and latency next to the big model?

## Scope

The SLM run and the scale run ran in CI, on a 4-CPU GitHub runner with no GPU (GitHub Actions run 40724222330, `poc06-slm.yml`, commit `bad1a45`). The hosted big-model run and the Mac latency still run on the user's Mac. The offline checks are `tests/test_poc06c_mac_static.py` (the Mac command, the Compose override, the model download) and `tests/test_poc06c_route_switch.py` (every sidecar engine runs smoke, simplifier, and lookup on `big-default`, then on `local-small` against the fake model, and only `spec.model.route` changes). `tests/test_poc06c_criteria.py` reads the recorded run against the exit criteria. Run them with `make test-poc POC=06c`.

- [x] One pre-trained SLM, not fine-tuned: Qwen3-1.7B, served once by the llama.cpp OpenAI-compatible server, with native tool calls on. · GitHub Actions run 40724222330: `Qwen3-1.7B-Q8_0.gguf` on llama.cpp b11963 with `--jinja`. Every lookup made its tool calls ([SLM run](notes/2026-10-28-slm-run.md)). `test_the_model_file_is_pinned_by_sha256`, `test_the_slm_note_records_the_hash_runtime_and_flags`, `test_the_model_download_is_https_only_and_the_names_are_validated`.
- [x] The model server sits behind LiteLLM on the existing `local-small` route, outside the agent pods. · GitHub Actions run 40724222330 ran through LiteLLM at route `local-small` ([SLM run](notes/2026-10-28-slm-run.md), Command). `test_every_trusted_engine_ran_every_task_on_local_small`, `test_the_litellm_config_routes_big_default_hosted_and_local_small_to_the_host`, `test_litellm_reaches_the_llama_server_through_host_docker_internal`.
- [x] Every engine scored in PoC-6a runs both benchmark tasks on `local-small`. Only `spec.model.route` changes. · GitHub Actions run 40724222330: the five trusted engines, every lane they have, smoke, simplifier, and lookup 5 of 5 each (135 of 135 runs). `test_every_trusted_engine_ran_every_task_on_local_small`, `test_only_the_route_changes_and_every_task_passes_both_times`, `test_the_scripts_trusted_list_is_the_registrys`. The untrusted engines (smolagents, Claude Agent SDK) run only on kind and are not part of this run.
- [x] Task checks with no judge model. · GitHub Actions run 40724222330 scored every run with the 6a checks in `packages/bakeoff/src/bakeoff/tasks.py`. No judge model ran. `test_every_cell_that_ran_records_success_tool_calls_tokens_and_latency`.
- [ ] Per engine on the SLM, next to the big-model numbers: success, tool-call success, token overhead, latency p50 and p95, time to first token, and the fallback share. · the SLM half is recorded ([SLM run](notes/2026-10-28-slm-run.md), `test_every_cell_that_ran_records_success_tool_calls_tokens_and_latency`): every cell 5 of 5, fallback share 0%, CPU latency. The big-model numbers wait on the Mac's `hosted` step (`test_the_slm_numbers_sit_next_to_the_big_model_numbers` skips until then).
- [x] Per engine: the request features the llama.cpp path through LiteLLM rejects or ignores. · The [SLM run](notes/2026-10-28-slm-run.md) note has a "Request features" section from GitHub Actions run 40724222330: the chassis proxy keeps only `model`, `messages`, `temperature`, `max_tokens`, `tools`, and `stream` (`packages/chassis/src/chassis/server/model_proxy.py:214-225`), so `stream_options` and PydanticAI's `tool_choice` (`auto`) are dropped. No request was rejected. Evidence: `tests/test_poc06c_criteria.py::test_the_slm_note_lists_the_request_features_per_engine`.
- [x] Scale: N agent pairs share one model server (N = 1, 2, 4). · GitHub Actions run 40724222330: `echo-python` and `echo-openai-agents`, 16 users, 60 s per point ([scale run](notes/2026-10-28-scale-run.md)). `test_the_scale_run_has_every_pair_count_for_every_scale_engine`, `test_the_scale_note_reads_the_limit`.
- [x] An SLM section in the bake-off ADR from PoC-6. · [ADR-006](../../docs/planning/adr/006-agent-engines-default-supported-lanes.md), section "SLM (PoC-6c)". `test_the_bakeoff_adr_has_an_slm_section`. The ADR stays Proposed.

## Exit criteria

Each one has a scenario test in `tests/` or a recorded reason it cannot have one yet.

- [x] Every PoC-6a engine runs both tasks on the pre-trained SLM through the chassis model proxy and LiteLLM, and only `spec.model.route` changed. · `test_every_trusted_engine_ran_every_task_on_local_small` on the results of GitHub Actions run 40724222330 (135 of 135 runs passed). `test_only_the_route_changes_and_every_task_passes_both_times` checks the same switch offline.
- [ ] Per engine, task success, tool-call success, the fallback share, token overhead, and latency are recorded on the SLM, next to the big-model numbers. · the SLM half is recorded (`test_every_cell_that_ran_records_success_tool_calls_tokens_and_latency`, GitHub Actions run 40724222330). The big-model side by side waits on the Mac's `hosted` step; `test_the_slm_numbers_sit_next_to_the_big_model_numbers` skips until then.
- [x] Per engine, the request features the SLM route rejects or ignores are listed. · The [SLM run](notes/2026-10-28-slm-run.md) note has a "Request features" section from GitHub Actions run 40724222330: the chassis proxy keeps only `model`, `messages`, `temperature`, `max_tokens`, `tools`, and `stream` (`packages/chassis/src/chassis/server/model_proxy.py:214-225`), so `stream_options` and PydanticAI's `tool_choice` (`auto`) are dropped. No request was rejected. Evidence: `tests/test_poc06c_criteria.py::test_the_slm_note_lists_the_request_features_per_engine`.
- [x] The scale run shows where throughput stops growing, and what limits it. · `test_the_scale_run_has_every_pair_count_for_every_scale_engine`, `test_the_scale_note_reads_the_limit` ([scale run](notes/2026-10-28-scale-run.md), GitHub Actions run 40724222330). Throughput stops growing at 2 pairs (about 2.3 to 2.4 ok requests per second) and dips at 4. The limit is the model server: 4 slots, CPU decoding, 4 CPUs shared with the stack. The chassis and workload sit far under their 1-vCPU caps.
- [x] The model, its file hash, the runtime version, and the server flags are pinned and recorded. · `test_the_model_file_is_pinned_by_sha256`, `test_the_slm_note_records_the_hash_runtime_and_flags`, `test_the_ci_job_pins_llama_cpp_and_the_model_revision`. The file hash is in `deploy/compose/poc06/model.sha256`; the Hugging Face revision and the llama.cpp release and its sha256 are in `.github/workflows/poc06-slm.yml`.
- [x] The bake-off ADR has an SLM section. If the default engine fails on the SLM, the default is reopened. · `test_the_bakeoff_adr_has_an_slm_section`; [ADR-006](../../docs/planning/adr/006-agent-engines-default-supported-lanes.md), "SLM (PoC-6c)". PydanticAI passes the lookup 5 of 5 in both lanes, as every engine does, so the default is not reopened. The hosted-model Revisit trigger is still open, and accepting the ADR waits on the user.

## How to run

```bash
make test-poc POC=06c      # offline checks of the Mac command
make poc06-mac             # on the Mac; see deploy/compose/README.md, "PoC-6: the one Mac command"
```

## Demo

The CI run's notes are the record: the [SLM run](notes/2026-10-28-slm-run.md) and the [scale run](notes/2026-10-28-scale-run.md), from GitHub Actions run 40724222330. The side-by-side demo (SLM next to the hosted model) needs the Mac run.

## Notes and decisions

Dated files in `notes/`: [Mac command debt](notes/2026-10-09-mac-command-debt.md) (the LiteLLM master key stands in for a scoped key; owner 026 CH-4). [SLM run](notes/2026-10-28-slm-run.md) (every engine on Qwen3-1.7B, request features, pins). [Scale run](notes/2026-10-28-scale-run.md) (1, 2, and 4 pairs on one llama-server). [notes/backlog-changes.md](notes/backlog-changes.md) lists what the backlog should change.
