# Findings R4: stateless and scalable

Session: R4. Area: `packages/chassis/src/chassis/server/` (`idempotency.py`, `config.py`, `config_loader.py`, `lifecycle.py`, `readiness.py`), `adapters/` (`valkey`, `s3`, `dapr`, `kafka`) with their tests and contract-suite bindings, `deploy/compose/` scale files, `deploy/kind/` (`cluster.yaml`, `run.sh`, `poc04/`), `docs/guides/poc-04-how-it-works.md`, `docs/contracts/contract-v3.md` and `contract-v4.md`. PoC: `pocs/poc-04-stateless-scalable`.
Rubric: [rubric.md](rubric.md).

## Summary

The PoC-4 core works and is well tested: the lease, fence, and replay rules have fake-clock tests, and every README evidence name exists. The top risks are config values the store can send that break exactly-once for the whole fleet (R4-08, R4-09), and test harness code that outlives the PoC but sits in `pocs/` behind a `sys.path` chain that PoC-5 and PoC-6 copy (R4-01, R4-02). The docs still call ADR-004 "Proposed" in six places, and about 20 tests use real sleeps as sync (R4-14 and the list at the end).

Not repeated here: R0-16 (`poc04_harness.py:316`), R3-06 (`idempotency.py:174`), R2-32, R2-33, and baseline B-1. 23 LOW findings that did not fit the cap are in the shared notes folder, `R4-dropped-low.md`.

## Findings

Most severe first. At most 30. Severity: CRITICAL, HIGH, MEDIUM, LOW.
Lens: simple, secure, maintainable, docs, tests. `[shared]` means the fix touches `Makefile`, `pyproject.toml`, `uv.lock`, CI, the root `CLAUDE.md`, or a PoC harness or conftest.

### R4-01 [HIGH] [maintainable] [shared] pocs/poc-04-stateless-scalable/tests/poc04_conftest.py:26
Problem: the conftest puts the PoC-2 and PoC-3 `tests/` folders on `sys.path` so `poc04_harness` can import `poc02_harness` and `poc03_harness`, and PoC-5 (`poc05_conftest.py:5`) and PoC-6a copy the chain.
Why it matters: the rubric forbids a `sys.path` insert in `pocs/*/tests`, and import order now decides which module wins.
Fix: move the shared pieces (`OutboundRouter`, `on_unix_socket`, `ENGINES`, `HarnessModel`, `Replicas`, `call`) into a test-support module under `packages/` and import it. Keeps behavior: yes.
Spec: "given a PoC-4 test imports the harness, when pytest collects it, then no `sys.path` entry for another PoC folder is needed."
Status: open

### R4-02 [HIGH] [maintainable] [shared] pocs/poc-04-stateless-scalable/load/idem_cost.py:29
Problem: the load tools that outlive PoC-4 (`run_matrix.py` 473 lines, `steady_client.py`, `locustfile.py`) live in `pocs/`, and `idem_cost.py:29` and `median.py:23` use `sys.path.insert` to import `run_matrix`.
Why it matters: CI (`.github/workflows/poc06-load.yml:70`), `scripts/poc06_mac.sh:328`, `deploy/kind/run.sh:107`, and `pocs/poc-06a-*/tests/test_poc06a_criteria.py:34` run files inside a closed PoC folder by path.
Fix: move the shared load code into a `packages/` module and make the PoC scripts and CI import it. Keeps behavior: yes.
Spec: "given CI and PoC-6 run the load matrix and the steady client, when they start, then they import a package and name no `pocs/poc-04-*` path."
Status: open

### R4-03 [HIGH] [simple] packages/chassis/src/chassis/server/idempotency.py:441
Problem: `Idempotency.begin` is 65 lines, one `while True` inside a `try`, and repeats "compute what is left, refuse at zero, sleep `min(poll_s, remaining)`" at :476-482 and :496-502.
Why it matters: claim, wait, takeover, and replay all meet here, so this is where the next change will go wrong.
Fix: split it into `_try_claim`, `_classify(raw)` (refusal, replay, or wait), and one `_wait_or_refuse(deadline)`, so `begin` only loops. Keeps behavior: yes.
Spec: "given a held key, an absent key, another entry version, a conflicting fingerprint, or a done entry, when `begin` runs, then each returns the same claim, replay, or refusal as before, and `begin` is under 40 lines."
Status: open

### R4-04 [HIGH] [docs] pocs/poc-04-stateless-scalable/README.md:15
Problem: ADR-004 is called "Proposed" in six places after the user accepted it on 2026-10-08: README:15, guide `poc-04-how-it-works.md:4` and `:343`, `notes/2026-10-01-debt.md:7`, `contract-v3.md:3` ("not written yet"), and `contract-v4.md:229` ("still Proposed").
Why it matters: README:15 is the answer a reader looks at first, and the guide and the debt note list pending work that `notes/backlog-changes.md:28-35` records as done.
Fix: say "accepted 2026-10-08" in all six places, drop the guide's :343 bullet, and close the debt item. Keeps behavior: yes.
Spec: "given the PoC-4 docs, when `grep -rn -i 'ADR-004.*proposed\|still Proposed\|not written yet' docs/guides/poc-04-how-it-works.md docs/contracts/contract-v3.md docs/contracts/contract-v4.md pocs/poc-04-stateless-scalable` runs, then it prints nothing."
Status: open

### R4-05 [HIGH] [tests] pocs/poc-04-stateless-scalable/tests/test_hidden_state.py:1
Problem: scenario docstrings quote a criterion that is not the README's words: `test_hidden_state.py:1` quotes a different criterion 8, `test_killed_replica.py:1` adds "mid-run", `test_idempotency_replicas.py:1` and `test_idempotency_valkey.py:1` say `Idempotency-Key` for "the same key", and `test_swap_drill.py:1` quotes a sentence that is not in the README.
Why it matters: `pocs/CLAUDE.md` asks for the criterion word for word, and a near-quote cannot be grepped against the README and can change what the test claims.
Fix: replace each quote with the README sentence, then say in plain words which clause the test covers. Keeps behavior: yes.
Spec: "given each exit criterion sentence in the README, when it is grepped in `tests/`, then the scenario file that proves it contains it exactly."
Status: open

### R4-06 [HIGH] [tests] pocs/poc-04-stateless-scalable/tests/test_graceful_shutdown.py:175
Problem: README-cited evidence tests have no docstring: both in this file (:175, :189), all five in `test_config_reload.py` (from :70), both in `test_config_minio.py` (:95, :156), and all three in `test_workload_drain.py` (:146, :221, :235); the criterion 6 sentence appears nowhere in the folder.
Why it matters: these tests are the proof for criteria 6 and 7, and nothing in them names what they prove.
Fix: give each a one-line docstring that starts with the README criterion sentence and names the part it covers. Keeps behavior: yes.
Spec: "given `grep -c 'Stopping a pair under load fails no request in flight' pocs/poc-04-stateless-scalable/tests/*.py`, when it runs, then each criterion 6 file prints at least 1."
Status: open

### R4-07 [HIGH] [tests] pocs/poc-04-stateless-scalable/tests/test_hidden_state.py:112
Problem: `test_run_b_sees_nothing_of_run_a_on_a_reused_workload` holds 13 asserts for six behaviors (answer, model input, task store, globals, files), asserts inside `for` and `if` (:134-139), and waits through a helper that returns quietly on timeout.
Why it matters: a failure does not say which hidden-state channel leaked or on which engine.
Fix: put the A-then-B run in a fixture and write one test per channel, parametrized by engine. Keeps behavior: yes.
Spec: "given run A with a marker on replica 0, when run B is sent to the same workload, then B's model calls hold no marker."
Status: open

### R4-08 [MEDIUM] [secure] packages/chassis/src/chassis/server/config.py:219
Problem: `IdempotencySpec.ttl_s` is `Field(default=86_400, gt=0)`, so a reloaded document can set `.inf` or a huge value; `ttl_ms` (`adapters/valkey/state.py:58`) then raises `OverflowError`, which is not in `_ERRORS`, or Valkey rejects the `PX` value.
Why it matters: after one bad bucket write, `Claim.finish` (`idempotency.py:378`) fails on every replica, so a finished run answers 500 or is never cached, and every keyed retry runs the agent again.
Fix: add `allow_inf_nan=False` and a `suggested:` upper bound to `ttl_s` and `lease_s`, and make `ttl_ms` raise `ValueError` out of range. Keeps behavior: yes.
Spec: "given a reloaded document with `spec.idempotency.ttl_s: .inf`, when it is parsed, then it is refused as `invalid` at `spec.idempotency.ttl_s` and the last good config stays."
Status: open

### R4-09 [MEDIUM] [secure] packages/chassis/src/chassis/adapters/s3/config.py:139
Problem: `_get` calls `response.read()` with no size cap, and `load` (:165) runs `yaml.safe_load`, which expands aliases; deep nesting raises `RecursionError`, which the `except yaml.YAMLError` at :166 does not catch.
Why it matters: every replica polls the same object, so one oversized or alias-bomb document can exhaust memory on all of them, and each restarted replica loads it again at start.
Fix: read at most a `suggested:` cap (256 KiB) and refuse more with `ConfigUnavailable`, and refuse documents with aliases or deep nesting. Keeps behavior: yes.
Spec: "given a config object over the cap or with an alias bomb, when `S3Config.load` runs, then it raises `ConfigUnavailable` without parsing it."
Status: open

### R4-10 [MEDIUM] [secure] packages/chassis/src/chassis/server/idempotency.py:454
Problem: a duplicate of an in-flight keyed call polls `set_if_absent` and `get` every `wait_poll_ms` (100 ms) for up to `budget.timeout_ms`, with no backoff, no waiter cap, and no disconnect check, because `serve.py:186` awaits `begin` before the disconnect watcher starts.
Why it matters: one slow call plus N duplicates sends about 20N operations a second to the shared Valkey, and a saturated store turns into 503 for every keyed call on every replica.
Fix: poll with jittered exponential backoff up to `wait_poll_ms`, and stop when the client goes away; a waiter cap with a 409 is a new refusal and goes to a proposal. Keeps behavior: yes (backoff); no (waiter cap).
Spec: "given a claim in flight and 500 duplicates of its key, when they wait for 1 s, then the store sees fewer than 500 × 10 operations."
Status: open

### R4-11 [MEDIUM] [secure] deploy/compose/docker-compose.scale.yaml:361
Problem: the one network `poc04` is not `internal: true`, so every workload (which shares its chassis's network namespace) can reach the internet, the LAN, and the host through `host.docker.internal`.
Why it matters: the reason `deploy/compose/SECURITY.md:69` gives (the published port and the provider call) does not hold here, since the stack uses the fake model and Traefik can publish from a second network.
Fix: make `poc04` internal, add an `edge` network that only `traefik` joins with `127.0.0.1:18080`, and correct SECURITY.md section 7. Keeps behavior: yes.
Spec: "given the merged scale and scale-dapr files, when the networks are read, then `poc04` has `internal: true` and only `traefik` is on a non-internal network."
Status: open

### R4-12 [MEDIUM] [secure] deploy/kind/poc04/base/network-policy.yaml:8
Problem: no PoC-4 test reads the default-deny policy or the agent pod's egress allow list (:30-56); `test_read_only_kind.py` checks pods, the Service, the cluster, and `run.sh` only.
Why it matters: this policy is what denies the metadata service and the Kubernetes API to the agent pod, and an edit that widens or deletes it still passes `make test-poc POC=04`.
Fix: add a static test over the rendered overlays, modeled on `test_poc05_netpol_static.py::test_poc05_netpol_no_rule_can_reach_metadata_or_api`. Keeps behavior: yes.
Spec: "given the rendered `native-sidecar` and `prestop` overlays, when the NetworkPolicies are read, then one is default-deny for all pods, the agent's egress allows only the fake model server, Valkey, and DNS, and no rule has an `ipBlock`."
Status: open

### R4-13 [MEDIUM] [secure] packages/contract-suites/src/chassis_contracts/containers/dapr.py:30
Problem: no test container image is pinned by digest (`daprd:1.16.0` here, `cp-kafka:7.6.0` in `kafka.py:14`, MinIO in `minio.py:20`, `valkey:8-alpine` in `valkey.py:17`), and Kafka and Valkey differ from the Compose pins (`apache/kafka:4.1.0`, `valkey:8.1.4-alpine` at `docker-compose.scale.yaml:267`, `:175`); kind uses a third Valkey (`deploy/kind/poc04/base/valkey.yaml:31`).
Why it matters: a moved tag runs unreviewed code with a host folder mounted, and the contract suite certifies a different product build from the one deployed, while `valkey.py:17` says it is "the image the Compose stack uses".
Fix: pin each constant to the digest Compose uses, use the same Valkey and Kafka in all three places, and list any intended difference in `deploy/compose/SECURITY.md`. Keeps behavior: yes.
Spec: "given every `*_IMAGE` constant in `containers/*.py` and the kind Valkey image, when matched against `@sha256:[0-9a-f]{64}$`, then all match and each equals its `image:` in `docker-compose.scale*.yaml`."
Status: open

### R4-14 [MEDIUM] [tests] [shared] packages/chassis/tests/test_shutdown.py:256
Problem: about 20 tests in this area use real sleeps as sync, the same pattern as B-1; the tightest is `test_a_stream_started_while_draining_closes_after_its_end_event`, with `sleep(0.6)` against a 0.5 s drain delay (about 0.15 s of margin). The full list is under "Wall-clock tests" below.
Why it matters: these are the drain, fence, and liveness tests that prove criteria 3, 6, and 9, and several can also false-pass when the timing slips (they skip the path they claim to test).
Fix: drive the drain, lease, and probe loops with the fake clocks the unit tests already use, or wait on an event the code sets, and keep real sleeps only as hang guards. Keeps behavior: yes.
Spec: "given a loaded machine (`stress` on all cores), when the listed tests run 50 times, then none fails and none passes without reaching the path it names."
Status: open

### R4-15 [MEDIUM] [simple] packages/chassis/src/chassis/server/idempotency.py:340
Problem: `Claim.finish` has two near-copies of `try: compare_and_set ... except StateUnavailable: counter("finish_failed"); log(...)`, one for the `too_large` marker (:369-376) and one for the `done` entry (:377-384).
Why it matters: a change to failure counting or the log text must be made twice, and the method is about 47 lines.
Fix: extract one `_write(entry, ttl_s)` that owns the try, the counter, and the log, and a `_done_entry(events, response)` builder. Keeps behavior: yes.
Spec: "given the store fails on the final write, when `finish` runs for a normal or a too-large result, then `finish_failed` is counted once and `finish` returns False."
Status: open

### R4-16 [MEDIUM] [maintainable] packages/chassis/src/chassis/server/config_loader.py:164
Problem: `_activate` takes `state: Any` and reaches into `state.idempotency.spec` with `getattr` and `hasattr` to swap the spec, and `_count_poll_failures` (:185) tests `hasattr(port, "on_poll_failed")`, which is S3 logic in the generic reloader.
Why it matters: a reload changes another object's attribute by duck typing, so only a runtime test notices when `Idempotency` or `S3Config` changes shape.
Fix: give `Idempotency` a typed `reload(spec)` method and let `S3Config` take its failure hook at construction. Keeps behavior: yes.
Spec: "given a reload that changes `spec.idempotency.ttl_s`, when it is activated, then `Idempotency` uses the new value through a typed method and mypy sees no `Any`."
Status: open

### R4-17 [MEDIUM] [maintainable] [shared] pocs/poc-04-stateless-scalable/tests/poc04_harness.py:417
Problem: `sse_events` repeats `poc02_harness.sse_frames` (:196) and `poc03_support.sse_data` (:192), and `test_graceful_shutdown.py:166` adds a fourth parser, `_events`.
Why it matters: the four parsers treat `\r\n`, `[DONE]`, non-JSON data, and the `event:` line differently.
Fix: keep one SSE parser in `chassis_contracts` helpers and use it in all four places. Keeps behavior: yes.
Spec: "given one SSE body with `[DONE]` and an `event:` line, when any PoC test parses it, then each gets the same frames."
Status: open

### R4-18 [MEDIUM] [maintainable] [shared] pocs/poc-04-stateless-scalable/tests/test_hidden_state.py:87
Problem: each file writes its own poll helper (`until` in `test_config_minio.py:88`, `_until` in `test_workload_drain.py:121`, `_until_pruned` here, `_wait_for_ready` in `test_liveness.py:85`, `wait_ready` in `test_compose_scale.py:43`, `_started` in `test_graceful_shutdown.py:134` and `test_shutdown.py:92`), while `chassis_contracts.events.wait_until` (:92) exists; `_until_pruned` returns quietly when time runs out.
Why it matters: each has its own deadline and failure style, the names say how they wait and not for what, and a quiet timeout hides a failure until a later assert.
Fix: use one sync and async `wait_until(condition, label, timeout)` from `chassis_contracts` helpers, with domain-named wrappers in `poc04_harness.py`. Keeps behavior: yes.
Spec: "given a condition that never becomes true, when any PoC-4 test waits on it, then it fails with the timeout and the label."
Status: open

### R4-19 [MEDIUM] [simple] packages/chassis/src/chassis/adapters/dapr/events.py:172
Problem: the publish retry loop (`attempts = len(backoff) + 1`, sleep `backoff[attempt - 1]`) is copied in `kafka/events.py:238-258`, and `PUBLISH_BACKOFF_S = (0.1, 0.5)` is defined twice (dapr :53, kafka :45).
Why it matters: two retry policies have to be kept equal by hand, and a fix to one will miss the other.
Fix: add one retry helper in `adapters/` and use it in both. Keeps behavior: yes.
Spec: "given a broker that fails twice then works, when either adapter publishes, then it makes three attempts with the same waits."
Status: open

### R4-20 [MEDIUM] [simple] packages/chassis/src/chassis/server/config.py:433
Problem: `RESTART_ONLY` is read by no code in `src/`; `restart_required` (`config_loader.py:127`) tests only `RELOADABLE`, and the docstring says the list is "not the complete list".
Why it matters: it is a second list that can drift from the real rule with no failing check.
Fix: delete it and its doc mentions, or make `restart_required` use it with a test that every non-reloadable path is listed. Keeps behavior: yes.
Spec: "given a changed field not in `RELOADABLE`, when the reload runs, then it is refused with `restart_required` and `grep -rn RESTART_ONLY packages/chassis/src` finds at most its use."
Status: open

### R4-21 [MEDIUM] [docs] pocs/poc-04-stateless-scalable/README.md:3
Problem: the metadata block (:3-5) has no `Builds on:` line and no `Hands to:` line.
Why it matters: the rubric requires both, and without them a reader cannot place PoC-4 between PoC-3 and PoC-5.
Fix: add "Builds on: PoC-3, one front door (native, OpenAI, Anthropic, MCP) for every engine in the PoC-2 lanes." and "Hands to: PoC-5, a disposable chassis replica (config from the store, idempotency in `StatePort`, `/ready`, drain, `EventPort`)." Keeps behavior: yes.
Spec: "given the README, when `grep -c '^Builds on:\|^Hands to:' pocs/poc-04-stateless-scalable/README.md` runs, then it prints 2."
Status: open

### R4-22 [MEDIUM] [docs] pocs/poc-04-stateless-scalable/README.md:7
Problem: the first three sentences after the title give status only ("10 of 11 exit criteria have evidence..."), and how to run it first appears at :63.
Why it matters: the rubric asks the first three sentences to say what it is, why it exists, and how to run it.
Fix: open with one sentence each on what PoC-4 is, why it exists, and `make test-poc POC=04`, then the status. Keeps behavior: yes.
Spec: "given the README, when the first three sentences after the metadata are read, then one holds `make test-poc POC=04`."
Status: open

### R4-23 [MEDIUM] [docs] docs/guides/poc-04-how-it-works.md:124
Problem: PoC-4 facts are said many times: the guide (:124, :156) restates the contract v3 idempotency rules (store key, fingerprint, `MIN_RUN_MS`, defaults, fence), `CLAUDE.md:17` restates the request path in a 76-word arrow sentence beside a mermaid of the same path, the drill and load commands appear in four docs (guide :305-321, `load/README.md:19-52`, README:66-72, CLAUDE.md:33), and the sidecar cost figures appear in about eight places.
Why it matters: the 122 X-5 rerun and any flag change will need edits in many places, and the missed ones become stale facts like R4-04.
Fix: keep each fact in one home (rules in contract v3, commands in `load/README.md`, numbers in the load-results note and README criteria 4 and 11) and link to it from the rest. Keeps behavior: yes.
Spec: "given the PoC-4 docs, when `grep -rln 'chassis:idem:v1\|kill_drill.py --mode kill --pair 2\|0.105 to 0.129' docs/guides pocs/poc-04-stateless-scalable` runs, then each string is found only in its home file."
Status: open

### R4-24 [MEDIUM] [tests] pocs/poc-04-stateless-scalable/tests/test_read_only_compose.py:117
Problem: tests carry the wrong criterion number: `test_pairs_are_explicit_and_chosen_by_profile` (:117) and `test_traefik_balances_all_four_chassis_on_ready` (:208) say "Exit criterion 4" (throughput), and `test_compose_scale.py:57` says "Criterion 1 (smoke)" (the swap drill) for a Traefik replay.
Why it matters: criterion 4 is flagged, and passing manifest checks under its number make it look better covered than it is; the README cites them under the Traefik scope item.
Fix: label the two as "Scope item: Traefik in front of N pairs" and the smoke test as criterion 2. Keeps behavior: yes.
Spec: "given `grep -n 'criterion 4' tests/test_read_only_compose.py`, when it runs, then it prints only the module note on what criterion 4 needs."
Status: open

### R4-25 [MEDIUM] [tests] packages/chassis/tests/test_idempotency_serve.py:233
Problem: asserts sit inside `if` or `for`: `test_same_key_other_input_is_422` (:233, :236) and `test_state_unavailable_is_503_for_a_keyed_call` (:283) branch on the interface, `test_idempotency_replicas.py:175` wraps an assert in `if interface != "mcp"`, and `test_read_only_compose.py` (:134, :146, :159, :180, :192, :201, :254, :272), `test_read_only_kind.py:241`, and `test_load_results.py:53` loop over services or files.
Why it matters: the first failing item stops the loop, the rest go untested, and the test id does not say which service or interface failed.
Fix: parametrize on interface, service, or file, with the expected body as `pytest.param` data, and assert once. Keeps behavior: yes.
Spec: "given each chassis and workload service in the scale file, when it is read, then it sets `read_only: true`, as its own test case."
Status: open

### R4-26 [MEDIUM] [tests] packages/chassis/tests/test_idempotency_serve.py:520
Problem: two README-cited evidence tests check many behaviors: `test_a_replica_cut_off_from_the_store_is_fenced_and_one_result_is_kept` is 99 lines with 14 asserts and two `if` blocks (:600, :608), and `test_reload_and_refusal_through_s3` (`pocs/.../test_config_minio.py:95`) is 59 lines across reload, two refusals in a loop, and a failed poll.
Why it matters: a failure cannot say whether the fence, the takeover, the publish, or the refusal broke.
Fix: move the setup to fixtures and split each into one test per behavior (fence, takeover, single publish; reload, refusal parametrized, poll failure). Keeps behavior: yes.
Spec: "given replica A cut off from the store mid-run, when its lease would pass, then A answers a retryable 409, as one test, and only B's result is published, as another."
Status: open

### R4-27 [LOW] [secure] packages/chassis/src/chassis/server/idempotency.py:176
Problem: `store_key` calls `idempotency_key.encode("utf-8")` on an unchecked key, so a lone surrogate (`"\ud800"` in JSON) raises `UnicodeEncodeError` before the `try` in `begin` (:444), and `serve.py:186` has no guard.
Why it matters: the caller gets a bare 500 with a traceback, the path the comment at `serve.py:231` says must never happen because SDKs retry it.
Fix: encode with `errors="surrogatepass"` so the key still hashes; a length cap with a 4xx refusal is a new error and goes to a proposal. Keeps behavior: yes.
Spec: "given a native body with `idempotency_key: \"\ud800\"`, when `/v1/run` is called, then it does not answer 500."
Status: open

### R4-28 [LOW] [secure] packages/chassis/src/chassis/server/config_loader.py:98
Problem: `content_hash(data)` runs before the `try` and calls `json.dumps(sort_keys=True)`, which raises `TypeError` for mixed key types (a YAML `1:` next to `foo:`) instead of `ConfigRejected`.
Why it matters: `on_change` is documented to never raise and to count `chassis.config.rejected`, but this logs as `poll_failed{TypeError}` and crashes startup with no refusal log.
Fix: compute the digest inside the `try`, and map `TypeError` and `RecursionError` to `ConfigRejected("invalid", [])`. Keeps behavior: yes.
Spec: "given a store document with top-level keys `1` and `foo`, when `on_change` runs, then it counts `chassis.config.rejected{reason=invalid}` and keeps the active config."
Status: open

### R4-29 [LOW] [secure] packages/chassis/src/chassis/server/idempotency.py:359
Problem: the `done` entry stores `request.model_dump`, which holds the raw client `idempotency_key` with the full input and output for `ttl_s` (24 h), while the module docstring (:99) says the store holds only the hash of the key.
Why it matters: the key is often built from business data and now sits in clear text at rest, and the docstring tells the reader otherwise.
Fix: correct the docstring now; storing the hash in place of the key changes the stored entry format, so it goes to a proposal. Keeps behavior: yes (docstring); no (entry format).
Spec: "given a finished keyed run, when the raw bytes under `store_key` are read, then the client's key string does not appear in them."
Status: proposal

### R4-30 [LOW] [secure] packages/chassis/src/chassis/adapters/s3/config.py:122
Problem: `CONFIG_S3_SECURE=false` is honored on every profile, including `cloud`, and `ValkeyState` takes a plain `redis://` URL with `VALKEY_PASSWORD` the same way.
Why it matters: the document that sets `spec.prompt`, `spec.model.route`, and `spec.limits` can then be swapped on the wire, and it passes every check because those paths are reloadable.
Fix: on the `cloud` profile, refuse `secure=false` and a non-TLS Valkey URL unless a named override is set. Keeps behavior: no (a plaintext cloud deployment fails at start).
Spec: "given profile `cloud` and `CONFIG_S3_SECURE=false`, when `build_ports` runs, then it raises with the variable name and does not connect."
Status: proposal

## Wall-clock tests (same pattern as B-1)

Every test in the area that waits on real time, except the two in B-1. Tight means the margin is under about 0.3 s or the test can pass without reaching the path it names. Lines checked by reading; nothing was run.

Tight:
- `pocs/.../test_timeout_budget_per_engine.py:205-216`, `test_past_timeout_ms_the_run_ends_with_a2a_timeout_and_the_task_is_cancelled` for echo_python, echo_pydanticai, echo_typescript: the run must reach the model inside `TIMEOUT_MS = 800` (:48), tightest for the TypeScript subprocess.
- `pocs/.../test_killed_replica.py:146`, `test_a_replica_cut_off_from_the_store_mid_run_is_fenced`: real `LEASE_S = 0.6`, fence at 0.48 s, 0.12 s between fence and takeover; `:173` bare `sleep(0.2)` for "no second publish" can only false-pass.
- `pocs/.../test_graceful_shutdown.py:154`, `:158` (used by `:175` and `:189`): `sleep(0.05)` then `/ready` must be 503; `sleep(delay_s + 0.2)` then the order is checked.
- `packages/chassis/tests/test_shutdown.py:100`, `test_sigterm_drains_the_in_flight_stream_after_ready_goes_false`: `:115` `sleep(0.05)`; `:122` `sleep(0.5)` against `delay_s=0.3`.
- `packages/chassis/tests/test_shutdown.py:241`, `test_a_stream_started_while_draining_closes_after_its_end_event`: `:256` `sleep(0.6)` against `delay_s=0.5` (about 0.15 s); `:249` `sleep(0.05)`.
- `packages/chassis/tests/test_shutdown.py:209`, `test_while_draining_every_public_response_closes_its_connection`: EOF within `wait_for(..., 0.3)` (:201); `:229-238` needs more than 10 request rounds inside the 1.0 s delay.
- `packages/chassis/tests/test_shutdown.py:133`, `test_a_second_signal_forces_exit`: `:145` `sleep(0.2)` then `not served.done()` (false-pass only).
- `pocs/.../test_workload_drain.py:146`, `:221`, `:235`: SIGTERM must land inside `HOLD_S = 1.0` (:49); if the call ends first the test passes without draining.
- `pocs/.../test_idempotency_replicas.py:129`, `test_concurrent_duplicates_run_once`, and `test_idempotency_valkey.py:73`: rely on the harness `SLOW` model's real 0.4 s sleep (`poc04_harness.py:126`); a late second call replays without waiting and the test still passes.
- `pocs/.../test_liveness.py:111`: coming back needs one probe answered under 100 ms (`FAST`, :31) through a real socket while hung probes hold threads.
- `packages/chassis/tests/test_readiness.py:121`, `test_the_monitor_task_starts_and_stops`: `sleep(0.05)` then "no more probes" (false-pass only).
- `packages/chassis/tests/test_client_disconnect.py:152`, `test_a_check_that_fails_stops_watching_and_waits_for_the_run`: a 40 ms gap (`DISCONNECT_POLL_S = 0.01` against a 0.05 s run) decides whether the broken check runs at all.
- Contract suites through every binding: `chassis_contracts/state.py:92`, `:100`, `:146` (`WAIT_PAST_TTL_S = 0.6`), `events.py:97`, `:196`, `:237`, `config.py:26`; integration `test_dapr_events_contract.py:75-77`, `:111-115`, `test_s3_config_contract.py:148`.

Wide (real waits, margin of seconds or more; listed so the set is complete):
- `pocs/.../test_killed_replica.py:70` (only :94 is a time assert, in B-1), `test_liveness.py:98` (8× margin), `test_hidden_state.py:87-91` (3 s), `test_workload_drain.py:87-97` (30 s) and `:254` (`elapsed < 5` against 0.3 s), `test_config_minio.py:88-92` (5 s, `network`), `test_swap_drill.py:212` (`wait(30)`), `test_compose_scale.py:43-52` (60 s, stack only), `test_kind.py:38`, `:75` (drill length), `packages/chassis/tests/test_readiness.py:153` (11× margin), `test_idempotency_serve.py:256`, `:383`, `:400` (real 30 ms and 1 ms timeouts, result does not depend on lateness), `chassis_contracts/containers/dapr.py:50-60` (startup poll).
