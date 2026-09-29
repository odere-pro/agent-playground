---
name: git-flow
description: Branch, commit, and pull request conventions for this repo, including saving an approved plan under docs/plans. Use before committing or opening a PR.
---
# Git flow

- Branches: `poc-NN/<topic>` for PoC work, `chassis/<topic>` for shared code, `docs/<topic>` for docs only. Never commit to `main` directly once a remote exists.
- Commits: conventional commits (`feat`, `fix`, `test`, `docs`, `chore`, `refactor`), one logical change each, tests and docs in the same commit. Trailer `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>` on commits Claude wrote.
- Before a commit: `make quick` green. The pre-commit hook runs it too.
- Before a PR: `make check` green; the PR body follows `.github/PULL_REQUEST_TEMPLATE.md` with the evidence pasted.
- An approved plan is saved as `docs/plans/YYYY-MM-DD-<slug>.md` with `Status: in progress` in the first commit of the work, and set to `done` in the last.
- Never `git push --force`, never push to `main`, never `git reset --hard`. If a rebase is needed, ask.
- Planning docs in the same PR as the code they describe; run `make planning-check`.
