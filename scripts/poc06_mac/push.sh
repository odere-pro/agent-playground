# shellcheck shell=bash
# Sourced by scripts/poc06_mac.sh: the --push git flow. The settings are in lib.sh.
# shellcheck disable=SC2154

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
