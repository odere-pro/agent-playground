# Improvements, in priority order

What is left after the core review of PoC-0 to PoC-7, ranked by risk. Written 2026-11-05.

- The review filed 285 findings. Fix sessions fixed 238 without a behavior change.
- 42 need a behavior change, so they wait for your decision in [proposals.md](proposals.md): the 41 filed proposals and F8-01. The fix sessions do not apply them.
- Each finding's outcome is on its `Status:` line, with the PR that closed it.

Ranks:

- **P0:** a hard requirement or a trust boundary is open. Decide first.
- **P1:** data reaches someone it should not, or a check fails open.
- **P2:** a refusal or a bound that is missing.
- **P3:** maintainability and test quality.
- **Process:** how the work itself should change.

## P0: trust boundaries

1. **R7-04: a streamed answer skips the output guard.** Secrets and PII in a `delta` frame reach the client unchecked. Either check streamed frames with a buffer, or refuse `stream` while the output guard is on. The PoC-7 README does not say the gap exists.
2. **R2-02: one shared bearer for every remote run.** A remote workload can charge model calls or write events under any run in flight. Mint a secret per run and require it with the run's `traceparent`. This adds a header to the proxy wire, so ask `chassis-architect` first.
3. **R5a-01: admission lets a sidecar workload get a service account token.** That undoes ADR-001 hard requirement 1, and nothing refuses it. Add rule 7e.
4. **R5a-02: the deployer identity can delete `default-deny`.** Drop `networkpolicies` from `agent-deployer`, and apply each policy as cluster admin.
5. **R5a-07, and the R2 note on `auth: none`: the public port takes no credential.** Any pod in a namespace with no policy can call `/v1/run`. The `cloud` profile also accepts `spec.adapters.auth: none`. Allow port 8080 only from the gateway namespace, and refuse `auth: none` in `cloud`.
6. **R5a-23: the code-runner dispatcher has no auth.** It serves `/mcp` on `0.0.0.0` and can create `SandboxClaim`s. Require a bearer from a file, and have LiteLLM send it.
7. **R6-11: remote runs share one pod.** Runs share a uid, `/tmp`, and a process tree, and smolagents runs model code in the server process. Use one sandbox per run, or one run per pod with a recycle after it.
8. **R7-15, and the R7 note on the guardrail server: two services take unauthenticated input.** The Collector's OTLP receiver forwards spans with the Langfuse credential, and the guardrail server has no auth. Put a bearer on both.

## P1: data exposure and fail-open checks

9. **R3-02 with R1-08: a run's own exception text reaches the caller.** This breaks the PoC-3 rule. Send `public_message(code)`, and keep the raw text in the log. Contract v2 and v5 change.
10. **R1-07 with R7-14: secrets pass redaction.** `Bearer sk-…`, `ghp_`, `xoxb-`, JWTs, and `password=` are not caught. Add a `SECRET` entity to `redact` and widen the guardrail scan, with the chassis fake to match (R7-19).
11. **Security review of #63: PII masking fails open.** When the Presidio analyzer answers with an error, LiteLLM lets text through unmasked. Set `pii_entities_config` so that path fails closed. Also add a stack test that stops the guardrail server and asserts a call is refused, since fail-closed is only checked statically today.
12. **R4-29, the half not fixed: the idempotency store keeps the raw key with the full input and output for 24 hours.** Store a hash of the key.
13. **R3-06: two principals with the same idempotency key share one cached result.** Hash the subject into the store key when auth is on.
14. **R2-10 with R7-16: any caller with the feedback scope can score any run.** A free-text label also goes into a metric. Bind feedback to the caller's subject, and count labels from a fixed set.
15. **R7-17: the evaluator gate fails open, and a client can steer its sampling.** Salt the sampling hash, and add a `fail_closed` option.
16. **R1-04: redaction misfires.** Dates, IP addresses, and order ids become `<PHONE_NUMBER>`, while some real phone numbers pass. Require a `+` or a separator, and add a Luhn check for cards.
17. **F8-01: a second signal does not force the drain to exit while a stream is open.** The old test passed only because it closed its stream.
18. **R6 note: an uncorrelated call can pick any route its key lists** (`model_proxy.py:555`). Pin uncorrelated calls to the default route.

## P2: missing refusals and bounds

19. **Unsafe config at startup** (R4-30, R5a-12, R5a-22, R5a-25). In `cloud`, refuse plaintext S3 and Valkey, a non-`https` JWKS URL, `--allow-any-proxy-host`, and a non-`https` LiteLLM MCP URL.
20. **Unbounded input** (R1-09, R2-31, R5a-11, R5a-24, R7-13, R2-11, R4-10, R3-30). Bound ids, tokens, code bodies, tool results, guardrail input, lane streams, and idempotency waiters. Stop clients from setting `x-chassis-interface`. Several of these need a contract version.
21. **More admission rules** (R5a-05, R5a-06, R5a-16). Refuse `shareProcessNamespace`, projected tokens in tool pods, and ephemeral containers that target the chassis.

## P3: maintainability and tests

22. **Being finished now: R4-02, R6-14, R7-18, R4-18.** F10's second PR moves the PoC-4 load tools and the eval trial into packages, hash-locks DeepEval and promptfoo, and shares the last PoC-4 wait helpers.
23. **Proposals that change tests or tool output** (R6-01, R6-07, R6-18, R6-19, R5b-08, R2-27, R3-14, R0-14, R1-28). Use one task source for the bake-off, and make criteria tests call real checks. Use one JSON Schema checker, and one error format for the chat adapters. Name env gates by what they enable, and give errors that say which adapter is missing.
24. **Docs left stale on purpose.** No session may change checkbox text, so you have to fix these two:
    - The PoC-7 README still says ADR-007 to 010 are "all Proposed", but all four say Accepted on 2026-10-30.
    - The PoC-6b README ticks criterion 3, while the PoC-6 planning doc lists the `kagent-adk` cells as open work for that criterion.

## Process

25. **Require CI before merge.** Merges ahead of CI broke `main` five times during the review:
    - the `agentctl` drift and the missing `launch.json`
    - the PoC-9 `uv` cache
    - `PassTime` failing codespell
    - detect-secrets line drift
    - `check` going past its time limit

    Protect `main`, and require `check` and `lint-extra`. GitHub auto-merge then works too, so nobody has to watch CI.
26. **R0-09 and R0-01: narrow the agent permissions in `.claude/settings.json`.** Agents may not edit that file, so you have to make this change. The allow rules `uv *`, `make *`, and `cat *` get around the deny list. Narrow them, and deny `uv pip`, `uv run curl`, and `.env` reads.
27. **R0-08: limit `SNYK_TOKEN`.** Give it only to `main` and `workflow_dispatch`, or put it behind an environment with a reviewer.
28. **Keep the installed hook current.** `make setup` copies the pre-commit hook, and the copy went stale, so the new lint step did not run for days. Have the session-start hook or `harness-lint` warn when `.git/hooks/pre-commit` differs from `scripts/git-hooks/pre-commit`.
29. **Run what this Mac skipped.** These have not run since the fixes:
    - `make test-integration`, which needs Docker
    - the PoC-4 and PoC-7 Compose drills
    - a local kind run of the PoC-5 and PoC-6 suites. CI's `remote-lane` and `poc06-kind` jobs passed.
    - an image build of the changed Dockerfiles
30. **1Password signing and SSH stopped agents three times.** If agents keep committing while you are away, give them a signing key that does not need a prompt.
31. **Watch `check`'s 25-minute limit.** It was 15 minutes, and the PoC-9 scaffold tests passed it. When a suite grows, put it in its own job, as #58 did.
32. **Fewer sessions at once.** Eight or more sessions at once hit the usage limit several times. They also made the timing tests flaky, which F8 has now fixed. Three or four at once is enough.
