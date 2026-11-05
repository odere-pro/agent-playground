# shellcheck shell=bash
# Sourced by scripts/poc06_mac.sh: the model file, the services, and the five steps. Each argument
# list is built once: the run uses it and the note records it. The settings are in lib.sh.
# Checked alone, its settings look unset and its state flags unused: lib.sh owns both.
# shellcheck disable=SC2034,SC2154

ensure_gguf() {
  say "== model file"
  # The `=` is curl's own syntax: `--proto =https` allows HTTPS and nothing else.
  # shellcheck disable=SC2191
  local fetch=(curl -fL --proto =https --proto-redir =https --retry 3 -o "$GGUF_PATH.part"
    "https://huggingface.co/$GGUF_REPO/resolve/$GGUF_REV/$GGUF_FILE")
  if [[ "$DRY" == 1 ]]; then
    say "  would use $GGUF_PATH; download it on first run:"
    show_cmd "${fetch[@]}"
    say "  would record its sha256 in ${PIN_FILE#"$ROOT"/} on the first run, and refuse a different hash after"
    return 0
  fi
  mkdir -p "$CACHE"
  if [[ ! -f "$GGUF_PATH" ]]; then
    say "  downloading $GGUF_REPO/$GGUF_FILE (about 1.8 GB) to $CACHE"
    "${fetch[@]}" || die "download failed" 1
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
  local log=$1 rc=0 key="" key_note="no provider key" up=(up -d --wait)
  if [[ "${2:-}" == hosted ]]; then
    key=$HOSTED_KEY
    key_note="provider key: from the env file, not shown"
  fi
  say "== start LiteLLM (project $LITELLM_PROJECT, 127.0.0.1:$LITELLM_PORT)"
  if [[ "$DRY" == 1 ]]; then
    show_cmd "docker compose -p $LITELLM_PROJECT -f ${LITELLM_COMPOSE#"$ROOT"/} ${up[*]}   # $key_note"
    return 0
  fi
  LITELLM_UP=1
  POC06_HOSTED_API_KEY="$key" compose_litellm "${up[@]}" >>"$log" 2>&1 || rc=$?
  if [[ "$rc" != 0 ]]; then
    redact <"$log" | tail -n 20
    return "$rc"
  fi
  say "  LiteLLM is healthy"
}

start_llama() {
  say "== start llama-server (host, thinking off)"
  local serve=(llama-server -m "$GGUF_PATH" "${LLAMA_ARGS[@]}")
  if [[ "$DRY" == 1 ]]; then
    show_cmd "${serve[@]}"
    say "  wait for http://127.0.0.1:$LLAMA_PORT/health, up to 300 s"
    return 0
  fi
  "${serve[@]}" >"$WORK/llama-server.log" 2>&1 &
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
  # Linux: CPU over each 2 s interval from /proc (utime + stime). `ps -o %cpu` there is the
  # average over the process's whole life, which hides load. macOS has no /proc; its `ps` %cpu
  # is a recent, decaying average, so it stays.
  (
    local stat="/proc/$LLAMA_PID/stat" hz prev now
    if [[ -r "$stat" ]]; then
      hz=$(getconf CLK_TCK 2>/dev/null || echo 100)
      prev=$(awk '{print $14 + $15}' "$stat" 2>/dev/null || echo 0)
      while kill -0 "$LLAMA_PID" 2>/dev/null; do
        sleep 2
        now=$(awk '{print $14 + $15}' "$stat" 2>/dev/null) || break
        awk -v d=$((now - prev)) -v hz="$hz" 'BEGIN {printf "%.1f\n", d * 100 / (hz * 2)}' >>"$WORK/llama-cpu.txt"
        prev=$now
      done
    else
      while kill -0 "$LLAMA_PID" 2>/dev/null; do
        ps -o %cpu= -p "$LLAMA_PID" >>"$WORK/llama-cpu.txt" 2>/dev/null || true
        sleep 2
      done
    fi
  ) &
  SAMPLER_PID=$!
}

# Only the steps that run the trusted engines need the TypeScript build; the kind step builds its own.
build_typescript() {
  local ts_case
  ts_case=",$ENGINES_CSV,$(echo "$LOAD_ENGINES" | tr ' ' ','),"
  if ! want hosted && ! want slm && ! want load; then ts_case=","; fi
  case "$ts_case" in
    *,echo-typescript,*)
      say; say "== TypeScript build"
      if [[ "$DRY" == 1 ]]; then show_cmd make ts-check; else make ts-check || die "make ts-check failed" 1; fi ;;
    *) ;;
  esac
}

# ---- steps -----------------------------------------------------------------------------------

# The bake-off command of one route, built once by the step: bakeoff_run runs it, the note records it.
bakeoff_cmd() {  # ROUTE: sets BAKEOFF_CMD
  BAKEOFF_CMD=(uv run python -m bakeoff run
    --model-url "http://127.0.0.1:$LITELLM_PORT/v1" --model-key-env POC06_LITELLM_KEY
    --route "$1" --tasks "smoke,simplifier,lookup" --repeat "$REPEAT"
    --engine "$ENGINES_CSV" --lane "inprocess,sidecar")
}

bakeoff_run() {  # LOG OUTDIR: runs BAKEOFF_CMD
  POC06_LITELLM_KEY="$LITELLM_KEY" run_logged "$1" "${BAKEOFF_CMD[@]}" --out "$2"
}

# Words that name the engines and pairs for load_driver.py, one per line.
driver_args() {  # ENGINES PAIRS
  local e p
  for e in $1; do printf -- '--engine\n%s\n' "$e"; done
  for p in $2; do printf -- '--pairs\n%s\n' "$p"; done
}

# The load_driver.py arguments of one run, built once by the step: the run uses them, the note
# records them.
driver_cmd() {  # ENGINES PAIRS [EXTRA ...]: sets DRIVER_ARGS
  local engines=$1 pairs=$2 a
  shift 2
  DRIVER_ARGS=()
  while IFS= read -r a; do DRIVER_ARGS+=("$a"); done < <(driver_args "$engines" "$pairs")
  DRIVER_ARGS+=(--only main "$@")
}

driver_note() {  # MODEL: the command the note records, from DRIVER_ARGS
  printf 'POC06_SCALE_MODEL=%s uv run python %s/load_driver.py --out <dir> %s' \
    "$1" "${POC06_DIR#"$ROOT"/}" "${DRIVER_ARGS[*]}"
}

build_scale_images() {  # LOG
  say "== build the PoC-4 scale images and the echo-openai-agents image"
  run_logged "$1" "$POC06_DIR/scale-slm.sh" build
}

step_hosted() {
  say; say "== step hosted: big-default, $HOSTED_MODEL, engines $ENGINES_CSV"
  local log="$WORK/hosted.log" out="$WORK/hosted-out" rc=0
  bakeoff_cmd big-default
  start_log "$log"
  if start_litellm "$log" hosted; then
    bakeoff_run "$log" "$out" || rc=$?
  else
    rc=1
  fi
  stop_litellm
  write_note hosted-run "$NOTES_A" "PoC-6 hosted big-model run" \
    "PoC-6a and 6b exit criterion 5 (every engine on a hosted big model) and the token-overhead column. Trusted engines only: the untrusted ones run on kind under gVisor. No prompt-bytes column: the chassis calls LiteLLM, not the fake model server." \
    "${BAKEOFF_CMD[*]} --out <dir>" \
    "$log" "$rc" "$out"
  return "$rc"
}

step_slm() {
  say; say "== step slm: local-small, $GGUF_FILE, engines $ENGINES_CSV"
  local log="$WORK/slm.log" out="$WORK/slm-out" rc=0
  bakeoff_cmd local-small
  start_log "$log"
  if start_llama && start_litellm "$log"; then
    bakeoff_run "$log" "$out" || rc=$?
  else
    rc=1
  fi
  stop_litellm
  EXTRA_SECTION="## Server load

$(cpu_summary)"
  stop_llama
  write_note slm-run "$NOTES_C" "PoC-6c pre-trained SLM run" \
    "PoC-6c exit criteria 1, 2, and 5: every trusted 6a engine on Qwen3-1.7B through LiteLLM \`local-small\`, only \`--route\` changed from the hosted run. The model file hash, the llama.cpp version, and the server flags are in Setup." \
    "${BAKEOFF_CMD[*]} --out <dir>" \
    "$log" "$rc" "$out"
  EXTRA_SECTION=""
  return "$rc"
}

step_scale() {
  say; say "== step scale: local-small, engines $SCALE_ENGINES, pairs $SCALE_PAIRS, one llama-server"
  local log="$WORK/scale.log" out="$WORK/scale-out" rc=0
  start_log "$log"
  driver_cmd "$SCALE_ENGINES" "$SCALE_PAIRS" --users "$SCALE_USERS" --duration-s 60 --warmup-s 10
  if start_llama && build_scale_images "$log"; then
    [[ "$DRY" == 1 ]] || SCALE_UP=1
    POC06_SCALE_MODEL=slm POC06_LITELLM_KEY="$LITELLM_KEY" POC06_LLAMA_PORT="$LLAMA_PORT" \
      run_logged "$log" uv run python "$POC06_DIR/load_driver.py" --out "$out" \
      "${DRIVER_ARGS[@]}" || rc=$?
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
    "$(driver_note slm)" \
    "$log" "$rc" "$out"
  EXTRA_SECTION=""
  return "$rc"
}

step_load() {
  say; say "== step load: fake model, engines $LOAD_ENGINES, pairs $SCALE_PAIRS"
  local log="$WORK/load.log" out="$WORK/load-out" rc=0
  start_log "$log"
  driver_cmd "$LOAD_ENGINES" "$SCALE_PAIRS"
  if build_scale_images "$log"; then
    [[ "$DRY" == 1 ]] || SCALE_UP=1
    POC06_SCALE_MODEL=fake run_logged "$log" uv run python "$POC06_DIR/load_driver.py" --out "$out" \
      "${DRIVER_ARGS[@]}" || rc=$?
  else
    rc=1
  fi
  stop_scale
  write_note load-run "$NOTES_A" "PoC-6a load run: echo-openai-agents and echo-typescript" \
    "PoC-6a exit criterion 4: throughput as pairs grow, on the fake model server, as PoC-4 did. It is the PoC-4 matrix, unchanged, through deploy/compose/poc06/load_driver.py, because scale.sh does not list echo-openai-agents. Same 64 users and 60 s per scenario." \
    "$(driver_note fake)" \
    "$log" "$rc" "$out"
  return "$rc"
}

step_kind() {
  say; say "== step kind: big-default, $HOSTED_MODEL, untrusted engines $UNTRUSTED_ENGINES, kind remote lane (gVisor)"
  local log="$WORK/kind.log" out="$WORK/kind-out" rc=0
  start_log "$log"
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
    "${KIND_HOSTED#"$ROOT"/} run <dir>   # uv run python -m bakeoff run --target echo-smolagents=<url> echo-claude-agent=<url> kagent-adk=<url> --tasks smoke,simplifier,lookup --repeat $REPEAT" \
    "$log" "$rc" "$out"
  EXTRA_SECTION=""
  return "$rc"
}
