# PoC-5 kickoff: what is left to finish

Paste the prompt below into a new Claude Code session opened at the repository root. It is self-contained: it names every file the session needs and the order of work. Background and evidence for what is already done are in `pocs/poc-05-sandboxed/notes/2026-10-02-handoff.md`.

```text
Finish PoC-5 (Sandboxed: the remote lane and the trust rule) in this monorepo.

ROLE
You are the orchestrator and judge. Plan, split the remaining work into atomic testable tasks, delegate each to a subagent (Opus 5.5 for security-sensitive, cluster, or large tasks; Sonnet 5.5 for small or docs tasks), review every result against its done check, and rerun a task until it meets it. Sandboxing is the core feature: every claim needs a test name or a pasted command output, and every refusal needs its paired allowed control in the same test.

READ FIRST (in order)
1. docs/planning/poc/005-PoC-5-sandboxed.md: the question and the 10 exit criteria.
2. docs/plans/2026-10-02-poc-05-sandboxed.md: the design; section 6 is the task list T01-T30 with owners, files, and done checks; section 7 lists the deliverables; section 8 the bring-up order and memory budget.
3. pocs/poc-05-sandboxed/notes/2026-10-02-handoff.md: what is done, the open findings, the lessons.
4. pocs/poc-05-sandboxed/notes/2026-10-02-bring-up.md: cluster facts already settled (items 1-9).
5. pocs/poc-05-sandboxed/notes/2026-10-02-review-security.md: the cluster verification list at its end.
6. pocs/poc-05-sandboxed/notes/2026-10-02-threat-model.md: check ids H01-H32 and boundaries B1-B12.

ALREADY DONE (do not redo): T01-T09, T11-T18, T20; two review rounds with fixes; all pods Running on kind cluster poc05; cluster items 1-9 answered. Offline gate was green: `PATH=/opt/homebrew/bin:$PATH make check` -> 2376 passed, 208 skipped, 5 xfailed. No README exit-criterion box is ticked yet.

WHAT IS LEFT, in this order

Step 0. Baseline. Run `PATH=/opt/homebrew/bin:$PATH make check` and `make test-poc POC=05`; paste the tails. Check the cluster: `kind get clusters`, `kubectl --context kind-poc05 get pods -A`.

Step 1. Ask me about T10 before anything touches it. T10 is the in-cluster probe workload (packages/workloads/hostile, an empty skeleton); earlier attempts to write it were stopped by a safety filter. Offer three options: I write it; drop it and verify controls from outside the pod only (criterion 8 closes as "partly shown"); close with criterion 8 flagged. Continue with the steps below while waiting; they must not depend on T10.

Step 2. Finish T19 (bring-up), one agent, holds Docker and kind:
- If the cluster is gone, run `deploy/kind/poc05/run.sh up` from nothing and record that it worked in one go (it was never shown end to end in one run). If it is up, run `up` again and show it converges. The last change in progress was the preflight memory check, which must not count our own poc05 node.
- Record one request through agent-echo (sidecar lane), one through chassis-echo-remote (remote lane: chassis -> remote Sandbox on gVisor -> chassis listener 8091 with its token -> LiteLLM with the chassis key -> fake model), and one tool call through the gateway. Add the missing "Requests" section to the bring-up note.
- Item 10: memory per pod and for the node once all are Ready; a table in the note.
- Run the kind tier: `POC05_KIND=1 uv run pytest -m network pocs/poc-05-sandboxed/tests -q -rs` (admission and tool-gateway files exist); fix what fails. Pass any key to a test through env read from the Secret in a subshell, never argv or a log.

Step 3. Cluster suites, one kind task at a time:
- T21 sidecar lane: criteria 3, 4, 5, 7 on kind. Hard requirement 1 per service (LiteLLM, MCP gateway, Valkey, MinIO): a call without the chassis's credential is refused and the same call through the chassis works. No provider key, internal credential, or service account token in the workload container. Metadata address, Kubernetes API (node IP:6443, not the Service IP), and the chassis public port on loopback unreachable from the workload; note that the metadata block cannot be demonstrated on Docker Desktop (nothing answers there), so record it as a static check plus a stated limit. Admission: run test_poc05_kind_admission.py.
- T22 remote lane: criteria 2, 4, 6, 8 for the remote lane. The same lane contract over the real remote pod; the remote reaches only the chassis listener and only with its own token (H17 to H20, H30); no secret but its own token; no service account token; read-only root; the PID limit (under gVisor, hitting it restarts the sandbox, so verify the limit is set rather than exhausting it); the code runner in its Sandbox.
- T23 gVisor overhead (criterion 9): each remote-capable engine (echo-python, echo-pydanticai, echo-langgraph, echo-typescript) answers under runsc; latency and memory under runsc vs runc; a dated note with the command; list exceptions (echo-typescript has no tool client).
- Without T10, do the checks from outside the pod: kubectl exec of standard tools already in our own images, pod specs, and cgroup files. Never write an attack tool.

Step 4. T24 CI: .github/workflows/remote-lane.yml runs the remote lane on kind; actions pinned by SHA; a wiring test test_poc05_ci_wiring.py. The x86_64 gVisor sha512 is not pinned in deploy/kind/poc05/install-gvisor.sh (it fails closed); pin it from the release's published checksum file and say so.

Step 5. T25 Kafka with SASL in a separate pass, other agent pods scaled down first; if it does not fit the 7.75 GiB VM, record H11 as an exception owned by platform-security, closing in 020 X-8.

Step 6. T26 demo: pocs/poc-05-sandboxed/demo/demo.sh and a dated record of its run. On kind it shows: a normal request through both lanes works; the remote lane's refusals (no token, wrong token, no run, unreachable internet, read-only root) each next to its allowed call; a sidecar workload calling LiteLLM and the MCP gateway directly is refused; an untrusted workload in the sidecar lane is rejected by admission. Exit 0 with a last line "result: every step ok".

Step 7. Docs the user asked for explicitly (docs-editor; every command in them must have been run once, by the demo or bring-up):
- .claude/skills/poc-05-operate/SKILL.md: bring the cluster up, run the offline and kind suites, run the demo, read the results, rotate a remote token, tear down, troubleshoot. Run `make harness-lint`.
- docs/guides/poc-05-runbooks.md: cluster will not start; sandbox pod not Ready; a refusal test passes when it should fail (control missing, DNS vs policy, wrong runtime); rotating the remote token (three steps, from packages/workload-a2a/README.md); adding an allow-listed tool (seed.sh and LiteLLM per-key MCP permissions); admission rejects a deploy (read the rule number in the message).
- docs/guides/poc-05-how-it-works.md with Mermaid diagrams: pod topology of both lanes; the trust-rule decision flow (admission rules 0-8); the NetworkPolicy map (from tests/fixtures/netpol_edges.yaml); the remote-lane request path; the credential map (which Secret is in which container). Check the Mermaid renders.

Step 8. T29 blind-spots note (criterion 10): what the chassis cannot see or control in the remote lane; start from threat-model section 5 and add what bring-up found. platform-security reviews it.

Step 9. T30 close:
- docs/contracts/contract-v4.md: additive changes (spec.trust, spec.engine.auth, uncorrelated cap and its 1024 default ceiling, remote listener 401/403 codes, tool error codes and public messages, agent.trust in /manifest, stricter admission rules 3 and 5 than the plan).
- pocs/poc-05-sandboxed/README.md: tick each scope item and exit criterion only with evidence (test name or command output); add the gate tails; status done, or in progress with the flagged criteria named.
- pocs/poc-05-sandboxed/notes/backlog-changes.md with NNN ID references (022 H-6, 054 H-16, 026 CH-4, 055 CH-6, 024 CH-3, 004 G-2, 020 X-8), applied with the planning-sync skill; `make planning-sync planning-check`.
- Update ADR-005 with what the cluster showed; keep it Proposed until I accept it.
- Carry these findings into the docs: LiteLLM's 401 body echoes the last 4 characters of a refused key; the gateway names tools <server>-<tool> and does not forward _meta (the adapter also sends the argument); the sidecar lane makes the chassis the native sidecar, the reverse of PoC-4 (for 024 CH-3); a pod creator can read its namespace's Secrets (PoC-5 removed pod-create from the submitter in poc05-agents); `chassis serve --host 0.0.0.0` has no CLI rule, the pod-IP bind is manifest-enforced; a stream closed early inside a run is uncharged (004 G-2); PoC-3's live MCP demo lists 0 tools without allow_all_keys.
- pocs/CURRENT moves to the next iteration only when the README says done.

After each step: `PATH=/opt/homebrew/bin:$PATH make check`. After steps 3, 6, and 9: a read-only `reviewer` and `platform-security` review, findings written to pocs/poc-05-sandboxed/notes/, then fixes.

RULES
- Only one agent at a time uses Docker or kind. The VM has 7.75 GiB and is shared with my other containers (paligo-*, opensearch): never stop, prune, or touch them; scale our own pods if memory is short and tell me.
- Every kubectl call pins --context kind-poc05; every kind call --name poc05.
- No secret value in any file, argv, log, or report.
- Run pytest over earlier PoCs only through scripts/check_offline.sh or with -m "not network"; plain uv run pytest starts Docker.
- Test basenames are unique repo-wide; PoC tests start with test_poc05_; kind tests are marked network and gated by POC05_KIND=1.
- Each agent owns named files; name the turn limit in each brief (developer and tester about 60, docs-editor 40, reviewer 30) and resume a stopped agent with SendMessage. Have agents append long notes one section per write and write long reports to a notes file.
- Refer to security checks by their H ids and describe them as "the control refuses X"; never write or ask for code that attacks.
- Commit only when I ask; never force-push.
```
