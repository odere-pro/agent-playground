# Backlog changes when PoC-6c closes

Status: **Drafted 2026-10-09; updated 2026-10-28 with the CI run on CPU.** The hosted big-model numbers and the Mac latency are not in. Applied through skill `planning-sync` as "Status after PoC-6" bullets at the end of `## What`. No acceptance box was ticked, and no order, size, or dependency changed.

Evidence: [Mac command debt](2026-10-09-mac-command-debt.md), [SLM run](2026-10-28-slm-run.md), [scale run](2026-10-28-scale-run.md) (both GitHub Actions run 40724222330, `poc06-slm.yml`, commit `bad1a45`), `tests/test_poc06c_mac_static.py`, `tests/test_poc06c_criteria.py`, `deploy/compose/README.md` ("PoC-6: the one Mac command").

## Applied

- **003 G-1b**: `big-default` hosted and `local-small` to llama.cpp are configured; the name stays.
- **036 S-7**: PoC-6c uses llama.cpp, not vLLM; vLLM on a GPU stays here.
- **063 S-8**: the base-model baseline, with no number yet.
- **026 CH-4**: the Mac command uses the LiteLLM master key as the chassis key. Accepted debt; the fix is a scoped key per run.

## What the CI run teaches: to apply

Not applied yet. Each one is a "Status after PoC-6c" bullet for the issue named.

- **063 S-8**: the base model already does both bake-off tasks. Qwen3-1.7B, not fine-tuned, passed smoke, simplifier, and lookup 5 of 5 on every trusted engine and lane (135 of 135 runs). The fallback share is 0%, under the epic's 10% target. This is a baseline, not a fallback rate: a small sample, two easy tasks, CPU. Fine-tuning has to be judged on harder tasks than these, or it has nothing to beat.
- **036 S-7**: the scale limit is the model server, not the agent pods. With 4 slots on CPU, throughput stops growing at 2 pairs (about 2.3 to 2.4 ok requests per second) and dips at 4. Each chassis uses 0.19 to 0.25 vCPU and each workload 0.09 to 0.17 vCPU, far under their caps. Serving capacity (slots, GPU, replicas) is what to size, and the scale test belongs with it.
- **036 S-7**: the queue needs a bound. At 1 pair, 16 users on 4 slots hit the run budget timeout (13 of 124 requests failed with p95 30000 ms). A request that waits for a slot should be refused or queued with a limit, not left to time out.
- **036 S-7 and 063 S-8**: every number is CPU only, on a 4-CPU GitHub runner that shares its CPUs between llama-server and the stack. Latency on a GPU or a Mac with Metal will differ. Do not size from these numbers.
- **The load sampler**: the llama-server CPU line used `ps -o %cpu`, which on Linux is a lifetime average and understates load. The script now samples per interval from `/proc`. A later scale run reads the new line.
- **013 CH-2**: the proxy's key filter is what kept every engine working on the SLM. It sends only six keys, so `stream_options` and PydanticAI's `tool_choice` (`auto`) never reach llama-server, and no request was rejected. Keep the filter; a key added to it needs a check on the `local-small` route.

## Not applied: waits on the Mac run

- The hosted big-model numbers next to the SLM numbers: success, tool-call success, token overhead, latency, time to first token.
- The Mac latency with Metal.
- ADR-006 acceptance: its SLM section is written and does not reopen the default; the hosted-model trigger is still open, and the user accepts it.
