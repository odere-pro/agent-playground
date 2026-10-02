# PoC-5 security review of the offline session work, 2026-10-02

Read-only `platform-security` pass over `git diff c847a03..aed4e70`. Verdict: **retry**, two blocking findings. The NetworkPolicy map and the credential map in the guide match the manifests (16 egress and 12 ingress edges; every `secretKeyRef`, `secretRef`, and `secretName`). The H11 exception is honest. The offline H08, H13, H15/H16, and H29 tests each pair the refusal with its control.

## Blocking

1. **A refused chassis key's suffix and hash reach the remote lane (H06, H29).** `chassis/adapters/litellm/client.py` `_redact` strips the configured key and `sk-[A-Za-z0-9_-]{8,}`. LiteLLM's 401 text quotes `sk-...434e` and `Key Hash (Token) = <hash>`, which match neither. Checked by one direct call: `_redact` returns the input unchanged. When the chassis's own key is refused (after `seed.sh rekey`, or a Postgres restart on its emptyDir), the fragment goes into `ModelError.message`, the proxy's 500 body on 8091, the `error` event, and `/v1/run` output. Fix: strip `Received API Key = \S+` and `Key Hash (Token) = \S+`, or replace any 401 body with a fixed public message. Test in `test_litellm.py` with LiteLLM's exact text, plus a paired control. Correct the backlog row (026 CH-4, 022 H-6).
2. **The CI failure step and `run.sh explain` print LiteLLM's logs unfiltered.** LiteLLM logs at INFO and its 401 line carries the key suffix and hash. Fix: one redaction filter on every `kubectl logs` in the workflow and in `run.sh`, or skip `poc05-platform` logs. Extend `test_poc05_ci_wiring.py` to require it.

## Optional

3. kind and kubectl are checked against a same-channel sha256. Pin the sums in the job env; require them before the push trigger; assert `pull_request_target` is never a trigger.
4. The setup-uv pin `887a942a…` is tag `v5.1.0` (`git ls-remote`); fix the comment in `remote-lane.yml` and `ci.yml`.
5. The CI job runs no H check on kind; say criterion 1's kind half is not in CI yet.
6. Blind-spots note: cite `test_poc05_pids_static.py`; say H19 is not shown (only DNS failed, a section 6 pitfall); under gVisor H21 shows "Permission denied", and `/proc/mounts` `/ ro` carries it. For the Mac run: `kubectl exec` into the sidecar workload container can show H02, H13 (kind half), H25, H26 in the pod.
7. Guide: probe pods are dropped, not "not built yet"; no kind suite with paired controls exists without T10; H05-H08 and H10 have bring-up evidence, from where.
8. With T10 dropped, `seed.sh` still mints `chassis-probe-sidecar-litellm`, `chassis-probe-remote-litellm`, and `remote-probe-token`, which no pod mounts. Drop the `probe-*` entries.
9. H11 note step 1 names the probe pod; use the sidecar workload container instead.
10. The deployer keeps pod create and NetworkPolicy write in `poc05-agents`, so it can read chassis Secrets through a pod. Record in ADR-005 that the deployer is inside the credential boundary, with rules 6a-6c as the control (6b forbids `command`, not `args`). Optional: a strict xfail showing the CLI accepts `--host 0.0.0.0` today.
11. H29 route: pin `== 500`.
12. PID test: `0 < limit` lets 1 pass. Assert `3 * (NPROC_ALLOWANCE + baseline) + headroom < podPidsLimit <= 256` (factor 3 from spike question 6, `suggested:`), so the code runner's in-guest cap is hit before the sandbox's.
