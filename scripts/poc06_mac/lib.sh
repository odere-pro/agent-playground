# shellcheck shell=bash
# Sourced by scripts/poc06_mac.sh (ROOT is set there): settings, arguments, teardown, helpers, and
# preflight. Checked alone, the assignments here look unused: the other files read them.
# shellcheck disable=SC2034

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
LLAMA_HOST=${POC06_LLAMA_HOST:-127.0.0.1}
KIND_MIN_MIB=${POC06_KIND_MIN_MIB:-6000}
# Qwen3 thinking off: the model's own chat-template switch, passed to llama.cpp's Jinja engine.
# Recorded in every slm note. The alternative is `--reasoning-budget 0` (llama.cpp's own switch).
LLAMA_ARGS=(--jinja --chat-template-kwargs '{"enable_thinking":false}' -c "$LLAMA_CTX" -np "$LLAMA_PARALLEL"
  --host "$LLAMA_HOST" --port "$LLAMA_PORT" --alias local-small)
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
BAKEOFF_CMD=()
DRIVER_ARGS=()

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

parse_args() {
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
}

want() { case " $ONLY " in *" $1 "*) return 0 ;; *) return 1 ;; esac; }

check_settings() {
  local step
  for step in $ONLY; do
    case "$step" in hosted | slm | scale | load | kind) ;; *) die "--only: hosted, slm, scale, load, or kind (got $step)" ;; esac
  done
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
  case "$LLAMA_HOST" in 127.0.0.1 | 0.0.0.0) ;; *) die "POC06_LLAMA_HOST must be 127.0.0.1 or 0.0.0.0" ;; esac
}

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

# Called by the entry after the arguments are checked. A trap set in a function stays set.
install_traps() {
  trap cleanup EXIT
  trap 'exit 130' INT
  trap 'exit 143' TERM HUP
}

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
    show_cmd "$@"
    return 0
  fi
  printf '$ %s\n' "$*" | tee -a "$log"
  "$@" 2>&1 | tee -a "$log" || rc=${PIPESTATUS[0]}
  printf 'exit code: %s\n' "$rc" >>"$log"
  return "$rc"
}

show_cmd() { say "  \$ $*"; }

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

# Start a step's log empty. A dry run writes nothing.
start_log() {
  if [[ "$DRY" != 1 ]]; then : >"$1"; fi
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
  need_file "$ROOT/packages/load-tools/src/load_tools/run_matrix.py"
  need_file "$ROOT/packages/load-tools/src/load_tools/locustfile.py"
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

preflight_dry() {
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
    command -v llama-server >/dev/null 2>&1 || die "llama-server not found; run: brew install llama.cpp (Linux: put a llama.cpp release on PATH)" 1
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
