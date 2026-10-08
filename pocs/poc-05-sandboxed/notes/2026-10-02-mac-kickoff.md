# PoC-5 kickoff for the Mac: the cluster steps, 2026-10-02

A cloud session did every offline step (commits `fb9f3e2` to `473cbec` on `poc-05/sandboxed`). It could not run kind: the session's permissions refused the kind and kubectl downloads, and the host has cgroup v1 (`2026-10-02-cloud-host-limits.md`). Everything that needs a cluster is left for a session on the Mac, where `kind-poc05` was brought up before. Paste the prompt below into a Claude Code session opened at the repository root.

## Decisions already made (do not reopen)

- T10, the in-pod probe workload, is dropped. Controls are checked from outside the pod: `kubectl exec` of standard tools already in our images, pod specs, cgroup files. Criterion 8 closes as "partly shown". Never write an attack tool.
- No broker in PoC-5. The events port stays broker-agnostic. H11 is an exception owned by platform-security, closing in 020 X-8 (`2026-10-02-h11-queue-exception.md`).
- Every subagent may run on Opus 5.5.
- 2026-10-08: "no secrets mounted" means no provider key and no internal credential. The remote pod's own token, one env var, is allowed; a test checks that nothing else is there.
- 2026-10-08: ADR-004 accepted. ADR-005 stays Proposed until T21 and T22 have run on kind; T30 updates it.
- The remote-lane workflow stays manual until the gVisor x86_64, kind, and kubectl sums are pinned and the three remote kind test files exist; `test_poc05_ci_wiring.py` enforces this.

## What the offline session left ready

- Offline tests: `test_poc05_hostile_offline.py` (H08, H13, H15/H16, H29), `test_poc05_events_agnostic.py`, `test_poc05_ci_wiring.py`, `test_poc05_pids_static.py`, `test_poc05_public_bind.py` (strict xfail, 026 CH-4).
- `run.sh` verbs `test-remote` (runs `test_poc05_kind_remote_lane.py`, `test_poc05_kind_remote_controls.py`, `test_poc05_kind_code_runner.py`; fails until they exist), `logs` and `redact` (every pod log goes through one redaction filter).
- `seed.sh` no longer mints the probe keys or `remote-probe-token`: re-seed after `up`.
- Docs: `docs/contracts/contract-v4.md`, `docs/guides/poc-05-how-it-works.md`, `notes/2026-10-02-blind-spots.md` (criterion 10), `notes/backlog-changes.md` (draft), ADR-005 (Proposed, deployer and submitter findings added).
- Fixed: LiteLLM's 401 echo of a key suffix and hash no longer passes the chassis's redaction (`test_litellm.py::test_401_litellm_echo_of_key_suffix_and_hash_is_redacted`).

## Prompt

```text
Finish PoC-5's cluster steps on this Mac. You orchestrate and judge; delegate each task to one subagent (Opus 5.5) with named owned files, a turn limit (developer/tester 60, docs-editor 40, reviewer 30), and a done check; resume stopped agents with SendMessage.

Read first: pocs/poc-05-sandboxed/notes/2026-10-02-mac-kickoff.md (decisions and what is ready), then 2026-10-02-handoff.md, 2026-10-02-bring-up.md, 2026-10-02-blind-spots.md, and docs/plans/2026-10-02-poc-05-sandboxed.md sections 4 to 8.

Steps, in order, one kind agent at a time:
0. `PATH=/opt/homebrew/bin:$PATH make check` and `make test-poc POC=05`; paste tails. Write the `make test` output to a log so a flaky failure is named (one unnamed shutdown flake on 2026-10-08). `kind get clusters`; `kubectl --context kind-poc05 get pods -A`.
1. T19: `deploy/kind/poc05/run.sh up` (from nothing if the cluster is gone; otherwise show it converges), then re-seed (the probe credentials are gone). Append a "Requests" section to the bring-up note: one request through agent-echo, one through chassis-echo-remote (to 8091 with its token, LiteLLM with the chassis key, the fake model), one tool call through the gateway. Item 10: a memory table per pod and for the node. Run `POC05_KIND=1 uv run pytest -m network pocs/poc-05-sandboxed/tests -q -rs`; fix what fails.
2. T21 sidecar on kind: test_poc05_kind_hardreq1.py and test_poc05_kind_sidecar_controls.py (criteria 3, 4, 5, 7). Hard requirement 1 for LiteLLM, the MCP gateway, Valkey, MinIO: refused without the chassis's credential from the sidecar workload container (kubectl exec, the image's Python, literal IPs from env), the same call through the chassis works. In the pod: H02 (no service account directory), H13 kind half (127.0.0.1:8080 refused, the pod IP answers), H25 (no Secret in env, names only), H26 (the chassis's /proc/1/environ not readable). H03/H04 at node IP:6443. H01 as a static check plus a stated limit (nothing answers on kind). Run test_poc05_kind_admission.py.
3. T22 remote on kind: test_poc05_kind_remote_lane.py (the same LaneContract requests to chassis-echo-remote, events compared with agent-echo), test_poc05_kind_remote_controls.py (H17 no/wrong token 401, outside a run 403, own token in a run 200; H18/H30 from a test pod; H19 with a literal outside IP, not DNS; H20; H21 via /proc/mounts / ro plus a /tmp write; H23 pids.max read from the pod cgroup, never exhausted; H28 runsc; the one-secret check: the remote pod's env holds exactly one value from a Secret, its own token, and no Secret volume is mounted), test_poc05_kind_code_runner.py (runsc, no egress, NPROC refusal after about 31 forks as the control). Then `run.sh test-remote` must pass.
4. T23 gVisor overhead (criterion 9): test_poc05_kind_gvisor.py and notes/2026-10-xx-gvisor-overhead.md; echo-python, echo-pydanticai, echo-langgraph, echo-typescript under runsc vs runc; start time, p50/p95 of 200 runs, working set; exceptions listed (echo-typescript has no tool client and no token flag). Then test_poc05_records.py (offline: the gVisor and blind-spots notes exist and cover their lists).
5. Reviewer and platform-security review; findings to notes/; fixes.
6. T26 demo: pocs/poc-05-sandboxed/demo/demo.sh and a dated record; both lanes answer; the remote refusals (no token, wrong token, no run, outside address, read-only root) each next to its allowed call; the sidecar workload calling LiteLLM and the MCP gateway directly refused, through the chassis allowed; admission rejects an untrusted sidecar pod and a third-party image (--dry-run=server, rule number in the message). Last line "result: every step ok", exit 0.
7. Docs with commands that were run: .claude/skills/poc-05-operate/SKILL.md (under 120 lines; `make harness-lint`) and docs/guides/poc-05-runbooks.md (cluster will not start; sandbox pod not Ready; a refusal passes when it should fail; rotating the remote token; adding an allow-listed tool; admission rejects a deploy). Refresh the "pending" cells in docs/guides/poc-05-how-it-works.md.
8. Pin the x86_64 gVisor sha512 in install-gvisor.sh and KIND_SHA256 / KUBECTL_SHA256 in .github/workflows/remote-lane.yml from the published files; switch the workflow to push and pull_request (the wiring test then requires it).
9. Close (T30): apply notes/backlog-changes.md with the planning-sync skill; `make planning-sync planning-check`; update ADR-005 with what the cluster showed (keep it Proposed); tick README boxes only with a test name or output, criterion 8 "partly shown", criterion 3 with the H11 exception; add the gate tails; set the status. Move pocs/CURRENT only if the README says done. Reviewer and platform-security once more.

Rules: only one agent on Docker or kind; never touch paligo-* or opensearch; every kubectl call pins --context kind-poc05 and every kind call --name poc05; no secret value in any file, argv, or log (read keys from the Secret into env in a subshell; print logs only through `run.sh logs`); every refusal test asserts its paired allowed control in the same test; refer to checks by H id as "the control refuses X"; run earlier PoCs' pytest only through scripts/check_offline.sh or -m "not network"; commit only when I ask; never force-push.
```
