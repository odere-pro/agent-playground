#!/usr/bin/env bash
# PoC-6 on a Mac: the runs that need a real model. One command, five steps, dated notes.
#
#   make poc06-mac ARGS="[--dry-run] [--push] [--only hosted|slm|scale|load|kind] [--engine A,B]"
#
#   hosted  6a and 6b criterion 5: the hosted big model (LiteLLM route `big-default`) for the
#           trusted engines, in the inprocess and sidecar lanes: smoke, simplifier, lookup x5.
#   slm     6c: Qwen3-1.7B on llama.cpp (llama-server on the host, thinking off) behind LiteLLM
#           route `local-small`; the same bake-off, only `--route` changes.
#   scale   6c criterion 4: the PoC-4 scale stack with 1, 2, 4 pairs of echo-python and
#           echo-openai-agents on `local-small`, all sharing the one llama-server.
#   load    6a criterion 4: the PoC-4 load matrix for echo-openai-agents and echo-typescript on
#           the fake model server.
#   kind    6b criterion 5 for the UNTRUSTED engines (echo-smolagents, echo-claude-agent,
#           kagent-adk): the same tasks on the hosted model, in the kind remote lane under gVisor.
#           Runs last, after the Compose stacks are down, so Docker memory is free. It brings up
#           PoC-5's cluster `kind-poc05` with the PoC-6 engines (deploy/kind/poc06/run.sh up), adds
#           the hosted route (deploy/kind/poc06/hosted.sh up; the provider key goes to its stdin),
#           runs the tasks (hosted.sh run), and on any exit runs hosted.sh down and run.sh delete.
#
# Options
#   --dry-run     Print every step and command, check that every file the run needs exists, and
#                 run nothing: no Docker, no key, no network. Works anywhere.
#   --push        After the run, commit ONLY the notes it wrote (exact paths) on the current
#                 branch and push that branch. Refused on main and master.
#   --only STEP   Run one step; repeatable, or a comma list. Default: all five, in the order above.
#   --engine A,B  The engines of the hosted and slm steps. Trusted engines only (see below). The
#                 kind step always runs the three untrusted ones.
#
# Environment (all optional; suggested defaults)
#   POC06_HOSTED_KEY_ENV   Name of the provider-key variable in deploy/compose/.env. Default
#                          OPENAI_API_KEY, the PoC-1 `local` variant's. Only the name is used here;
#                          the value goes to the LiteLLM container and nowhere else.
#   POC06_HOSTED_MODEL     LiteLLM model string of `big-default`. Default openai/gpt-4o-mini, the
#                          PoC-1 example (BIG_DEFAULT_MODEL in docker-compose.local.yaml).
#   POC06_GGUF_REPO / POC06_GGUF_FILE   Default Qwen/Qwen3-1.7B-GGUF / Qwen3-1.7B-Q8_0.gguf,
#                          downloaded on first run to ~/.cache/poc06/ (POC06_CACHE).
#   POC06_REPEAT (5)  POC06_SCALE_PAIRS ("1 2 4")  POC06_SCALE_ENGINES ("echo-python echo-openai-agents")
#   POC06_LOAD_ENGINES ("echo-openai-agents echo-typescript")  POC06_SCALE_USERS (16)
#   POC06_LLAMA_PORT (8089)  POC06_LITELLM_PORT (14000)  POC06_LLAMA_CTX (16384)
#   POC06_LLAMA_HOST (127.0.0.1)  The llama-server bind address: 127.0.0.1 or 0.0.0.0, nothing else.
#                          Docker Desktop reaches a loopback-bound server through
#                          host.docker.internal; Linux Docker does not (host-gateway is the bridge
#                          address), so a Linux host, such as the poc06-slm.yml CI job, sets 0.0.0.0.
#   POC06_LLAMA_PARALLEL (4: slots; the scale run's shared-server limit)
#   POC06_KIND_MIN_MIB (6000)  Docker memory floor for the kind step. suggested: the PoC-5 stack
#                          measured about 3.3 GiB working set on the node in a 7.75 GiB VM
#                          (pocs/poc-05-sandboxed/notes/2026-10-02-bring-up.md, section 10); the
#                          PoC-6 chassis, remotes, and kagent-adk add about 1.5 GiB. Below 7500
#                          MiB the script warns.
#
# Safety rules, enforced below
#   - Untrusted engines (echo-smolagents, echo-claude-agent, kagent-adk) execute model-written code
#     and shell commands. Every run here uses a real model, so none runs here, on the host or on
#     Compose. They run only in the `kind` step, in the remote lane under gVisor. The script refuses
#     any engine that is not on the trusted list in the hosted and slm steps (and in the scale and
#     load lists). `bakeoff run --model-url` refuses them as well.
#   - The provider key never leaves the LiteLLM container or, in the kind step, the LiteLLM pod. The
#     script reads it from deploy/compose/.env into a shell variable that is not exported, hands it
#     to the one `docker compose` command that starts LiteLLM and, in the kind step, to the stdin of
#     hosted.sh (a Secret, read only by the LiteLLM pod), and never prints, logs, or writes it. The
#     chassis gets a LiteLLM key generated for the run. Output is scrubbed before it reaches a note.
#   - It tears down what it started on any exit: llama-server, the Compose projects poc06mac and
#     poc04, the kind cluster poc05 (it refuses to start if one already exists), and its temp files.
#     It touches no other project's container, and prunes nothing.
#
# Works with the bash 3.2 macOS ships. Needs on the Mac: Docker Desktop (about 7.75 GiB), uv, node,
# npm, make, and for slm and scale `brew install llama.cpp`. On Linux (the poc06-slm.yml CI job), a
# llama.cpp release binary on PATH and POC06_LLAMA_HOST=0.0.0.0. Time: suggested 60 to 90 minutes,
# mostly the scale and load steps; the first run also builds the images and downloads about 1.8 GB.
set -euo pipefail

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
ROOT=$(cd "$HERE/.." && pwd)
PIECES_DIR="$HERE/poc06_mac"

# Sourced in this order, and by this path, whatever the working directory.
# shellcheck source=scripts/poc06_mac/lib.sh
. "$PIECES_DIR/lib.sh"
# shellcheck source=scripts/poc06_mac/notes.sh
. "$PIECES_DIR/notes.sh"
# shellcheck source=scripts/poc06_mac/steps.sh
. "$PIECES_DIR/steps.sh"
# shellcheck source=scripts/poc06_mac/push.sh
. "$PIECES_DIR/push.sh"

parse_args "$@"
check_settings
install_traps

cd "$ROOT"
if [[ "$DRY" == 1 ]]; then
  say "poc06_mac: DRY RUN. Nothing below is run, no key is read, no network is used."
  WORK="<tmp>"
else
  WORK=$(mktemp -d "${TMPDIR:-/tmp}/poc06-XXXXXX")
  if [[ -n "$(git status --short)" ]]; then START_DIRTY=" (dirty)"; fi
fi
say "steps: $ONLY | engines (trusted only): $ENGINES_CSV"
say "scale: engines $SCALE_ENGINES, pairs $SCALE_PAIRS | load: engines $LOAD_ENGINES"
check_files

if [[ "$DRY" == 1 ]]; then
  preflight_dry
else
  preflight_real
  LITELLM_KEY="sk-poc06-$(openssl rand -hex 24)"
fi

build_typescript
if want slm || want scale; then ensure_gguf; fi

for step in $ONLY; do
  if ! "step_$step"; then FAILED="$FAILED $step"; fi
done

if [[ "$PUSH" == 1 ]]; then do_push; fi

say
if [[ "$PIN_CREATED" == 1 ]]; then
  say "NOTE: ${PIN_FILE#"$ROOT"/} is new and is not part of --push. Commit it by hand."
fi
if [[ -n "$FAILED" ]]; then
  say "poc06_mac: steps that failed:$FAILED (their notes say why)"
  exit 1
fi
if [[ "$DRY" == 1 ]]; then say "poc06_mac: dry run complete; the plan above is what a real run does."; else say "poc06_mac: done."; fi
