---
name: record-measurement
description: Record a measurement or a drill result in a PoC's notes/ folder so another engineer can rerun it and trust it. Use after a load run, a latency or memory measurement, a kind or Compose drill, or any run that backs an exit criterion.
---
# Record a measurement

A note is evidence only when someone else can rerun it and get the same kind of output.
Model: `pocs/poc-04-stateless-scalable/notes/2026-10-01-container-roles.md`.

## File

- `pocs/poc-NN-*/notes/YYYY-MM-DD-<topic>.md`, with today's date in UTC. One topic per file. Add a dated section for a later rerun instead of editing old numbers.
- Title line: what was measured and the date.

## What the note holds

1. **Why.** The exit criterion or the plan task this run backs, by number (for example "exit criterion 4, task T22").
2. **Setup.**
   - The commit: `git rev-parse --short HEAD`, plus "dirty" if `git status --short` is not empty.
   - The machine: OS, CPU, and the Docker VM memory (`docker info --format '{{.MemTotal}}'`).
   - Tool versions that matter: kind, the node image, uv, Python.
   - The cluster or Compose project and its context (`--context kind-pocNN`).
   - Replicas, concurrency, duration, and the profile or config file.
3. **Command.** The exact command, copied from the shell, not retyped.
4. **Output.** The tail of the raw output in a fenced block, with its exit code. Trim the middle, never the numbers. Say what was trimmed.
5. **Reading.** One to three sentences on what the numbers show, and whether the criterion passes, fails, or stays open. A failure stays in the note.
6. **Controls.** For a refusal or a deny result, name the control run that shows the target was reachable (see the PoC-5 review on `REFUSED_TCP`). A refusal without a control proves nothing.

## Rules

- No secret in a note: no key, no token, no key suffix or hash. Redact `sk-` runs and bearer values before you paste.
- Values the epic does not give are marked `suggested:`.
- Link the note from the PoC README box it backs, in the same commit.
- A measurement that changes a backlog issue goes to `notes/backlog-changes.md` too (skill `planning-sync`).
- A number that drives a decision beyond the PoC goes to an ADR (skill `adr`).
