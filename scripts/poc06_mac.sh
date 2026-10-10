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
# npm, make, and for slm and scale `brew install llama.cpp`. Time: suggested 60 to 90 minutes,
# mostly the scale and load steps; the first run also builds the images and downloads about 1.8 GB.
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
COMPOSE_DIR="$ROOT/deploy/compose"
POC06_DIR="$COMPOSE_DIR/poc06"
ENV_FILE="$COMPOSE_DIR/.env"
PIN_FILE="$POC06_DIR/model.sha256"
LITELLM_COMPOSE="$POC06_DIR/litellm-hosted.yaml"
LITELLM_PROJECT=poc06mac
NOTES_A="pocs/poc-06a-bake-off-sidecar-lane/notes"
NOTES_C="pocs/poc-06c-pretrained-slm/notes"
NOTES_B="pocs/poc-06b-bake-off-remote-lane/notes"
KIND_DIR="$ROOT/deploy/kind/poc06"
KIND_RUN="$KIND_DIR/run.sh"
KIND_HOSTED="$KIND_DIR/hosted.sh"
KIND_CLUSTER=poc05

# The hard list. An engine not named here does not run. Keep in step with
# packages/bakeoff/src/bakeoff/registry.py (a test compares them).
TRUSTED_ENGINES="echo-python echo-pydanticai echo-langgraph echo-openai-agents echo-typescript"
UNTRUSTED_ENGINES="echo-smolagents echo-claude-agent kagent-adk"

HOSTED_KEY_ENV=${POC06_HOSTED_KEY_ENV:-OPENAI_API_KEY}
HOSTED_MODEL=${POC06_HOSTED_MODEL:-openai/gpt-4o-mini}
GGUF_REPO=${POC06_GGUF_REPO:-Qwen/Qwen3-1.7B-GGUF}
GGUF_FILE=${POC06_GGUF_FILE:-Qwen3-1.7B-Q8_0.gguf}
# suggested: `main` is trust on first use; the sha256 pin (model.sha256) is what holds after.
GGUF_REV=${POC06_GGUF_REV:-main}
CACHE=${POC06_CACHE:-$HOME/.cache/poc06}
REPEAT=${POC06_REPEAT:-5}
SCALE_PAIRS=${POC06_SCALE_PAIRS:-"1 2 4"}
SCALE_ENGINES=${POC06_SCALE_ENGINES:-"echo-python echo-openai-agents"}
LOAD_ENGINES=${POC06_LOAD_ENGINES:-"echo-openai-agents echo-typescript"}
SCALE_USERS=${POC06_SCALE_USERS:-16}
LLAMA_PORT=${POC06_LLAMA_PORT:-8089}
LITELLM_PORT=${POC06_LITELLM_PORT:-14000}
LLAMA_CTX=${POC06_LLAMA_CTX:-16384}
LLAMA_PARALLEL=${POC06_LLAMA_PARALLEL:-4}
KIND_MIN_MIB=${POC06_KIND_MIN_MIB:-6000}
# Qwen3 thinking off: the model's own chat-template switch, passed to llama.cpp's Jinja engine.
# Recorded in every slm note. The alternative is `--reasoning-budget 0` (llama.cpp's own switch).
LLAMA_ARGS=(--jinja --chat-template-kwargs '{"enable_thinking":false}' -c "$LLAMA_CTX" -np "$LLAMA_PARALLEL"
  --host 127.0.0.1 --port "$LLAMA_PORT" --alias local-small)
LLAMA_FLAGS="${LLAMA_ARGS[*]}"   # for display and the notes
GGUF_PATH="$CACHE/$GGUF_FILE"

DRY=0
PUSH=0
ONLY=""
ENGINES_CSV=$(echo "$TRUSTED_ENGINES" | tr ' ' ',')

WORK=""
LLAMA_PID=""
SAMPLER_PID=""
LITELLM_UP=0
SCALE_UP=0
KIND_UP=0
KIND_HOSTED_UP=0
HOSTED_KEY=""          # a shell variable only: never exported, never printed
LITELLM_KEY="sk-poc06-dry-run"
DOCKER_MEM_MIB=0
LLAMA_VERSION="not checked"
GGUF_SHA="not checked"
PIN_CREATED=0
START_DIRTY=""
EXTRA_SECTION=""
MISSING=0
NOTE_FILES=()
FAILED=""

say() { printf '%s\n' "$*"; }
warn() { printf 'poc06_mac: warning: %s\n' "$*" >&2; }
die() { printf 'poc06_mac: %s\n' "$1" >&2; exit "${2:-2}"; }

usage() {
  sed -n '2,/^set -euo/p' "$0" | sed '$d' | sed 's/^# \{0,1\}//' >&2
  exit 2
}

# ---- trusted-engine rule ---------------------------------------------------------------------

require_trusted() {
  local engine
  for engine in "$@"; do
    case " $TRUSTED_ENGINES " in
      *" $engine "*) ;;
      *)
        case " $UNTRUSTED_ENGINES " in
          *" $engine "*)
            die "refusing $engine: it is untrusted and would run model-written code against a real model on this host or on Compose. It runs only on kind under gVisor." ;;
          *) die "refusing $engine: not on the trusted list ($TRUSTED_ENGINES)" ;;
        esac ;;
    esac
  done
}

# ---- arguments -------------------------------------------------------------------------------

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) DRY=1; shift ;;
    --push) PUSH=1; shift ;;
    --only) [[ $# -ge 2 ]] || usage; ONLY="$ONLY $(echo "$2" | tr ',' ' ')"; shift 2 ;;
    --engine) [[ $# -ge 2 ]] || usage; ENGINES_CSV=$2; shift 2 ;;
    *) usage ;;
  esac
done
[[ -n "$ONLY" ]] || ONLY="hosted slm scale load kind"
for step in $ONLY; do
  case "$step" in hosted | slm | scale | load | kind) ;; *) die "--only: hosted, slm, scale, load, or kind (got $step)" ;; esac
done
want() { case " $ONLY " in *" $1 "*) return 0 ;; *) return 1 ;; esac; }

# The engine lists are space-separated on purpose, so they are left unquoted.
# shellcheck disable=SC2046,SC2086
{
  require_trusted $(echo "$ENGINES_CSV" | tr ',' ' ')
  require_trusted $SCALE_ENGINES
  require_trusted $LOAD_ENGINES
}
[[ "$HOSTED_KEY_ENV" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || die "POC06_HOSTED_KEY_ENV is not a variable name"
[[ "$GGUF_FILE" =~ ^[A-Za-z0-9._-]+$ ]] || die "POC06_GGUF_FILE must match [A-Za-z0-9._-]+"
[[ "$GGUF_REPO" =~ ^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$ ]] || die "POC06_GGUF_REPO must be owner/name"
[[ "$GGUF_REV" =~ ^[A-Za-z0-9._-]+$ ]] || die "POC06_GGUF_REV must match [A-Za-z0-9._-]+"
[[ "$REPEAT" =~ ^[0-9]+$ ]] || die "POC06_REPEAT must be a number"
[[ "$KIND_MIN_MIB" =~ ^[0-9]+$ ]] || die "POC06_KIND_MIN_MIB must be a number"

# ---- teardown --------------------------------------------------------------------------------

compose_litellm() {
  POC06_LITELLM_KEY="$LITELLM_KEY" POC06_LITELLM_PORT="$LITELLM_PORT" POC06_LLAMA_PORT="$LLAMA_PORT" \
    POC06_HOSTED_MODEL="$HOSTED_MODEL" docker compose -p "$LITELLM_PROJECT" -f "$LITELLM_COMPOSE" "$@"
}

stop_litellm() {
  if [[ "$LITELLM_UP" == 1 ]]; then
    compose_litellm down -v --remove-orphans >/dev/null 2>&1 || warn "could not stop project $LITELLM_PROJECT"
    LITELLM_UP=0
  fi
}

stop_scale() {
  if [[ "$SCALE_UP" == 1 ]]; then
    "$POC06_DIR/scale-slm.sh" down >/dev/null 2>&1 || warn "could not stop project poc04"
    SCALE_UP=0
  fi
}

stop_llama() {
  if [[ -n "$SAMPLER_PID" ]]; then
    kill "$SAMPLER_PID" 2>/dev/null || true
    wait "$SAMPLER_PID" 2>/dev/null || true
    SAMPLER_PID=""
  fi
  if [[ -n "$LLAMA_PID" ]]; then
    kill "$LLAMA_PID" 2>/dev/null || true
    wait "$LLAMA_PID" 2>/dev/null || true
    LLAMA_PID=""
  fi
}

# Undo the kind step: the hosted route first, then the cluster. Each runs once, whatever came before.
# Output goes to the step's log, so the note shows the teardown.
stop_kind() {
  local klog="${WORK:-/dev/null}/kind.log"
  [[ -d "${WORK:-}" ]] || klog=/dev/null
  if [[ "$KIND_HOSTED_UP" == 1 ]]; then
    KIND_HOSTED_UP=0
    "$KIND_HOSTED" down >>"$klog" 2>&1 || warn "hosted.sh down failed; the cluster delete below removes it anyway"
  fi
  if [[ "$KIND_UP" == 1 ]]; then
    KIND_UP=0
    "$KIND_RUN" delete >>"$klog" 2>&1 || warn "could not delete the kind cluster $KIND_CLUSTER, which may still hold the provider key Secret; run: deploy/kind/poc06/run.sh delete"
  fi
}

cleanup() {
  local rc=$?
  trap '' INT TERM HUP   # a second Ctrl-C must not abort the teardown
  trap - EXIT
  stop_kind
  stop_scale
  stop_litellm
  stop_llama
  if [[ "$DRY" != 1 && -n "$WORK" && -d "$WORK" ]]; then rm -rf "$WORK"; fi
  exit "$rc"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM HUP

# ---- helpers ---------------------------------------------------------------------------------

# Remove the secrets we hold, and anything that looks like a key, from text on stdin.
redact() {
  local text
  text=$(cat)
  if [[ -n "$HOSTED_KEY" ]]; then text=${text//"$HOSTED_KEY"/[redacted]}; fi
  if [[ -n "$LITELLM_KEY" ]]; then text=${text//"$LITELLM_KEY"/[redacted]}; fi
  if [[ -n "$HOME" ]]; then
    local tilde='~'
    text=${text//"$HOME"/$tilde}
  fi
  printf '%s\n' "$text" | sed -E -e 's/(sk-|Bearer )[A-Za-z0-9._-]{8,}/\1[redacted]/g' -e 's/sk-[A-Za-z0-9._-]*\*+[A-Za-z0-9]*/sk-[redacted]/g'
}

# Run a command, show it, and keep its output in a log. In a dry run, only show it.
run_logged() {
  local log=$1 rc=0
  shift
  if [[ "$DRY" == 1 ]]; then
    say "  \$ $*"
    return 0
  fi
  printf '$ %s\n' "$*" | tee -a "$log"
  "$@" 2>&1 | tee -a "$log" || rc=${PIPESTATUS[0]}
  printf 'exit code: %s\n' "$rc" >>"$log"
  return "$rc"
}

sha256_of() {
  if command -v shasum >/dev/null 2>&1; then shasum -a 256 "$1" | awk '{print $1}'; else sha256sum "$1" | awk '{print $1}'; fi
}

# NAME FILE: the value of NAME in a dotenv file, or nothing. The caller never prints it.
env_value() {
  local line
  line=$(grep -E "^[[:space:]]*(export[[:space:]]+)?$1=" "$2" | tail -n 1) || return 0
  line=${line#*=}
  case "$line" in
    \"*\") line=${line#\"}; line=${line%\"} ;;
    \'*\') line=${line#\'}; line=${line%\'} ;;
    *) line=${line%% #*} ;;
  esac
  printf '%s' "$line"
}

need_file() {
  if [[ -e "$1" ]]; then say "  ok       ${1#"$ROOT"/}"; else say "  MISSING  ${1#"$ROOT"/}"; MISSING=1; fi
}

# ---- preflight -------------------------------------------------------------------------------

check_files() {
  say "== files this run needs"
  need_file "$ROOT/Makefile"
  need_file "$LITELLM_COMPOSE"
  need_file "$POC06_DIR/litellm.config.yaml"
  need_file "$COMPOSE_DIR/litellm/token_log.py"
  need_file "$POC06_DIR/scale-litellm-slm.yaml"
  need_file "$POC06_DIR/chassis-configs/scale-slm.yaml"
  need_file "$POC06_DIR/scale-slm.sh"
  need_file "$POC06_DIR/load_driver.py"
  need_file "$COMPOSE_DIR/scale.sh"
  need_file "$COMPOSE_DIR/docker-compose.scale.yaml"
  need_file "$ROOT/pocs/poc-04-stateless-scalable/load/run_matrix.py"
  need_file "$ROOT/pocs/poc-04-stateless-scalable/load/locustfile.py"
  need_file "$ROOT/packages/bakeoff/src/bakeoff/cli.py"
  need_file "$ROOT/packages/chassis/configs/scale.yaml"
  need_file "$ROOT/packages/workloads/echo-openai-agents/Dockerfile"
  need_file "$ROOT/packages/workloads/echo-typescript/package.json"
  need_file "$ROOT/$NOTES_A"
  need_file "$ROOT/$NOTES_C"
  need_file "$ROOT/$NOTES_B"
  need_file "$KIND_RUN"
  need_file "$KIND_HOSTED"
  need_file "$KIND_DIR/hosted/litellm-config.yaml"
  need_file "$KIND_DIR/hosted/network-policy.yaml"
  need_file "$KIND_DIR/hosted/relay.py"
  [[ "$MISSING" == 0 ]] || die "a file this run needs is missing (above)" 1
}

preflight_real() {
  say "== preflight"
  command -v docker >/dev/null 2>&1 || die "docker is not installed" 1
  docker info >/dev/null 2>&1 || die "Docker is not running; start Docker Desktop" 1
  local mem tool
  mem=$(docker info --format '{{.MemTotal}}' 2>/dev/null || echo 0)
  DOCKER_MEM_MIB=$((mem / 1024 / 1024))
  say "  Docker VM memory: $DOCKER_MEM_MIB MiB"
  [[ "$DOCKER_MEM_MIB" -ge 4000 ]] || die "Docker has $DOCKER_MEM_MIB MiB; the stacks need about 7 GiB (Docker Desktop, Settings, Resources)" 1
  [[ "$DOCKER_MEM_MIB" -ge 7000 ]] || warn "under 7000 MiB: the 4-pair scale and load runs may not fit"
  for tool in uv node npm make openssl git curl; do
    command -v "$tool" >/dev/null 2>&1 || die "$tool is not installed" 1
  done
  say "  uv, node, npm, make, openssl, git, curl: present"
  if want kind; then
    for tool in kind kubectl jq; do
      command -v "$tool" >/dev/null 2>&1 || die "$tool is not installed (the kind step needs kind, kubectl, jq)" 1
    done
    say "  kind, kubectl, jq: present"
    [[ "$DOCKER_MEM_MIB" -ge "$KIND_MIN_MIB" ]] \
      || die "Docker has $DOCKER_MEM_MIB MiB; the kind step needs $KIND_MIN_MIB (POC06_KIND_MIN_MIB; Docker Desktop, Settings, Resources)" 1
    [[ "$DOCKER_MEM_MIB" -ge 7500 ]] || warn "under 7500 MiB: the kind step may be slow or lose a pod to memory"
    if kind get clusters 2>/dev/null | grep -qx "$KIND_CLUSTER"; then
      die "a kind cluster named $KIND_CLUSTER exists, and this step deletes it at the end. Delete it yourself first (deploy/kind/poc06/run.sh delete) or run without the kind step." 1
    fi
  fi
  if want hosted || want kind; then
    [[ -f "$ENV_FILE" ]] || die "deploy/compose/.env does not exist; copy .env.example and set $HOSTED_KEY_ENV" 1
    HOSTED_KEY=$(env_value "$HOSTED_KEY_ENV" "$ENV_FILE")
    if [[ -z "$HOSTED_KEY" ]]; then
      die "$HOSTED_KEY_ENV is not set in deploy/compose/.env (presence is checked, the value is never shown)" 1
    fi
    say "  $HOSTED_KEY_ENV: set in deploy/compose/.env (value not shown); model $HOSTED_MODEL"
  fi
  if want slm || want scale; then
    command -v llama-server >/dev/null 2>&1 || die "llama-server not found; run: brew install llama.cpp" 1
    local help version
    help=$(llama-server --help 2>&1 || true)
    case "$help" in
      *--chat-template-kwargs*) ;;
      *) die "this llama-server has no --chat-template-kwargs; run: brew upgrade llama.cpp" 1 ;;
    esac
    version=$(llama-server --version 2>&1 || true)
    LLAMA_VERSION=$(printf '%s\n' "$version" | grep -i -m 1 'version' || echo unknown)
    say "  llama-server: $LLAMA_VERSION"
  fi
}

ensure_gguf() {
  say "== model file"
  if [[ "$DRY" == 1 ]]; then
    say "  would use $GGUF_PATH; download it on first run:"
    say "  \$ curl -fL --proto =https --proto-redir =https --retry 3 -o $GGUF_PATH.part https://huggingface.co/$GGUF_REPO/resolve/$GGUF_REV/$GGUF_FILE"
    say "  would record its sha256 in ${PIN_FILE#"$ROOT"/} on the first run, and refuse a different hash after"
    return 0
  fi
  mkdir -p "$CACHE"
  if [[ ! -f "$GGUF_PATH" ]]; then
    say "  downloading $GGUF_REPO/$GGUF_FILE (about 1.8 GB) to $CACHE"
    curl -fL --proto =https --proto-redir =https --retry 3 -o "$GGUF_PATH.part" "https://huggingface.co/$GGUF_REPO/resolve/$GGUF_REV/$GGUF_FILE" \
      || die "download failed" 1
    mv "$GGUF_PATH.part" "$GGUF_PATH"
  fi
  GGUF_SHA=$(sha256_of "$GGUF_PATH")
  if [[ -f "$PIN_FILE" ]]; then
    local expected
    expected=$(awk 'NR==1 {print $1}' "$PIN_FILE")
    [[ "$GGUF_SHA" == "$expected" ]] \
      || die "$GGUF_FILE has sha256 $GGUF_SHA but ${PIN_FILE#"$ROOT"/} pins $expected. Refusing a different file." 1
    say "  sha256 matches the pin: $GGUF_SHA"
  else
    printf '%s  %s\n' "$GGUF_SHA" "$GGUF_FILE" >"$PIN_FILE"
    PIN_CREATED=1
    say "  first run: recorded sha256 $GGUF_SHA in ${PIN_FILE#"$ROOT"/} (commit it)"
  fi
}

# ---- services --------------------------------------------------------------------------------

start_litellm() {  # LOG [hosted]: only "hosted" gives the container the provider key
  local log=$1 rc=0 key=""
  [[ "${2:-}" == hosted ]] && key=$HOSTED_KEY
  say "== start LiteLLM (project $LITELLM_PROJECT, 127.0.0.1:$LITELLM_PORT)"
  if [[ "$DRY" == 1 ]]; then
    if [[ "${2:-}" == hosted ]]; then
      say "  \$ docker compose -p $LITELLM_PROJECT -f ${LITELLM_COMPOSE#"$ROOT"/} up -d --wait   # provider key: from the env file, not shown"
    else
      say "  \$ docker compose -p $LITELLM_PROJECT -f ${LITELLM_COMPOSE#"$ROOT"/} up -d --wait   # no provider key"
    fi
    return 0
  fi
  LITELLM_UP=1
  POC06_HOSTED_API_KEY="$key" compose_litellm up -d --wait >>"$log" 2>&1 || rc=$?
  if [[ "$rc" != 0 ]]; then
    redact <"$log" | tail -n 20
    return "$rc"
  fi
  say "  LiteLLM is healthy"
}

start_llama() {
  say "== start llama-server (host, thinking off)"
  if [[ "$DRY" == 1 ]]; then
    say "  \$ llama-server -m $GGUF_PATH $LLAMA_FLAGS"
    say "  wait for http://127.0.0.1:$LLAMA_PORT/health, up to 300 s"
    return 0
  fi
  llama-server -m "$GGUF_PATH" "${LLAMA_ARGS[@]}" >"$WORK/llama-server.log" 2>&1 &
  LLAMA_PID=$!
  local i=0
  until curl -fs "http://127.0.0.1:$LLAMA_PORT/health" >/dev/null 2>&1; do
    kill -0 "$LLAMA_PID" 2>/dev/null || { tail -n 20 "$WORK/llama-server.log" >&2; return 1; }
    i=$((i + 1))
    [[ "$i" -lt 150 ]] || { warn "llama-server not healthy in 300 s"; return 1; }
    sleep 2
  done
  say "  llama-server is healthy (pid $LLAMA_PID)"
  : >"$WORK/llama-cpu.txt"
  (
    while kill -0 "$LLAMA_PID" 2>/dev/null; do
      ps -o %cpu= -p "$LLAMA_PID" >>"$WORK/llama-cpu.txt" 2>/dev/null || true
      sleep 2
    done
  ) &
  SAMPLER_PID=$!
}

# ---- notes -----------------------------------------------------------------------------------

cpu_summary() {
  if [[ -s "$WORK/llama-cpu.txt" ]]; then
    awk '{s += $1; if ($1 > m) m = $1; n++} END {printf "llama-server host CPU (ps, every 2 s, 100 = one core): mean %.0f, max %.0f over %d samples\n", s / n, m, n}' "$WORK/llama-cpu.txt"
  else
    echo "llama-server host CPU: no samples"
  fi
}

env_block() {
  local commit machine
  commit=$(git -C "$ROOT" rev-parse --short HEAD)
  machine="$(uname -srm); $(sysctl -n machdep.cpu.brand_string 2>/dev/null || echo 'cpu unknown'); macOS $(sw_vers -productVersion 2>/dev/null || echo '?')"
  cat <<EOF
- Commit: $commit$START_DIRTY
- Machine: $machine
- Docker: $(docker version --format '{{.Server.Version}}' 2>/dev/null || echo '?'), VM memory $DOCKER_MEM_MIB MiB
- uv $(uv --version 2>/dev/null | awk '{print $2}'), node $(node --version 2>/dev/null)
- LiteLLM image: $(grep -o 'ghcr.io/berriai/litellm:[^ "]*' "$LITELLM_COMPOSE" | head -n 1)
- Hosted model (\`big-default\`): $HOSTED_MODEL
- Pre-trained SLM (\`local-small\`): $GGUF_REPO @ $GGUF_REV / $GGUF_FILE
- GGUF sha256: $GGUF_SHA
- llama.cpp: $LLAMA_VERSION
- llama-server flags: \`$LLAMA_FLAGS\`
EOF
}

# write_note STEM NOTES_DIR TITLE WHY COMMAND LOG RC RESULTS_DIR
write_note() {
  local stem=$1 dir=$2 title=$3 why=$4 command=$5 log=$6 rc=$7 results=$8
  local date note json
  date=$(date -u +%F)
  if [[ "$DRY" == 1 ]]; then
    say "  would write $dir/$date-$stem.md (and $date-$stem.results.json), command, environment, output tail, results"
    return 0
  fi
  note="$ROOT/$dir/$date-$stem.md"
  json="$ROOT/$dir/$date-$stem.results.json"
  {
    echo "# $title, $date"
    echo
    echo "## Why"
    echo
    echo "$why"
    echo
    echo "## Setup"
    echo
    env_block
    echo
    echo "## Command"
    echo
    echo '```console'
    echo "\$ $command"
    echo '```'
    echo
    echo "## Output"
    echo
    echo "Exit code: $rc. The last 60 lines of the raw output, scrubbed of keys; the middle is trimmed."
    echo
    echo '```console'
    redact <"$log" | tail -n 60
    echo '```'
    if [[ -f "$results/results.md" ]]; then
      echo
      echo "## Results"
      echo
      redact <"$results/results.md"
      echo
      echo "The full numbers are in \`$(basename "$json")\`."
      redact <"$results/results.json" >"$json"
      NOTE_FILES+=("$dir/$date-$stem.results.json")
    fi
    if [[ -n "$EXTRA_SECTION" ]]; then
      echo
      echo "$EXTRA_SECTION"
    fi
    echo
    echo "## Reading"
    echo
    if [[ "$rc" == 0 ]]; then
      echo "To be written after review: the script records numbers and does not judge them."
    else
      echo "The run failed (exit code $rc). The failure stays in this note; see the output above."
    fi
  } >"$note"
  NOTE_FILES+=("$dir/$date-$stem.md")
  say "  wrote $dir/$date-$stem.md"
}

# ---- steps -----------------------------------------------------------------------------------

bakeoff_run() {  # LOG ROUTE OUTDIR
  POC06_LITELLM_KEY="$LITELLM_KEY" run_logged "$1" uv run python -m bakeoff run \
    --model-url "http://127.0.0.1:$LITELLM_PORT/v1" --model-key-env POC06_LITELLM_KEY \
    --route "$2" --tasks smoke,simplifier,lookup --repeat "$REPEAT" \
    --engine "$ENGINES_CSV" --lane inprocess,sidecar --out "$3"
}

# Words that name the engines and pairs for load_driver.py, one per line.
driver_args() {  # ENGINES PAIRS
  local e p
  for e in $1; do printf -- '--engine\n%s\n' "$e"; done
  for p in $2; do printf -- '--pairs\n%s\n' "$p"; done
}

build_scale_images() {  # LOG
  say "== build the PoC-4 scale images and the echo-openai-agents image"
  run_logged "$1" "$POC06_DIR/scale-slm.sh" build
}

step_hosted() {
  say; say "== step hosted: big-default, $HOSTED_MODEL, engines $ENGINES_CSV"
  local log="$WORK/hosted.log" out="$WORK/hosted-out" rc=0
  [[ "$DRY" == 1 ]] || : >"$log"
  if start_litellm "$log" hosted; then
    bakeoff_run "$log" big-default "$out" || rc=$?
  else
    rc=1
  fi
  stop_litellm
  write_note hosted-run "$NOTES_A" "PoC-6 hosted big-model run" \
    "PoC-6a and 6b exit criterion 5 (every engine on a hosted big model) and the token-overhead column. Trusted engines only: the untrusted ones run on kind under gVisor. No prompt-bytes column: the chassis calls LiteLLM, not the fake model server." \
    "uv run python -m bakeoff run --model-url http://127.0.0.1:$LITELLM_PORT/v1 --model-key-env POC06_LITELLM_KEY --route big-default --tasks smoke,simplifier,lookup --repeat $REPEAT --engine $ENGINES_CSV --lane inprocess,sidecar" \
    "$log" "$rc" "$out"
  return "$rc"
}

step_slm() {
  say; say "== step slm: local-small, $GGUF_FILE, engines $ENGINES_CSV"
  local log="$WORK/slm.log" out="$WORK/slm-out" rc=0
  [[ "$DRY" == 1 ]] || : >"$log"
  if start_llama && start_litellm "$log"; then
    bakeoff_run "$log" local-small "$out" || rc=$?
  else
    rc=1
  fi
  stop_litellm
  EXTRA_SECTION="## Server load

$(cpu_summary)"
  stop_llama
  write_note slm-run "$NOTES_C" "PoC-6c pre-trained SLM run" \
    "PoC-6c exit criteria 1, 2, and 5: every trusted 6a engine on Qwen3-1.7B through LiteLLM \`local-small\`, only \`--route\` changed from the hosted run. The model file hash, the llama.cpp version, and the server flags are in Setup." \
    "uv run python -m bakeoff run --model-url http://127.0.0.1:$LITELLM_PORT/v1 --model-key-env POC06_LITELLM_KEY --route local-small --tasks smoke,simplifier,lookup --repeat $REPEAT --engine $ENGINES_CSV --lane inprocess,sidecar" \
    "$log" "$rc" "$out"
  EXTRA_SECTION=""
  return "$rc"
}

step_scale() {
  say; say "== step scale: local-small, engines $SCALE_ENGINES, pairs $SCALE_PAIRS, one llama-server"
  local log="$WORK/scale.log" out="$WORK/scale-out" rc=0 a args=()
  [[ "$DRY" == 1 ]] || : >"$log"
  while IFS= read -r a; do args+=("$a"); done < <(driver_args "$SCALE_ENGINES" "$SCALE_PAIRS")
  if start_llama && build_scale_images "$log"; then
    [[ "$DRY" == 1 ]] || SCALE_UP=1
    POC06_SCALE_MODEL=slm POC06_LITELLM_KEY="$LITELLM_KEY" POC06_LLAMA_PORT="$LLAMA_PORT" \
      run_logged "$log" uv run python "$POC06_DIR/load_driver.py" --out "$out" \
      "${args[@]}" --only main --users "$SCALE_USERS" --duration-s 60 --warmup-s 10 || rc=$?
  else
    rc=1
  fi
  stop_scale
  EXTRA_SECTION="## Server load

$(cpu_summary)

How to read the limit: compare the per-container CPU in the results (chassis and workload, capped at 1 CPU each) with the llama-server CPU above (100 = one core) and its slot count (-np $LLAMA_PARALLEL). If throughput stops growing while the chassis and workload sit below their caps, the model server is the limit."
  stop_llama
  write_note scale-run "$NOTES_C" "PoC-6c scale run: N pairs on one model server" \
    "PoC-6c exit criterion 4: where throughput stops growing as 1, 2, and 4 agent pairs share one llama-server, and what limits it. The PoC-4 scale stack with LiteLLM added (deploy/compose/poc06/scale-litellm-slm.yaml) and every chassis on route \`local-small\`. $SCALE_USERS Locust users, 60 s per scenario." \
    "POC06_SCALE_MODEL=slm uv run python deploy/compose/poc06/load_driver.py --out <dir> ${args[*]} --only main --users $SCALE_USERS --duration-s 60 --warmup-s 10" \
    "$log" "$rc" "$out"
  EXTRA_SECTION=""
  return "$rc"
}

step_load() {
  say; say "== step load: fake model, engines $LOAD_ENGINES, pairs $SCALE_PAIRS"
  local log="$WORK/load.log" out="$WORK/load-out" rc=0 a args=()
  [[ "$DRY" == 1 ]] || : >"$log"
  while IFS= read -r a; do args+=("$a"); done < <(driver_args "$LOAD_ENGINES" "$SCALE_PAIRS")
  if build_scale_images "$log"; then
    [[ "$DRY" == 1 ]] || SCALE_UP=1
    POC06_SCALE_MODEL=fake run_logged "$log" uv run python "$POC06_DIR/load_driver.py" --out "$out" \
      "${args[@]}" --only main || rc=$?
  else
    rc=1
  fi
  stop_scale
  write_note load-run "$NOTES_A" "PoC-6a load run: echo-openai-agents and echo-typescript" \
    "PoC-6a exit criterion 4: throughput as pairs grow, on the fake model server, as PoC-4 did. It is the PoC-4 matrix, unchanged, through deploy/compose/poc06/load_driver.py, because scale.sh does not list echo-openai-agents. Same 64 users and 60 s per scenario." \
    "POC06_SCALE_MODEL=fake uv run python deploy/compose/poc06/load_driver.py --out <dir> ${args[*]} --only main" \
    "$log" "$rc" "$out"
  return "$rc"
}

step_kind() {
  say; say "== step kind: big-default, $HOSTED_MODEL, untrusted engines $UNTRUSTED_ENGINES, kind remote lane (gVisor)"
  local log="$WORK/kind.log" out="$WORK/kind-out" rc=0
  [[ "$DRY" == 1 ]] || : >"$log"
  if [[ "$DRY" == 1 ]]; then
    say "  \$ ${KIND_RUN#"$ROOT"/} up   # PoC-5 cluster kind-poc05, gVisor, the PoC-6 engines and remotes"
    say "  \$ printf '%s' \"\$PROVIDER_KEY\" | ${KIND_HOSTED#"$ROOT"/} up   # key from the env file, on stdin; not shown"
    say "  \$ ${KIND_HOSTED#"$ROOT"/} run <tmp>/kind-out   # POC06_REPEAT=$REPEAT"
    say "  \$ ${KIND_HOSTED#"$ROOT"/} down   # on any exit: the hosted route, the provider Secret, the egress policy"
    say "  \$ ${KIND_RUN#"$ROOT"/} delete    # on any exit"
  else
    KIND_UP=1
    if run_logged "$log" "$KIND_RUN" up; then
      KIND_HOSTED_UP=1
      if printf '%s' "$HOSTED_KEY" | POC06_HOSTED_MODEL="$HOSTED_MODEL" run_logged "$log" "$KIND_HOSTED" up; then
        POC06_REPEAT="$REPEAT" POC06_HOSTED_MODEL="$HOSTED_MODEL" run_logged "$log" "$KIND_HOSTED" run "$out" || rc=$?
      else
        rc=1
      fi
    else
      rc=1
    fi
    stop_kind
    EXTRA_SECTION="## Kind setup

- Cluster: PoC-5's \`kind-poc05\` (gVisor, agent-sandbox, admission rules) with the PoC-6 engines; \`$(basename "$KIND_RUN") up\` built the images, \`$(basename "$KIND_HOSTED") up\` added the hosted route.
- Engines (untrusted, remote lane, gVisor): $UNTRUSTED_ENGINES. Tasks: smoke, simplifier, lookup, $REPEAT repeats, through each chassis on route \`big-default\`.
- Route: \`big-default\` is $HOSTED_MODEL. Each chassis key lists two routes (\`fake-chat\`, \`big-default\`) and a budget of 1.0 USD (suggested).
- Provider key: only in the Secret \`litellm-provider\` and the LiteLLM pod env. LiteLLM is the only pod with internet egress, TCP 443 only. No workload holds a provider key.
- LiteLLM image: $(grep -o 'ghcr.io/berriai/litellm:[^ "]*' "$ROOT/deploy/kind/poc05/platform/litellm.yaml" | head -n 1)
- kind: $(kind version 2>/dev/null | head -n 1 || echo '?'); kubectl: $(kubectl version --client 2>/dev/null | head -n 1 || echo '?')
- Docker memory floor for this step: $KIND_MIN_MIB MiB (suggested)."
  fi
  write_note kind-run "$NOTES_B" "PoC-6b hosted run on kind: the untrusted engines" \
    "PoC-6b exit criterion 5 (every engine on a hosted big model) for the untrusted engines: echo-smolagents, echo-claude-agent, and kagent-adk, in the kind remote lane under gVisor, on LiteLLM route \`big-default\`. The trusted engines are in the hosted note of PoC-6a." \
    "deploy/kind/poc06/hosted.sh run <dir>   # uv run python -m bakeoff run --target echo-smolagents=<url> echo-claude-agent=<url> kagent-adk=<url> --tasks smoke,simplifier,lookup --repeat $REPEAT" \
    "$log" "$rc" "$out"
  EXTRA_SECTION=""
  return "$rc"
}

# ---- push ------------------------------------------------------------------------------------

do_push() {
  say; say "== push the notes"
  if [[ "$DRY" == 1 ]]; then
    say "  \$ git log @{u}..HEAD   # refused if any commit besides the notes is unpushed"
    say "  \$ git add -- <exactly the note files written above>"
    say "  \$ git commit -m 'docs(poc06): record the Mac runs' -- <the same files>"
    say "  \$ git push origin <current branch>   # refused on main and master"
    return 0
  fi
  [[ "${#NOTE_FILES[@]}" -gt 0 ]] || { say "  no notes were written"; return 0; }
  local branch
  branch=$(git -C "$ROOT" branch --show-current)
  case "$branch" in main | master | "") die "--push: refusing to push from '${branch:-detached HEAD}'" 1 ;; esac
  # Only the notes commit may leave: refuse when any other commit is not on the remote yet.
  local unpushed base
  if git -C "$ROOT" rev-parse --abbrev-ref '@{u}' >/dev/null 2>&1; then
    unpushed=$(git -C "$ROOT" log --format='%h %s' '@{u}..HEAD')
  else
    base=$(git -C "$ROOT" merge-base HEAD origin/main 2>/dev/null || true)
    [[ -n "$base" ]] || die "--push: no upstream and no origin/main to compare with; push the branch by hand first" 1
    unpushed=$(git -C "$ROOT" log --format='%h %s' "$base..HEAD")
  fi
  if [[ -n "$unpushed" ]]; then
    printf 'poc06_mac: --push: these commits are not pushed and would leave with the notes:\n%s\n' "$unpushed" >&2
    die "--push refused: push or drop them first" 1
  fi
  git -C "$ROOT" add -- "${NOTE_FILES[@]}"
  git -C "$ROOT" commit -m "docs(poc06): record the Mac runs (${ONLY# })" -- "${NOTE_FILES[@]}"
  git -C "$ROOT" push origin "$branch"
}

# ---- main ------------------------------------------------------------------------------------

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
  say; say "== preflight (would check)"
  say "  \$ docker info              # running; memory (Docker Desktop, about 7.75 GiB)"
  say "  \$ command -v uv node npm make openssl git curl"
  if want kind; then
    say "  \$ command -v kind kubectl jq   # and: Docker memory at least $KIND_MIN_MIB MiB; no kind cluster named $KIND_CLUSTER yet"
  fi
  if want hosted || want kind; then
    say "  presence of $HOSTED_KEY_ENV in ${ENV_FILE#"$ROOT"/}   # name only; the value is never printed"
  fi
  if want slm || want scale; then
    say "  \$ command -v llama-server; llama-server --help | grep -- --chat-template-kwargs; llama-server --version"
  fi
else
  preflight_real
  LITELLM_KEY="sk-poc06-$(openssl rand -hex 24)"
fi

# Only the steps that run the trusted engines need the TypeScript build; the kind step builds its own.
TS_CASE=",$ENGINES_CSV,$(echo "$LOAD_ENGINES" | tr ' ' ','),"
if ! want hosted && ! want slm && ! want load; then TS_CASE=","; fi
case "$TS_CASE" in
  *,echo-typescript,*)
    say; say "== TypeScript build"
    if [[ "$DRY" == 1 ]]; then say "  \$ make ts-check"; else make ts-check || die "make ts-check failed" 1; fi ;;
  *) ;;
esac
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
