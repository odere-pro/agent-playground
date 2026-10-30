# Findings R0: scaffold and harness

Session: R0. Area: `Makefile`, `pyproject.toml`, `.github/workflows/`, `scripts/`, `.claude/`, root `CLAUDE.md`, `pocs/CLAUDE.md`, `pocs/README.md`, `docs/guides/local-dev.md`, `docs/guides/testing.md`, `docs/templates/`. PoC: none (PoC-0).
Rubric: [rubric.md](rubric.md).

## Summary

The core of the scaffold holds. Every action is pinned by SHA, and every workflow is read-only by default. `make check` runs the offline gate in CI, and harness-lint keeps the skill and agent index true. The top risk is the agent harness: the `.claude/settings.json` allow list lets `uv`, `make`, and `cat` get around the deny list. A second risk is that about 3,000 lines of shared test harness still live in PoC folders, chained by `sys.path`. The docs and skills still describe a single `pocs/CURRENT`, but four PoCs are open.

Method: one Explore map, then four lens agents (python-reviewer, platform-security, pr-test-analyzer, general-purpose docs). R0 merged and deduped their findings and re-checked each cited line with `sed -n`. Not run: the test suite, mypy, Docker, kind.

## Open items from the parallel-poc-work plan

From [2026-10-09-parallel-poc-work.md](../../plans/2026-10-09-parallel-poc-work.md). Its status is still `in progress`.

- WS1.1 Retire `pocs/CURRENT`: **open**. The file still exists (`poc-07-cross-cutting-decisions`). `harness_lint.py:98`, `session_start.sh:4`, and `poc-iteration/SKILL.md:10` all read it. The README `Status:` lines are free text, not `open|closed|planned`. See R0-03.
- WS1.2 `pocs/*/poc.mk` and `-include` in the Makefile: **open**. There is no `poc.mk` and no `-include`.
- WS1.3 Promote the harness to `packages/poc-harness`: **open**. The package does not exist. There are 24 `sys.path` lines in 11 files under `pocs/`, and the per-PoC mypy excludes are still in `pyproject.toml:100-105`. The PoC-named gates (`POC04_KIND`, `POC05_KIND`, `POC06_KIND`) are still in place. See R0-02, R0-13, and R0-14.
- WS1.4 Rename tests: **partly done**. Test basenames are unique, and `harness_lint.py:156-163` checks that. PoC-1 to PoC-4 test files still have no `test_pocNN_` prefix.
- WS1.5 Move PoC-4 kind to `deploy/kind/poc04/`: **open**. `deploy/kind/run.sh` (`CLUSTER=poc04`) and `deploy/kind/cluster.yaml` are still at the top level. `deploy/kind/poc04/` holds only kustomize overlays.
- WS1.6 Discover CI by glob: **open**. The images matrix is inline (`ci.yml:78-90`), and `deploy/images.yaml` does not exist. `ci.yml` has no path filters.
- WS2.1 Make `make setup` worktree-safe: **done** (`Makefile:15` uses `git rev-parse --git-common-dir`).
- WS2.2 Add `AO_NS`, `AO_PORT_BASE`, and `deploy/kind/lib.sh`: **open**. None of them exist.
- WS2.3 Add `scripts/infra_lock.sh`: **open**.
- WS2.4 Write the parallel model in `local-dev.md`: **partly done**. Line 25 states "one worktree at a time" as a convention, with no lock.
- WS2 fixed port 8090 in `test_server_cli.py`: **no action needed**. The test monkeypatches `create_server` and never binds a port.
- WS3 skills: `record-measurement` and `demo-record` are **done**. `worktree-parallel`, `kind-operate`, `poc-close`, and `cross-poc-change` are **open**.
- WS3 agents `infra-operator` and `poc-lead`: **open**.
- WS3 PreToolUse hook on kind, kubectl, and docker: **open**. There is no PreToolUse hook.
- WS3 SessionStart that prints the worktree, branch, and open PoCs: **open**.
- WS3 permissions for `git worktree`, `kind get`, and `kubectl --context`: **open**. None are in `settings.json`.
- WS3 `git-flow` fixes: **done**. The trailer is gone (`:8`), and there is a worktree section. The worktree path in that section is wrong, though (R0-22).
- WS3 root CLAUDE.md lists every skill and agent: **done**. It still says "`pocs/CURRENT` names the one in progress", not "PoCs with Status: open" (R0-03).
- WS3 shrink `packages/chassis/CLAUDE.md`: **done** (54 lines, 5.4 KB).
- WS3 trim `docs/planning/CLAUDE.md` and fix the stale lines in `pocs/poc-05-sandboxed/CLAUDE.md`: **not checked by R0**. They are in R5's area, and planning is out of scope.
- WS4 harness-lint checks:
  - **Done**: skill and agent index in both directions (`:124-139`), cited paths in CLAUDE.md files (`:148-153`), the 8 KB size cap (`:26`, `:146`), and unique test basenames (`:156-163`).
  - **Open**: "a closed PoC has no unchecked boxes and every ticked box links to evidence", and "`poc.mk` targets are prefixed".
- Planning link (an optional `poc` field in `backlog.py`): **open**.

## Findings

Most severe first. 30 findings: CRITICAL 0, HIGH 4, MEDIUM 23, LOW 3.

### R0-01 [HIGH] [secure] .claude/settings.json:12
Problem: The allow rules `Bash(uv *)`, `Bash(make *)`, `Bash(python3 scripts/*)`, `Bash(cat *)`, and `Bash(docker compose *)` get around the deny list. Examples: `uv run curl`, `uv pip install`, `uv run git push --force`, `make load-test ARGS='; curl …'` (`$(ARGS)` is unquoted at `Makefile:54`), and `cat deploy/compose/.env.poc04`.
Why it matters: The deny list is the only thing that enforces "no curl, no pip install, never force-push". `Read(.env*)` (`:40`) guards only the Read tool, so a `cat` or a `docker compose … config` can print the generated LiteLLM, Postgres, and Langfuse keys into a session.
Fix: Narrow the allow rules to the commands the harness uses, for example `uv sync*`, `uv run pytest *`, `uv run ruff *`, and `uv run mypy*`. Drop `cat *`. Add denies for `uv pip *`, `uv run curl*`, `uv run git push*`, `cat *.env*`, `docker compose * config*`, and `Read(**/.env*)`. Keeps behavior: no (more prompts).
Spec: "given the project settings, when the agent runs `uv run curl https://example.com` or `cat deploy/compose/.env.poc04`, then Claude Code asks or denies."
Status: proposal

### R0-02 [HIGH] [maintainable] [shared] pocs/poc-02-two-engines-one-contract/tests/poc02_harness.py:1
Problem: About 3,000 lines of shared test harness live in PoC test folders. Examples: `poc02_harness.py` (751 lines), `poc04_harness.py` (494), and `poc05_harness.py` (489). Later PoCs reach these files through `sys.path` inserts, 24 lines in 11 files (for example `poc03_harness.py:50`, `poc05_conftest.py:32`, and the 06a/06b/06c/07 `conftest.py`), plus 9 `noqa: E402`.
Why it matters: The rule "code that outlives a PoC goes to `packages/`" is broken, and a closed PoC's test folder is a hidden dependency of every later gate. Import order decides what resolves, and helper names have no visible origin.
Fix: Move the shared helpers (`route_outbound`, `running`, `a2a_on_unix_socket`, `uds_transport`, and the replica builders) into a workspace package, as WS1.3 planned. Import them normally and delete every `sys.path` insert. Keeps behavior: yes.
Spec: "given any PoC test folder collected alone, when pytest imports its harness, then it resolves from a package and `grep -rn sys.path pocs/` is empty."
Status: open

### R0-03 [HIGH] [docs] [shared] .claude/skills/poc-iteration/SKILL.md:10
Problem: The skill says "Set `CURRENT` to the folder you open", and `:24` says `CURRENT` moves on. But 06a, 06b, 06c, and 07 are all `Status: in progress`, and `pocs/CURRENT` holds only one name. The same claim appears in `CLAUDE.md:18`, `pocs/CLAUDE.md:9`, and `pocs/README.md:21,23`. `harness_lint.py:98` reads the file with no guard, so retiring it crashes the lint. `:129` does `splitlines()[0]` with no guard.
Why it matters: An agent that opens or closes a PoC overwrites the one value. The session hook then names one PoC and hides the other three, and the tool that finds wiring drift crashes on drift.
Fix: Let `pocs/CLAUDE.md` alone say what `CURRENT` means: the default PoC for a new session, with open PoCs read from `Status:`. Change the other copies to link there. Make `harness_lint.py` report a missing file or `Skills:` line as a finding, not a traceback. Keeps behavior: yes.
Spec: "given no `pocs/CURRENT`, when `make harness-lint` runs, then it prints one finding and exits 1, and `grep -rn 'Set \`CURRENT\`' .claude pocs` is empty."
Status: open

### R0-04 [HIGH] [tests] scripts/harness_lint.py:156
Problem: No gate checks that a README evidence line such as `tests/test_x.py::test_y` names a test that exists. `check_test_names` only rejects duplicate basenames.
Why it matters: The rubric says a renamed test updates every README line that cites it, and Wave 2 fixes will rename tests. About 300 citations across the PoC READMEs are checked by nobody. A scan finds no stale name today.
Fix: Add a harness-lint check that parses the `::test_*` and `Test*` names in `pocs/*/README.md` and fails when a name is not defined in `packages/` or `pocs/`, including the inherited suites in `chassis_contracts`. Keeps behavior: yes.
Spec: "given a README cites `::test_old_name`, when that test is renamed and the README is not, then `make harness-lint` fails and names the README line."
Status: open

### R0-05 [MEDIUM] [secure] packages/chassis/CLAUDE.md:46
Problem: The doc says "`uv run pytest packages/chassis` … Tests run offline with no keys", but `pyproject.toml:71-80` has no `addopts`. A bare `uv run pytest` runs with sockets on and keys present. The integration conftest skips only under `--disable-socket` (`tests/integration/conftest.py:24`).
Why it matters: Following the documented command runs the Docker and real-Langfuse integration tests with whatever keys are in the shell. That breaks the hard rule "tests run offline with no keys".
Fix: Change the doc line to `make test` or `scripts/check_offline.sh packages/chassis`. Moving `--disable-socket` into `addopts` would be a separate proposal. Keeps behavior: yes.
Spec: "given `LANGFUSE_SECRET_KEY` is set, when a developer runs the command `packages/chassis/CLAUDE.md` gives, then no integration test runs and no socket opens."
Status: open

### R0-06 [MEDIUM] [secure] scripts/check_offline.sh:6
Problem: The strip regex `(_API_KEY|_TOKEN|_SECRET|_PASSWORD)$` misses credential variables this repo uses. Examples: `LANGFUSE_SECRET_KEY`, `LANGFUSE_PUBLIC_KEY`, `LITELLM_MASTER_KEY`, `POC06_PROVIDER_KEY`, `POC06_LITELLM_KEY`, `CHASSIS_S3_SECRET_KEY`, and `AWS_SECRET_ACCESS_KEY`. `check_integration.sh:7-9` copies the same loop.
Why it matters: A real key in the developer's shell reaches the test process. Under `make test-integration`, sockets are open, so the key reaches a real service.
Fix: Put one strip loop in a sourced `scripts/lib/strip_secrets.sh`, and widen it to `_SECRET_KEY`, `_PUBLIC_KEY`, `_MASTER_KEY`, `_ACCESS_KEY`, `_PROVIDER_KEY`, `_VIRTUAL_KEY`, and `_LITELLM_KEY`. Keeps behavior: yes, if no offline test reads one of these inherited values (not yet proven).
Spec: "given `LANGFUSE_SECRET_KEY=x LITELLM_MASTER_KEY=y` are exported, when `make test` runs a test that reads `os.environ`, then neither is present."
Status: open

### R0-07 [MEDIUM] [secure] scripts/git-hooks/pre-commit:5
Problem: The pre-commit hook runs only `make quick`. `detect-secrets` runs only in `make lint-extra` (`Makefile:37`), which is in neither `quick` nor `check` and runs only as a CI job after the push.
Why it matters: A secret can reach a local commit and the remote before anything flags it, and the cleanup is a history rewrite plus a key rotation.
Fix: Run `detect-secrets-hook --baseline .secrets.baseline` on the staged files in the hook, before `make quick`. Keeps behavior: yes (it adds a check).
Spec: "given a staged file with `api_key = \"sk-…\"`, when `git commit` runs with the hook installed, then the commit is refused."
Status: open

### R0-08 [MEDIUM] [secure] [shared] .github/workflows/poc07-agent-scan.yml:37
Problem: `SNYK_TOKEN` goes to `deploy/agent-scan/scan.sh` on every push and every same-repo PR (`:18-20`). On those runs, the scan script, `mcp.json`, and the MCP servers it starts are branch-controlled code. `scan.sh:23` notes that a server could read the scanner's environment from `/proc`.
Why it matters: Anyone who can push a branch can change the scan script or a server and exfiltrate the token.
Fix: Pass the token only when `github.ref == 'refs/heads/main'` or on `workflow_dispatch`, or use a GitHub environment with a required reviewer. Keeps behavior: no (branch runs do only the inspect pass).
Spec: "given a push to a non-main branch, when poc07-agent-scan runs, then `SNYK_TOKEN` is empty and only the inspect run happens."
Status: proposal

### R0-09 [MEDIUM] [secure] .claude/settings.json:32
Problem: The push denies `git push --force*` and `git push * main*` miss `git push -f …`, `git push origin +HEAD:main` (a force push through `+`), and `git push origin HEAD:main` (no space before `main`).
Why it matters: The hard rule "never push to main, never force-push" depends on these patterns, and the short forms get past them.
Fix: Add denies for `git push *-f*`, `git push *+*`, and `git push *:main*`. Keeps behavior: yes (the deny list only gets tighter, and `git push` is not in the allow list).
Spec: "given the settings, when the agent runs `git push origin +HEAD:main`, then it is denied."
Status: open

### R0-10 [MEDIUM] [secure] [shared] .github/workflows/remote-lane.yml:40
Problem: `setup-uv` has no `version:` here or in `poc06-kind.yml:46`, `poc06-load.yml:54`, and `poc06-slm.yml:62`, so each run installs the latest uv. The same four workflows sync with `--frozen`, not `--locked`.
Why it matters: The tool that installs every hash-pinned dependency is itself unpinned. `--frozen` also skips the check that `uv.lock` matches `pyproject.toml`, which `ci.yml` does run.
Fix: Set `version:` to the uv version that `ci.yml:23` and the Dockerfiles use, and switch to `uv sync --all-packages --locked`. Keeps behavior: yes.
Spec: "given any workflow that uses setup-uv, when actionlint and a pin check parse it, then `with.version` is set and every `uv sync` passes `--locked`."
Status: open

### R0-11 [MEDIUM] [maintainable] [shared] Makefile:46
Problem: `make test-poc POC=06` expands to `pocs/poc-06-*/tests`, which matches nothing, because the folders are `poc-06a-…`, `poc-06b-…`, and `poc-06c-…`.
Why it matters: The `poc-iteration` skill says to run `make test-poc POC=NN` before ticking a box. For PoC-6, that command errors on a missing path.
Fix: Use `pocs/poc-$(POC)*/tests`, so that `POC=06` selects 6a, 6b, and 6c and `POC=06a` selects one of them. Keeps behavior: yes (the working cases do not change).
Spec: "given `POC=06`, when `make test-poc` runs, then it collects the 06a, 06b, and 06c tests."
Status: open

### R0-12 [MEDIUM] [maintainable] [shared] Makefile:79
Problem: `quick`, which is also the pre-commit hook, runs `fmt-check lint` and tests but never `type`. Only `check` runs mypy.
Why it matters: The root CLAUDE.md calls `make quick` the gate "before every commit", yet strict-mypy errors surface only in `make check` or CI.
Fix: Say in the `quick` help text and in `CLAUDE.md` that `quick` skips mypy. Adding `type` to `quick` is the alternative, and it slows the hook. Keeps behavior: yes for the doc fix.
Spec: "given `make help`, when it is read, then the `quick` line says it does not type-check."
Status: open

### R0-13 [MEDIUM] [maintainable] [shared] pocs/poc-06b-bake-off-remote-lane/tests/poc06b_conftest.py:26
Problem: The "skip a kind drill unless a gate variable is set" logic exists three times. `poc06b_conftest.py:26-42` copies `poc05_conftest.py:39-55` line for line, and `poc04_conftest.py:36-48` does the same job a third way. The `poc04_conftest.py` and `poc05_conftest.py` shims exist only so mypy can check them, and `pyproject.toml:100-105` excludes six PoC conftests by name.
Why it matters: Three copies of one gate will drift, and every new PoC adds a mypy exclude and a re-export shim.
Fix: Move one `pytest_collection_modifyitems` hook into `chassis_contracts/plugin.py`, driven by a marker, variable, and context table. Then delete the shims and the excludes. Keeps behavior: yes.
Spec: "given a test marked `kind` and no gate variable, when it is collected, then it is marked `network` and skipped with a reason that names the variable."
Status: open

### R0-14 [MEDIUM] [maintainable] [shared] pyproject.toml:79
Problem: Env gates carry PoC numbers: `POC04_STACK`, `POC04_KIND`, `POC05_KIND`, `POC06_KIND`, `POC07_STACK`, and `POC07_OFF` in `chassis/profiles.py`. The `kind` marker text describes a different variable per PoC and omits `POC06_KIND`.
Why it matters: The rubric says there is no PoC number in a pytest marker. The gates are per-PoC special cases that every reader must look up.
Fix: Name the gates after what they enable, for example `KIND_DRILLS=1` and `COMPOSE_STACK=1`, and keep one `kind` and one `stack` marker. Keeps behavior: no (the env names in CI, notes, and evidence commands change).
Spec: "given `kind` tests in any PoC, when `KIND_DRILLS=1` is set, then they run, and without it they skip."
Status: proposal

### R0-15 [MEDIUM] [simple] pocs/poc-02-two-engines-one-contract/tests/poc02_harness.py:570
Problem: `free_port` is copied 7 times, for example at `bakeoff/procs.py:22` and `test_serve.py:128`. The "start uvicorn in a thread and poll `server.started`" loop appears at `poc02_harness.py:485`, at `:545`, and again in `chassis_contracts.interface.serve_on_unix_sockets` (`:207`).
Why it matters: The rubric says there should be no second helper that does what one in `packages/` already does.
Fix: Keep one `free_port` and one threaded-uvicorn context manager in `chassis_contracts`, and delete the copies. Keeps behavior: yes.
Spec: "given any test that needs a loopback port or a threaded server, when it asks, then the same helper answers and `grep -rn 'bind((\"127.0.0.1\", 0))'` has one hit."
Status: open

### R0-16 [MEDIUM] [simple] pocs/poc-04-stateless-scalable/tests/poc04_harness.py:316
Problem: `replicas_on_unix_sockets` is 83 lines and nests about five levels deep. Other harness functions over 50 lines:
- `poc02_harness.py:316 patch_outbound` (62 lines)
- `:600 typescript_echo` (54)
- `poc05_harness.py:388 remote_lane` (55)
- `poc03_harness.py:164 chassis_on_unix_sockets` (52)
Why it matters: These functions break the 50-line and 4-level rules, and every later PoC harness builds on them.
Fix: Extract `_engine_for(target, lane, …)` and `_replica(i, …)`, and leave only the assembly in the loop. Do the same for the others when the harness moves (R0-02). Keeps behavior: yes.
Spec: "given target `echo-typescript` and lane `inprocess`, when replicas start, then `ValueError` names the TypeScript sidecar-only rule."
Status: open

### R0-17 [MEDIUM] [simple] scripts/poc06_mac.sh:1
Problem: The script is 820 lines, over the 800 limit. It mixes preflight, process lifecycle, note rendering (`write_note`, 62 lines), a `--push` git flow, and five steps. It has 18 inline `[[ "$DRY" == 1 ]]` branches that repeat each command as dry-run text. `:614` hand-types the bake-off command into the note instead of recording what `bakeoff_run` ran.
Why it matters: It is the largest file in `scripts/`. The recorded command, which is PoC evidence, can drift from the real one.
Fix: Split it into sourced pieces (`lib.sh`, `notes.sh`, `steps.sh`, `push.sh`) behind a short entry script. Build each argument list once and use it for both the run and the note. Keeps behavior: yes.
Spec: "given `--dry-run`, when it runs, then the output lists the same steps and commands as before the split."
Status: open

### R0-18 [MEDIUM] [tests] [shared] pyproject.toml:75
Problem: Pytest runs without `--strict-markers`. There is no `addopts`, and `check_offline.sh:12` does not pass the flag either.
Why it matters: A misspelled `network` marker silently runs an online test inside the offline gate instead of failing at collection.
Fix: Pass `--strict-markers` in `check_offline.sh` and `check_integration.sh`. Keeps behavior: yes (every marker in use is registered; verify with one collect run).
Spec: "given a test with an unregistered marker, when `make test` collects it, then collection fails."
Status: open

### R0-19 [MEDIUM] [tests] docs/guides/testing.md:1
Problem: The test-shape rules are stated nowhere an author reads: the `test_<subject>_<does what>_when_<condition>` name, the one-line docstring, and the arrange-act-assert layout. `testing.md` and `.claude/agents/tester.md` omit them. Also, `pocs/CLAUDE.md:6` asks for the exit criterion in the docstring, while scenario docstrings run 4 to 8 lines (`test_engines.py:48-52`, `test_poc05_netpol_static.py:665-672`).
Why it matters: Only 3 of 689 PoC tests use `when_`, and 851 of 982 chassis tests have no docstring. The one-line rule and the criterion-in-docstring rule collide.
Fix: Add the naming, docstring, and layout rules to `testing.md`, and link to them from `tester.md`. State that a scenario docstring's first line is the quoted criterion and that more lines are optional. Keeps behavior: yes.
Spec: "given `testing.md`, when a new author reads it, then it states the name shape, the docstring rule, and the scenario first-line rule."
Status: open

### R0-20 [MEDIUM] [tests] pocs/poc-05-sandboxed/tests/test_poc05_pids_static.py:1
Problem: PoC-5 scenario tests cite their exit criterion by number and a paraphrase. Examples: "exit criterion 8, H23" here, and "Exit criterion 5 (offline part)" at `test_poc05_netpol_static.py:665`. PoC-2 quotes the README text word for word (`test_engines.py:47-52`).
Why it matters: `pocs/CLAUDE.md:6` asks for the criterion itself. A bare number points at the wrong criterion if the README list is renumbered: criterion 8 is "The hostile suites pass in both lanes".
Fix: Quote the README criterion text on each scenario docstring's first line, and keep the H-number as a second reference. Keeps behavior: yes.
Spec: "given a PoC-5 scenario test, when its docstring is read, then it contains the README criterion text it covers."
Status: open

### R0-21 [MEDIUM] [tests] pocs/poc-05-sandboxed/tests/test_poc05_pids_static.py:62
Problem: `test_poc05_kubelet_sets_a_small_pod_pids_limit` loops over the patches, puts its asserts in a helper (`:55`), and has no docstring. `:44` has no docstring either. `pocs/poc-02-…/tests/test_engines.py:47` packs six asserts with no arrange-act-assert split.
Why it matters: A loop hides which node failed. Six claims in one body hide which part of the criterion broke.
Fix: Parametrize over the nodes. Split `test_engines` into one test per claim behind a module-scoped run fixture, and keep the original name for the criterion test. Keeps behavior: yes.
Spec: "given each kind node, when its KubeletConfiguration is read, then `podPidsLimit` is above the floor and at most the ceiling, in one test case per node."
Status: open

### R0-22 [MEDIUM] [docs] .claude/skills/git-flow/SKILL.md:20
Problem: The worktree steps use `../agent-orchestration.wt/<branch-with-dashes>`, but the repo is `agent-playground` and its worktrees live under `.claude/worktrees/`. The cleanup command at `:27` uses the same wrong path. `:24` says the "host ports are fixed", but the PoC-6 ports can now be overridden (`poc06_mac.sh:105`).
Why it matters: An agent that follows the skill word for word creates a stray folder in `~/Git`, where every subfolder must be a repo.
Fix: Use `.claude/worktrees/<name>`, and say which names and ports can be overridden today. Keeps behavior: yes.
Spec: "given the repo, when `grep -rn 'agent-orchestration' .claude docs/guides` runs, then it prints nothing."
Status: open

### R0-23 [MEDIUM] [docs] docs/guides/testing.md:39
Problem: The marker list is one 41-word sentence that omits `kind`. `:35` names only `POC04_STACK` and `POC04_KIND`, but the tests also use `POC05_KIND` (53 hits), `POC06_KIND` (8), and `POC07_STACK` (8).
Why it matters: A reader of the testing guide cannot learn how the PoC-5, PoC-6, or PoC-7 drills are gated.
Fix: Write one bullet per marker (`slow`, `network`, `stack`, `kind`, `contract`), and link to `pyproject.toml` and the PoC README for the gate names. Keeps behavior: yes.
Spec: "given `testing.md`, when `grep -c '\`kind\`'` runs, then the count is at least 1."
Status: open

### R0-24 [MEDIUM] [docs] [shared] CLAUDE.md:27
Problem: Several doc lines misstate the gates:
- Root `CLAUDE.md:27` and `local-dev.md:17` say only `*_API_KEY` is stripped. The script also strips `_TOKEN`, `_SECRET`, and `_PASSWORD`.
- `CLAUDE.md:23` calls `make check` "(what CI runs)" and then says `lint-extra` is not in `check`.
- `local-dev.md:16` says "everything CI runs".
- `local-dev.md:30` says the `local` and `cloud` adapters are for "later PoCs", but they exist now.
Why it matters: A reader who trusts the root file assumes a green `make check` covers the `lint-extra`, `typescript`, and `images` CI jobs. They may also assume a `*_TOKEN` variable reaches the tests.
Fix: Let `testing.md` "Offline is enforced" own the strip list, and link to it from the others. Say "the CI `check` job", list the other jobs once in `local-dev.md`, and drop "day 0". Keeps behavior: yes.
Spec: "given the docs, when `grep -rn 'everything CI runs\|(what CI runs)\|day 0' CLAUDE.md docs/guides` runs, then it prints nothing."
Status: open

### R0-25 [MEDIUM] [docs] docs/templates/poc-readme.md:3
Problem: The template has no `Builds on:` or `Hands to:` line, so no PoC README has one. It allows `Status: not started | in progress | done`, but real Status lines are 38 to 41 words long (`poc-07/README.md:3`, `poc-06a/README.md:3`).
Why it matters: The rubric asks for both lines. Without them, the PoC chain lives only in the planning docs, and no tool can parse a paragraph-long status.
Fix: Add `Builds on:` and `Hands to:` under `Planning doc:`. Keep `Status:` to one fixed value plus a date, and move the detail to a `## Waiting on` section. Keeps behavior: yes.
Spec: "given a PoC README, when `make harness-lint` runs, then it fails if `^Builds on:` or `^Hands to:` is missing, or if the Status value is not in the template set."
Status: open

### R0-26 [MEDIUM] [docs] [shared] CLAUDE.md:19
Problem: The `deploy/` map bullet is 68 words. Its first sentence is 45 words, and it also covers `scripts/` and `docs/`. The Commands line (`:23`) is a 94-word run-on of 19 targets that repeats the `## help` strings in the Makefile. The opening (`:3`) packs three ideas, and it restates the read-only epic rule from `:32`.
Why it matters: Every agent reads this file first, and these lines break the house rule of short sentences with one idea each. New PoCs keep adding clauses to them.
Fix: Split the map line into `deploy/`, `scripts/`, and `docs/` bullets, and link to a guides index. Keep five daily commands and send the rest to `make help`. State the epic rule once. Keeps behavior: yes.
Spec: "given root CLAUDE.md, when a sentence-length check runs, then no line is over about 25 words."
Status: open

### R0-27 [MEDIUM] [docs] pocs/README.md:19
Problem: The open, run, and close procedure appears in four places: `pocs/README.md:19-23`, `pocs/CLAUDE.md:5-8`, `poc-iteration/SKILL.md`, and `docs-editor.md:18`. The rule "one worktree on Docker at a time" also appears in four: `local-dev.md:25`, `git-flow/SKILL.md:24`, `demo-record/SKILL.md:39`, and `poc-05-operate/SKILL.md:15`.
Why it matters: R0-03 shows the cost: the `CURRENT` step went stale in every copy at once.
Fix: Let `poc-iteration` own the procedure and `git-flow` own the worktree rule. Each other file keeps one line that links to the owner. Keeps behavior: yes.
Spec: "given the docs, when `grep -rln 'test name or the command' pocs .claude` runs, then it matches one file."
Status: open

### R0-28 [LOW] [secure] [shared] .github/workflows/poc06-slm.yml:119
Problem: On the unpinned path (`LLAMA_CPP_TAG` empty), `[[ -z "$listed" || … ]]` accepts an asset with no listed digest. Separately, `remote-lane.yml:39`, `ci.yml:19`, and `poc07-evals.yml:35` run PR code without `persist-credentials: false`. `remote-lane.yml:42` also caches on `pull_request`, which `poc06-kind.yml:45,49` avoids.
Why it matters: The tag pin at `:52` makes the first path dormant, but emptying it in a "bump" commit would quietly turn verification off. The stored-token rule is applied unevenly across workflows.
Fix: Fail when `listed` is empty. Add `persist-credentials: false` to every checkout that runs PR code, and disable the cache on `pull_request`. Keeps behavior: yes.
Spec: "given `LLAMA_CPP_TAG` is empty and the asset has no digest, when the install step runs, then it exits 1 before extraction."
Status: open

### R0-29 [LOW] [secure] .claude/hooks/session_start.sh:4
Problem: The hook prints the raw contents of `pocs/CURRENT`, with no length or format check, plus up to 8 `git status` lines, into the session context.
Why it matters: A hostile branch can put text into the start of every session's context, which is a prompt-injection path.
Fix: Accept only `^poc-[0-9a-z-]{1,40}$` from the file and print `unknown` otherwise. Keeps behavior: yes.
Spec: "given `pocs/CURRENT` holds 2 KB of text, when the hook runs, then it prints `Current PoC: unknown`."
Status: open

### R0-30 [LOW] [simple] [shared] Makefile:65
Problem: The help text calls `bakeoff` and `poc06-mac` "(stub)" (`:65`, `:68`), but both are real. `POC04 :=` (`:51`) is defined in the middle of the file and used once (`:54`), and the `.PHONY` line (`:8`) is about 300 characters.
Why it matters: `make help` misleads readers, and the long `.PHONY` line conflicts on every new target.
Fix: Drop "(stub)", inline the path, and wrap `.PHONY`. Keeps behavior: yes.
Spec: "given `make -n load-test`, when it runs before and after the change, then the command is the same."
Status: open

## Below the cap

These were merged or dropped to stay at 30: `.claude/hooks/format_py.sh:8` hides `ruff format` errors, and `scripts/check_integration.sh:10-12` reaches only PoC-4 `network` tests. A note for R6: `deploy/kind/poc06/run.sh:52` sets `CLUSTER=poc05` on purpose, because PoC-6 reuses the PoC-5 cluster.
