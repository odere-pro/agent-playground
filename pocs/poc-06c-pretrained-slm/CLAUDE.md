# pocs/poc-06c-pretrained-slm

Context for this iteration. The root `CLAUDE.md` and `pocs/CLAUDE.md` still apply.

- **Read first:** `docs/planning/poc/006c-PoC-6c-pretrained-slm.md` (question and what is left; the exit criteria are in `README.md`), then `README.md` here (status and evidence).
- **Touches:** the LiteLLM config for the `local-small` route under `deploy/compose/`, the PoC-6a benchmark kit, and the SLM and scale run notes in `notes/`. Code that outlives the iteration goes to `packages/`, not here.
- **Done means:** every exit criterion in `README.md` has evidence, `make test-poc POC=06c` is green, the record is the SLM and scale run notes in `notes/` (`demo/` is empty), and `notes/backlog-changes.md` exists.
- **Do not touch:** the workloads or the chassis to make an engine work on the SLM. A needed change is a finding: record it under `notes/` and stop. Fine-tuning and vLLM serving belong to the SLM waves of the backlog.
- **Ask:** `chassis-architect` before a contract change, `platform-security` before anything with keys or egress, `eval-expert` for the task checks and the scorecard.
