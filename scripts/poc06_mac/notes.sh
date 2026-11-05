# shellcheck shell=bash
# Sourced by scripts/poc06_mac.sh: the dated notes. The settings are in lib.sh.
# shellcheck disable=SC2154

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
  if [[ "$(uname -s)" == Darwin ]]; then
    machine="$(uname -srm); $(sysctl -n machdep.cpu.brand_string 2>/dev/null || echo 'cpu unknown'); macOS $(sw_vers -productVersion 2>/dev/null || echo '?')"
  else
    machine="$(uname -srm); $(grep -m 1 'model name' /proc/cpuinfo 2>/dev/null | sed 's/.*: //' || echo 'cpu unknown'), $(nproc 2>/dev/null || echo '?') CPUs, no GPU offload; $(. /etc/os-release 2>/dev/null && echo "$PRETTY_NAME" || echo 'Linux')"
  fi
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
