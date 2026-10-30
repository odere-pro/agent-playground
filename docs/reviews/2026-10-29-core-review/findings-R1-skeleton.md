# Findings R1: walking skeleton

Session: R1. Area: `packages/chassis/src/chassis/{core,ports,fakes}/`, `profiles.py`, `schemas.py`, `redaction.py`, `tracecontext.py`, `packages/chassis/schemas/`, the chassis tests for these modules, `packages/contract-suites/`, `packages/fake-model-server/`, `packages/chassis/README.md` and `CLAUDE.md`, `docs/guides/chassis-reference.md`, `docs/guides/adding-a-port.md`, `docs/contracts/contract-v0.md`. PoC: `pocs/poc-01-walking-skeleton/`.
Rubric: [rubric.md](rubric.md).

## Summary

The core is in good shape. `core`, `ports`, and `fakes` import nothing from `server/` or `adapters/`, no function there is over 50 lines, every port has a fake and a contract suite, every envelope and event model uses `extra="forbid"`, and a secret grep found no real credential in fixtures, cassettes, or demo records. The top risk is `redaction.py`: its email pattern is quadratic on caller text (40k characters take 1.2 s, so a 1 MiB body can stall the event loop for minutes), and its phone pattern rewrites dates and IP addresses, with only five tests and no negative case. The docs have drifted: three files name three different "current" contracts, `adding-a-port.md` sends new ports to the frozen v0 record, and the PoC-1 HOW-TO-RUN says the public port has no `/v1/chat/completions`, which has been false since PoC-3.

Counts: 5 HIGH, 20 MEDIUM, 5 LOW. Proposals (behavior changes): R1-04, R1-07, R1-08, R1-09, R1-28.

Evidence for R1-01 and R1-04 (run in this session):

```
$ UV_NO_SYNC=1 uv run python -c "...redact('a-'*(n//2)) for n in 10k,20k,40k; redact(dates, IPs)..."
10000 0.07 s
20000 0.29 s
40000 1.17 s
'released <PHONE_NUMBER> ok'
'ip <PHONE_NUMBER>'
'Bearer sk-abc123'
```

## Findings

### R1-01 [HIGH] [secure] packages/chassis/src/chassis/redaction.py:19
Problem: The `EMAIL_ADDRESS` pattern has no left anchor and an unbounded local part, so it runs in quadratic time on a long run of `[A-Za-z0-9._%+-]` with no `@`.
Why it matters: `redact` runs synchronously on caller text (result events in `server/results.py`, feedback comments, `Error.message` in the pipeline), and the 1 MiB body cap allows a stall of many minutes on the event loop.
Fix: Add the lookbehind `(?<![A-Za-z0-9._%+-])` before the local part; a leftmost match already starts at such a position, so the matches do not change.
Keeps behavior: yes.
Spec: "given a 200k-character string of `a-` with no `@`, when `redact` runs, then it returns in under 100 ms and the string is unchanged."
Status: open

### R1-02 [HIGH] [docs] pocs/poc-01-walking-skeleton/HOW-TO-RUN.md:64
Problem: "The public port has no `/v1/chat/completions`" is false since PoC-3, and line 46 lists only `/health`, `/ready`, and `/v1/run` on 8080.
Why it matters: A reader who curls 8080 gets the agent through the OpenAI interface, not the 404 the doc predicts; this is the exact mix-up `packages/chassis/CLAUDE.md` warns about.
Fix: Say the public port also serves the OpenAI and Anthropic interfaces, `/v1/mcp`, and `/manifest`, and link to the chassis README endpoint list instead of repeating it.
Keeps behavior: yes.
Spec: "given the fake profile running, when a reader POSTs to 127.0.0.1:8080/v1/chat/completions, then HOW-TO-RUN says it reaches the OpenAI interface (the agent), not the model proxy."
Status: open

### R1-03 [HIGH] [docs] packages/chassis/README.md:3
Problem: The README and `docs/guides/chassis-reference.md:14` name contract v3 as current, `packages/chassis/CLAUDE.md:4` names v6, `pocs/poc-01-walking-skeleton/CLAUDE.md:16` says "v1 is current", and v6 itself is marked draft while v5 says it is in force.
Why it matters: Four copies of one fact give four answers, so a reader cannot tell which contract to build to.
Fix: State the current contract once in `packages/chassis/CLAUDE.md` ("v6 draft over v5 in force") and have the README, the reference, and the PoC-1 CLAUDE.md link to that line.
Keeps behavior: yes.
Spec: "given a new engineer, when they open the chassis README, CLAUDE.md, the reference, or the PoC-1 CLAUDE.md, then all four lead to the same current contract."
Status: open

### R1-04 [HIGH] [secure] packages/chassis/src/chassis/redaction.py:25
Problem: The phone pattern rewrites non-PII (`released 2026-10-29` and `ip 192.168.100.200` both become `<PHONE_NUMBER>`, and so does `order 1234567890`), while SSNs without dashes and lowercase IBANs slip through; `test_redaction_and_chunks.py:28` has no negative case.
Why it matters: Stored result events and logs lose dates and addresses silently, and the suite stays green.
Fix: Require a leading `+` or a separator for phones, add a Luhn check for cards, and add a parametrized `test_redact_leaves_<kind>_alone` over dates, IPs, versions, and order ids.
Keeps behavior: no (the redacted text in result events and logs changes).
Spec: "given 'released 2026-10-29 from 10.0.0.12, order 1234567890', when `redact` runs, then the text comes back unchanged."
Status: proposal

### R1-05 [HIGH] [tests] pocs/poc-01-walking-skeleton/tests/test_day0.py:31
Problem: Exit criteria 1 and 2 cite whole files (`test_day0.py`, `test_contracts.py`) instead of a named test, and `test_fake_profile_builds_the_four_day0_ports` claims "a contract suite that the fake passes" but only checks `isinstance`.
Why it matters: `pocs/CLAUDE.md` asks for a test name per box; nothing asserts "no network, no keys", and nothing names the four suites the fakes pass.
Fix: Add one scenario test per criterion (sockets disabled and no `*_API_KEY` in the run; the four fakes bound by suite name) and cite those names on README lines 92 and 93; the boxes stay ticked.
Keeps behavior: yes.
Spec: "given the fake profile, when the offline gate runs, then no socket is reachable and no `*_API_KEY` is set."
Status: open

### R1-06 [MEDIUM] [docs] docs/guides/adding-a-port.md:12
Problem: Step 6 says to document a new port in `docs/contracts/contract-v0.md`, which calls itself the superseded PoC-1 record; the `contract-suite` skill (`.claude/skills/contract-suite/SKILL.md:14`) has also drifted from this guide (it registers only in `REGISTRY` and omits `PORTS`, `AdapterSpec`, `PROFILE_DEFAULTS`, and the `lazy` factory).
Why it matters: The next port lands in a frozen record, and an agent that follows the skill builds a port that `profiles.py` rejects or that loads its SDK eagerly.
Fix: Point step 6 at the contract in force and at `chassis-reference.md`, and cut the skill down to a pointer to this guide.
Keeps behavior: yes.
Spec: "given an engineer or agent adding a port, when they follow the guide or the skill, then they take the same steps and edit the contract in force."
Status: open

### R1-07 [MEDIUM] [secure] packages/chassis/src/chassis/redaction.py:18
Problem: `PATTERNS` covers PII only; `Bearer sk-abc123` passes through unchanged, and there is no pass for `sk-`, `AKIA`, `ghp_`, or PEM shapes.
Why it matters: The pipeline writes a workload or connector `Error.message` to logs and spans through this `redact` alone, and upstream model errors can echo a key (a test fixture shows `ModelError("Received API Key = sk-...")`).
Fix: Add a `SECRET` entity (Bearer tokens, key shapes, PEM headers) to `PATTERNS`, replacing with `<SECRET>`.
Keeps behavior: no (the redacted text in result events and logs changes).
Spec: "given an engine `Error.message` of 'upstream said Bearer abc.def-123 and sk-abcdefgh12345678', when it is redacted, then neither token remains."
Status: proposal

### R1-08 [MEDIUM] [secure] packages/chassis/src/chassis/core/collector.py:45
Problem: `collect` copies the raw `Error.message` into `output.error.message`, which the native `/v1/run` and MCP replies return to the caller.
Why it matters: OpenAI and Anthropic send `public_message(code)` because run-error text can carry internal URLs or a pydantic `input_value=...`; native and MCP bypass that, as contract-v2 line 85 documents.
Fix: Return `public_message(code)` in the native and MCP envelope and keep the raw text in the log and span.
Keeps behavior: no (it changes the `output.error.message` wire value that contract-v2 freezes).
Spec: "given a run that ends with `Error(code='a2a.bad_event', message=<text with an internal URL>)`, when `/v1/run` answers, then `output.error.message` is the fixed public text."
Status: proposal

### R1-09 [MEDIUM] [secure] packages/chassis/src/chassis/core/envelope.py:40
Problem: `request_id`, `trace_id`, and `idempotency_key` (also lines 55-57 and 67-69) have no length or charset limit, and event payloads (`core/events.py:61`: `Error.message`, `Delta.text`, `ToolCall.arguments`, `Metrics` counts) have no `max_length` or `ge=0`; the published schemas match that looseness.
Why it matters: Control characters and values up to the 1 MiB body cap reach logs, span attributes, state keys, and the `collect` token sums, and a negative token count is accepted.
Fix: Add `Field(min_length=1, max_length=128, pattern=...)` on id fields and `max_length` or `ge=0` on event fields, then run `make schemas`; ask `chassis-architect` first.
Keeps behavior: no (it narrows the wire contract and the event schema).
Spec: "given an `idempotency_key` with a newline or a `Metrics(input_tokens=-5)` event, when the chassis parses it, then it is refused."
Status: proposal

### R1-10 [MEDIUM] [simple] packages/contract-suites/src/chassis_contracts/interface.py:637
Problem: `interface.py` is 934 lines and `inbound.py` is 880 lines, both over the 800 limit; lines 124-636 of `interface.py` are a serving and client harness, and lines 1-400 of `inbound.py` are case tables and an SSE parser.
Why it matters: A reader changing one case scrolls past hundreds of lines of plumbing, and the harness cannot be reused without importing the suite.
Fix: Move the harness to `interface_harness.py` and the tables and `sse()` to `inbound_cases.py`, re-importing them; one PR per file.
Keeps behavior: yes.
Spec: "given the split files, when `pytest packages/chassis packages/contract-suites` collects, then the same test ids appear and no file is over 800 lines."
Status: open

### R1-11 [MEDIUM] [simple] packages/chassis/src/chassis/tracecontext.py:13
Problem: W3C `traceparent` logic lives in two modules: this file declares again `TRACEPARENT_LEN = 55` next to `core/trace.py`'s `_TRACEPARENT_LEN`, and neither `tracecontext.py` nor `core/trace.py` has a direct test (`parse_traceparent` is tested only through the `server.correlation` re-export).
Why it matters: A change to version handling must be made twice, and the 55-character cut, lowercasing, and `None` path are unpinned.
Fix: First add `test_trace.py` with parametrized cases importing from `chassis.core.trace`, then move `continued` and `parent_span_id` into `core/trace.py` and update the three importers.
Keeps behavior: yes.
Spec: "given a valid traceparent with upper case and trailing spaces, when `continued` is called, then it returns the 55-character lowercase form, and `None` for an invalid header."
Status: open

### R1-12 [MEDIUM] [maintainable] packages/chassis/src/chassis/ports/state.py:50
Problem: `InMemoryState`, a full fake with a clock and `fail_next`, lives in `ports/` because `PortBundle.state` defaults to it (`ports/bundle.py:27`); `fakes/state.py` is a 7-line re-export and tests import from both paths.
Why it matters: It breaks "fakes live in `fakes/`", and a hand-built bundle silently gets a private per-replica store, the hidden state `packages/chassis/CLAUDE.md` forbids.
Fix: Make `state` a required `PortBundle` field (`build_ports` already passes it), move the class to `fakes/state.py`, and drop the re-export.
Keeps behavior: yes (`build_ports` is unchanged; a hand-built bundle without `state` now fails loudly).
Spec: "given a `PortBundle` built without `state`, when it is constructed, then it raises `TypeError`, and `build_ports('fake')` still returns an `InMemoryState`."
Status: open

### R1-13 [MEDIUM] [tests] packages/contract-suites/tests/test_suites_against_fakes.py:23
Problem: Fake bindings are split across two packages: five fakes are bound both here and in `packages/chassis/tests/test_contracts.py:58-219`, six (state, events, auth, guardrail, evaluator, feedback) are bound only in chassis, and `chassis_contracts/__init__.py:15` does not export `StatePortContract` or `EventPortContract`.
Why it matters: `packages/contract-suites/CLAUDE.md` says "do not copy cases" and claims the self-tests run the suites against the fakes; both are half true, and copies drift.
Fix: Bind each fake in exactly one place (the chassis tests), delete the copies here, export every port suite from `__init__`, and correct the CLAUDE.md sentence.
Keeps behavior: yes.
Spec: "given any fake, when the suites are collected, then it is bound in exactly one file, and `from chassis_contracts import StatePortContract` succeeds."
Status: open

### R1-14 [MEDIUM] [tests] pocs/poc-01-walking-skeleton/tests/test_model_adapter.py:40
Problem: The test loads `packages/chassis/tests/test_contracts.py` by file path with `importlib` and `parents[3]` (lines 26-31), outside pytest collection, and only asserts `issubclass`.
Why it matters: A move or rename fails with an opaque error, and a subclass check proves the declaration, not that the suite ran; README line 95 already cites the real binding.
Fix: Delete this test and cite `packages/chassis/tests/test_contracts.py::TestLiteLLMModel` alone on README line 95.
Keeps behavior: yes.
Spec: "given the LiteLLM adapter over the fake server, when `ModelPortContract` runs against it, then every case passes."
Status: open

### R1-15 [MEDIUM] [tests] pocs/poc-01-walking-skeleton/tests/test_serve.py:38
Problem: PoC-1 has no harness: the `workload.transport` fixture is copied at `test_serve.py:38` and `test_a2a_inprocess.py:63` (set by hand, reset to `None` instead of the prior value), `_frames` is copied at `test_serve.py:92` and `test_a2a_inprocess.py:71`, and `_sse_frames` sits at `test_compose.py:209`.
Why it matters: Copies of the SSE parser drift, and the hand-rolled teardown leaks state if another test sets the hook.
Fix: Add one `conftest.py` with a `monkeypatch`-based transport fixture and one `sse_frames` helper.
Keeps behavior: yes.
Spec: "given the workload routed to the proxy app, when a test ends, then the workload's transport is the value it had before."
Status: open

### R1-16 [MEDIUM] [tests] pocs/poc-01-walking-skeleton/tests/test_compose.py:223
Problem: `test_compose_up_serves_one_request_and_counts_tokens` (52 lines) covers three exit criteria plus two leak checks in one body, and it and `test_serve.py:136` (53 lines) each hand-roll a sleep-based poll.
Why it matters: A failure does not say which criterion broke, and sleep polling is a flake risk.
Fix: Split into one test per criterion over a module-scoped stack fixture with one `wait_until_ready` helper, and update README lines 97 and 100.
Keeps behavior: yes.
Spec: "given the Compose stack is up, when one request is sent, then the router logs `total_tokens` tagged `agent:echo`."
Status: open

### R1-17 [MEDIUM] [tests] pocs/poc-01-walking-skeleton/tests/test_compose.py:136
Problem: About 12 tests wrap asserts in `for` or `if` (worst: `test_keys_stay_in_env_and_reach_one_service_each` branches on service name inside two loops), and others test several behaviors at once (`packages/chassis/tests/test_scripted_model.py:21` has 8 `raises` blocks).
Why it matters: The first failure hides the rest, and a branch can skip an assert silently.
Fix: Use `parametrize` with ids over (file, service), bad tokens, and refused message shapes, one behavior per test.
Keeps behavior: yes.
Spec: "given the chassis service in each Compose file, when its environment is read, then it holds `LITELLM_API_KEY` and no master or provider key."
Status: open

### R1-18 [MEDIUM] [tests] packages/chassis/tests/test_inbound_contract.py:545
Problem: `anthropic_read_stream` is a 67-line state machine with asserts inside the test file, and `:655` (65 lines) and `:280` (57 lines) are also over 50, with no tests of their own.
Why it matters: A bug in a reader can mask a bug in an adapter, and nobody can tell which side broke.
Fix: Move the readers into a harness module in `chassis_contracts` with a few direct tests for good and bad streams.
Keeps behavior: yes.
Spec: "given a stream with a delta for a block that is not open, when the reader reads it, then it fails naming the block."
Status: open

### R1-19 [MEDIUM] [simple] packages/fake-model-server/src/fake_model_server/app.py:79
Problem: `create_app` is 76 lines and its nested `chat` handler is 56; usage, the body, the chunk builder, and the stream generator are inline closures.
Why it matters: Both break the 50-line rule, and the pieces cannot be tested alone.
Fix: Lift `_usage`, `_completion_body`, and `_chunk` to module level so `chat` only routes.
Keeps behavior: yes.
Spec: "given a scripted rule with a tool call, when `/v1/chat/completions` is called with `stream` true and false, then the bodies are byte-identical to today's."
Status: open

### R1-20 [MEDIUM] [maintainable] [shared] packages/chassis/tests/test_contracts_poc07.py:1
Problem: Two chassis test files carry a PoC number (`test_contracts_poc07.py`, `test_poc04_seams.py`), and `pyproject.toml` describes the `stack` and `kind` markers as PoC-4 and PoC-5 drills.
Why it matters: The rubric bans PoC numbers in names, and these tests outlive their PoC.
Fix: Rename to `test_contracts_cross_cutting_ports.py` and `test_state_and_event_seams.py`, and reword the marker descriptions (env var names may stay).
Keeps behavior: yes.
Spec: "given the renamed files, when `pytest packages/chassis` runs, then the same tests are collected and no file under `packages/` matches `poc[0-9]+`."
Status: open

### R1-21 [MEDIUM] [docs] pocs/poc-01-walking-skeleton/README.md:5
Problem: The README has no `Builds on:` line and no `Hands to:` line, and neither does the PoC README template under `docs/templates`.
Why it matters: A reader cannot see where PoC-1 sits in the chain without reading "What it is not yet".
Fix: Add `Builds on: PoC-0 (scaffold and harness)` and `Hands to: PoC-2 (sidecar lane, proxy listener, traceparent)` under line 5, and add both lines to the template.
Keeps behavior: yes.
Spec: "given a reader of the PoC-1 README, when they read the header block, then they see what it builds on and what it hands to."
Status: open

### R1-22 [MEDIUM] [docs] docs/guides/chassis-reference.md:13
Problem: The history list jumps from PoC-4 to PoC-7 with no entry for `server/remote_auth.py`, `adapters/mcp/gateway.py`, `spec.trust`, or the plain-A2A mode, while `packages/chassis/CLAUDE.md:51-53` describes those modules in a 100-word bullet under "## Test".
Why it matters: CLAUDE.md calls the reference "the full test map", yet the PoC-5 and PoC-6 detail lives only in a brief that should hold rules, and two module lists will drift.
Fix: Add PoC-5 and PoC-6 sections to the reference and cut CLAUDE.md lines 51-53 to one line per PoC naming its scenario folder and env flag.
Keeps behavior: yes.
Spec: "given an engineer changing `remote_auth.py`, when they open chassis-reference, then they find its section and tests."
Status: open

### R1-23 [MEDIUM] [docs] docs/guides/chassis-reference.md:51
Problem: The guide says `ToolError` carries `unknown_tool` or `bad_arguments`, but `ports/tool.py:41-46` also defines `idempotency_key_required`, `tool_denied`, and `tool_unavailable`.
Why it matters: An adapter written from the guide would not raise the codes the gateway and write mode depend on, including the only retryable one.
Fix: List all five codes and mark `tool_unavailable` as retryable, or link to the `ToolError` docstring.
Keeps behavior: yes.
Spec: "given an engineer writing a ToolPort adapter, when they read the reference, then they see every ToolError code."
Status: open

### R1-24 [MEDIUM] [docs] packages/contract-suites/README.md:5
Problem: The README lists 9 suites and omits `auth`, `guardrail`, `evaluator`, `feedback`, `lane`, and `redteam`, with no run command; `packages/fake-model-server/README.md:3` opens with a code block and never says what the server is or why it exists.
Why it matters: Both fail the first-three-sentences rule, and the suite list undercounts by six.
Fix: Open each README with what it is, why it exists, and how to run it, and replace the suite list with a link to the folder.
Keeps behavior: yes.
Spec: "given a new engineer, when they read the first three sentences, then they know what it is, why it exists, and how to run it."
Status: open

### R1-25 [MEDIUM] [docs] docs/contracts/contract-v0.md:3
Problem: The superseded banner says "Read v1 for the contract in force", but v1 is itself superseded, and line 5 still says "build PoC-2 code to them".
Why it matters: The banner sends readers one hop into another stale record.
Fix: Point the banner at the one line that names the current contract (see R1-03) and mark line 5 as history.
Keeps behavior: yes.
Spec: "given a reader landing on contract-v0, when they read the banner, then they reach the contract in force in one click."
Status: open

### R1-26 [LOW] [secure] packages/chassis/src/chassis/core/events.py:77
Problem: `parse_event` calls `data.get(...)` before any type check, so a non-dict raises `AttributeError`, which `server/idempotency.py:534` does not catch.
Why it matters: A corrupt cache entry whose `events` item is a string becomes a 500 instead of a cache miss.
Fix: Start with `if not isinstance(data, dict): raise TypeError(...)` and truncate the `{version!r}` in the `UnsupportedSchemaVersion` text.
Keeps behavior: yes.
Spec: "given a cached entry whose events list holds the string 'x', when the idempotency store reads it, then it is treated as corrupt and re-run, with no 500."
Status: open

### R1-27 [LOW] [secure] packages/contract-suites/src/chassis_contracts/recording.py:94
Problem: `default_vcr_config` scrubs headers but not query parameters or body fields, and `CassetteMiss` prints the URL and up to 2000 body characters.
Why it matters: A provider that takes `?key=` or an `api_key` body field would put the key in a committed cassette on `--record-mode=rewrite`; today's cassette is clean.
Fix: Add `filter_query_parameters` and a body scrub in `before_record_request`, and fail the save if the cassette matches a key-shaped pattern.
Keeps behavior: yes.
Spec: "given a recorded request to `https://x/v1?key=sk-abcdefghijklmnop`, when the cassette is saved, then the file contains no `sk-` string."
Status: open

### R1-28 [LOW] [simple] packages/chassis/src/chassis/profiles.py:176
Problem: `lazy(..., poc="PoC-4")` on six `REGISTRY` entries produces "adapter X arrives in PoC-N", but every target module now exists, so the error fires only for a missing SDK and misleads.
Why it matters: It is a PoC-numbered option that outlived its purpose.
Fix: Drop the `poc` argument, say "adapter X is unavailable (no module Y)", and rename `_POC07_OFF` (line 110).
Keeps behavior: no (error text changes; `test_profiles.py:222`, `pocs/poc-05-sandboxed/tests/test_poc05_trust_config.py:274`, and `pocs/poc-01-walking-skeleton/tests/test_day0.py:103` match it).
Spec: "given a lazy factory whose module is missing, when called, then it raises `AdapterNotAvailable` naming the port and adapter and no PoC number."
Status: proposal

### R1-29 [LOW] [maintainable] packages/chassis/src/chassis/profiles.py:39
Problem: Lines 39-40 import `chassis.adapters.a2a` and `chassis.adapters.litellm` eagerly, while the docstring (line 16) says profiles reach adapters through `lazy`.
Why it matters: Importing `profiles` (and so `server.config` and `schemas`) loads a2a-sdk, and the import-linter contract passes only because indirect imports are allowed.
Fix: Make `inprocess`, `sidecar`, and `litellm` `lazy` entries like `remote`, or state in the docstring that these two are eager by design.
Keeps behavior: yes.
Spec: "given a fresh interpreter, when `chassis.profiles` is imported, then `a2a` is not in `sys.modules` until a lane is built."
Status: open

### R1-30 [LOW] [docs] pocs/poc-01-walking-skeleton/README.md:126
Problem: The notes index says in the present tense that the model proxy sits on the public port, while line 55 of the same README says PoC-2 paid that debt; the same file also has 9 sentences over 20 words (worst: line 121, 49 words).
Why it matters: The README contradicts itself on whether the debt is open, and long sentences break the house style.
Fix: Rewrite the bullet as "recorded; paid in PoC-2" and split line 121 into one sentence per demo step.
Keeps behavior: yes.
Spec: "given a reader of the notes index, when they read a debt bullet, then it says whether the debt is open or paid."
Status: open

## Left out (below the cap)

Noted by the lenses, not ranked: `redact_value` skips dict keys and breaks on NamedTuples (`redaction.py:43`); an all-zero `trace_id` yields an invalid `traceparent` (`core/trace.py:36`); `Refused` puts the body in `str(exc)` (`core/inbound.py:118`); the fake model server does not type-check its JSON body (`fake_model_server/app.py:98`); the rule matcher is written twice (`fakes/model.py:59`, `fake_model_server/script.py:86`); ten ports are listed in parallel in `profiles.py` with ten `type: ignore` lines (`profiles.py:351`); `EngineConnectorContract.test_cancel_is_clean` asserts nothing (`chassis_contracts/engine.py:140`); test names, one-line docstrings, and blank-line AAA splits are missing across `packages/chassis/tests` (`test_collector.py:25`); `test_day0.py:52` cites an ADR item as an exit criterion; the proxy port `127.0.0.1:8090` is stated eight times across the PoC-1 docs (`pocs/poc-01-walking-skeleton/CLAUDE.md:15`).

## Checked and clean

- Layering: `core`, `ports`, and `fakes` import no product SDK, web framework, or network library, and nothing from `server/` or `adapters/`.
- No `sys.path` insert in PoC-1, chassis, contract-suites, or fake-model-server tests.
- All 16 test names the PoC-1 README cites as evidence exist.
- Secret grep (`sk-`, `Bearer`, `api_key`, `password`, `AKIA`, `ghp_`, `BEGIN`) over the area finds only marked test values; the committed cassette is clean.
- The fake-model-server image pins its base and uv by digest and runs as non-root.
