---
name: git-flow
description: Branch, commit, worktree, and pull request conventions for this repo, including saving an approved plan under docs/plans. Use before committing, opening a PR, or starting a second line of work in parallel.
---
# Git flow

- Branches: `poc-NN/<topic>` for PoC work, `chassis/<topic>` for shared code, `docs/<topic>` for docs only. Never commit to `main` directly once a remote exists.
- Commits: conventional commits (`feat`, `fix`, `test`, `docs`, `chore`, `refactor`), one logical change each, tests and docs in the same commit. No AI co-author trailer, no "generated with" line, no mention of the tool.
- Commit messages are short: subject at most 72 characters, body at most 10 lines and only when the why is not obvious. Detail goes in the PR description.
- Before a commit: `make quick` green. The pre-commit hook runs it too.
- Before a PR: `make check` green; the PR body follows `.github/PULL_REQUEST_TEMPLATE.md` with the evidence pasted.
- An approved plan is saved as `docs/plans/YYYY-MM-DD-<slug>.md` with `Status: in progress` in the first commit of the work, and set to `done` in the last.
- Never `git push --force`, never push to `main`, never `git reset --hard`. If a rebase is needed, ask.
- Planning docs in the same PR as the code they describe; run `make planning-check`.

## Parallel work in worktrees

One checkout runs one line of work. A second line of work gets its own worktree, never a branch switch under another session.

1. `git fetch origin main`, then `git worktree add -b <branch> ../agent-orchestration.wt/<branch-with-dashes> origin/main`.
2. `git branch --unset-upstream` in the new tree, so a bare `git push` cannot target `main`. Push later with `git push -u origin <branch>`.
3. `make setup` in the new tree. The pre-commit hook lands in the shared git dir, so every worktree uses it.
4. Offline gates (`make quick`, `make test`, `make test-poc`) run in any number of worktrees at once.
5. Docker, Compose, and kind are one shared machine: kind cluster names, image tags, and host ports are fixed today. Run infra work (`make kind-poc0N`, `make test-integration`, `make load-test`, `make record-cassettes`) in one worktree at a time. Run `kind get clusters` and `docker ps` first; if another tree's cluster is up, do not touch it.
6. Edits to shared files (`pyproject.toml` members, `uv.lock`, `Makefile`, root `CLAUDE.md`, `.github/workflows/ci.yml`) land in a small `chassis/<topic>` PR first. Other branches rebase onto it, after asking.
7. Never use a bare `git stash`: the stash stack is shared by all worktrees. Use a WIP commit instead.
8. When the branch merges: `git worktree remove ../agent-orchestration.wt/<dir>`, then `git branch -d <branch>`.
