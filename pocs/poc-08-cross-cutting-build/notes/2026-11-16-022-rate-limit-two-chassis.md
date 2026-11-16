# One rate-limit bucket across two chassis on one Valkey (2026-11-16)

## Why

022 H-6 (`docs/planning/issues/022-H-6-security-middleware.md`), the box: "A caller over its rate
limit gets 429, and the limit holds across two pods. Open until a stack test runs two chassis
processes on one Valkey and shows one bucket." The offline half is
`packages/chassis/tests/test_rate_limit.py::test_two_replicas_share_the_limit_through_the_store`
(two apps on one `InMemoryState`). This run is the half with real Valkey and two processes.

## What changed

- `deploy/compose/poc08/chassis/ratelimit.yaml`: agent `echo-ratelimit`, auth `static`, state
  `valkey`, rate limit on, `requests_per_minute: 1`, `request_burst: 6` (suggested), `fail_closed`.
  Its own agent name, so the stack drill's burst never spends this bucket.
- `deploy/compose/poc08/docker-compose.yaml`: two services, `chassis-rl-a` (127.0.0.1:18091) and
  `chassis-rl-b` (127.0.0.1:18092), on that config, one env anchor (one `VALKEY_URL`, one caller
  map with two callers: `poc08-ratelimit` and `poc08-ratelimit-other`). Not scraped.
- `deploy/compose/poc08/stack.sh`: makes `POC08_OTHER_TOKEN`; `make_env` keeps an existing env
  file and appends any key it lacks; `wait` polls both new chassis.
- Why a Compose change: no two existing chassis share a bucket key
  (`ratelimit:<agent>:<subject hash>`). `chassis` and `chassis-stages` differ in agent and
  subject; `chassis-plain` has the limit off. `chassis` itself refills a token a second and its
  bucket is spent by the drill's burst test.

## Setup

- Run: 2026-11-16T15:21Z (log stamps 15:21:21Z before, 15:21:51Z after).
- Commit: HEAD `71db2e8` (71db2e88b255041aa40791463d40ffdf19f36d47), dirty: the change was
  uncommitted at run time. HEAD and `git status --short` were the same before and after the run.
- The checksums below are of the uncommitted files at run time only: the five changed tracked
  files and the five new files. Every other file is as at `71db2e8`. The same values were
  recorded before and after the run.
  - `git diff HEAD | shasum -a 256` (the five changed tracked files):
    `c69ecb89902b5e520a3a102b2995ef2268be114df55d7d27c2b4283515523882`.
  - The changed tracked files (`shasum -a 256`):
    - `.github/workflows/poc08-stack.yml` `714c01ba695856a27788e9e012509fa49cf42b1c954ad4c635a6db438aaa8c25` (comment only)
    - `deploy/compose/poc08/docker-compose.yaml` `f2f40278a13892786dcbccadab16ad1e7886926d6f9c64b301eba20d8293f86c`
    - `deploy/compose/poc08/stack.sh` `c2d14258e58b30bb4dbb6c8c480905eb5da7d6cc66944e666be9d673124688bf`
    - `pocs/poc-08-cross-cutting-build/tests/conftest.py` `faaffed323774c3e9a4b5d6019ab2c1293fc8df24c2b1a739544684d59b81e49`
    - `pocs/poc-08-cross-cutting-build/tests/test_poc08_c1_ci.py` `ae7d8113addd7c7764c6ff47d50d5dbaa9cd345b33f71ae7be90030b8932995e`
  - The new files (`shasum -a 256`):
    - `deploy/compose/poc08/chassis/ratelimit.yaml` `c006dce8199ac24a325b679a019469e792aa651914232ecb9b657aa59c3fe72d`
    - `pocs/poc-08-cross-cutting-build/tests/poc08_ratelimit.py` `a02ae88b2d4a7a67ec39db77efae33f967a6aeee918dd94808d852101990c89d`
    - `pocs/poc-08-cross-cutting-build/tests/test_poc08_ratelimit_static.py` `0881dcf45bdc947cec27910a0bd700714fb2d1df4d8adca091a61220b8c49b20`
    - `pocs/poc-08-cross-cutting-build/tests/test_poc08_stack_ratelimit.py` `014bd85312a874eab277e45c0bea194abeca04f412e0322af63b00d1fd28a38b`
    - This note, as its skeleton at run time:
      `c0554477afa1f7b00481687aa431e0f1c70a5868118f3da1a3fd438eb766a0e7`. It was filled after the
      run, so its committed checksum differs.
- Machine: macOS (darwin), Python 3.12.13, pytest 9.1.1, run with uv. The Docker version and the
  VM memory are not in the log.
- Compose project `poc08` (`deploy/compose/poc08`), fresh: `stack.sh down` before `up` (exit 0).
  Valkey `valkey/valkey:8.1.4-alpine` (pinned by digest in the Compose file). `up` built both of
  our images from the worktree, the chassis included:
  ```
   Image agent-platform/chassis:poc08 Built
   Image agent-platform/fake-model-server:poc08 Built
  ```
  All nine containers reached `Healthy`; `wait` printed `ready:` for 18088, 18089, 18090, 18091,
  18092, Prometheus, and Grafana.
- Two chassis processes, `chassis-rl-a` and `chassis-rl-b`, config `chassis/ratelimit.yaml`.
- The drill plan (suggested): the caller spends 4 runs on A and 2 on B (the burst of 6), then one
  more on B and one on A; the other caller runs once on each. The window is under 30 s, so the
  refill (one token a minute) adds under one token.

## Command

```
deploy/compose/poc08/stack.sh down
deploy/compose/poc08/stack.sh up && deploy/compose/poc08/stack.sh wait
export "$(deploy/compose/poc08/stack.sh env)" && COMPOSE_STACK=1 uv run pytest -m network -v -s -p no:randomly pocs/poc-08-cross-cutting-build/tests/test_poc08_stack_ratelimit.py
deploy/compose/poc08/stack.sh down
```

## Output

Exit code 0 (`test_rc=0 up_rc=0`). Trimmed: the pytest header (platform and plugin lines), and
the build and up lines quoted under Setup.

```
collecting ... collected 3 items

pocs/poc-08-cross-cutting-build/tests/test_poc08_stack_ratelimit.py::test_the_caller_spends_one_bucket_across_two_chassis on A [200, 200, 200, 200], on B [200, 200], next on B 429 (Retry-After 60), next on A 429, other caller A 200 B 200, 2 bucket keys, caller tokens left 0.007345, 0.55 s
PASSED
pocs/poc-08-cross-cutting-build/tests/test_poc08_stack_ratelimit.py::test_another_caller_still_runs_on_both_chassis PASSED
pocs/poc-08-cross-cutting-build/tests/test_poc08_stack_ratelimit.py::test_valkey_holds_one_bucket_per_caller_not_one_per_chassis PASSED

============================== 3 passed in 5.05s ===============================
test exit 0
```

`stack.sh down` stopped and removed all nine containers (`poc08-grafana-1`, `poc08-prometheus-1`,
`poc08-chassis-1`, `poc08-chassis-stages-1`, `poc08-chassis-plain-1`, `poc08-chassis-rl-a-1`,
`poc08-chassis-rl-b-1`, `poc08-fake-model-server-1`, `poc08-valkey-1`) and the network `poc08`,
`down exit 0`. `docker ps` after `down` listed nothing. `grep -c sk-` on the run's log: 0.

## Reading

The box passes. The caller made 4 runs on A and 2 on B, all 200. The next run on B and the next on
A were 429 `rate_limited` with `Retry-After 60` (one token a minute). The other caller then got 200
on both. Valkey held 2 bucket keys under `ratelimit:echo-ratelimit:`, one per caller, and the
caller's held 0.007345 tokens. The calls took 0.55 s, so the refill was about 0.009 tokens.

## Controls

- The paired control for the 429 on B: B had served only 2 of the caller's runs, below the burst
  of 6. A bucket per process would answer 200 there; only a bucket shared with A answers 429.
- The 429 is not a dead chassis or store: the other caller got 200 on both chassis right after,
  and with `fail_closed` every 200 proves Valkey answered the bucket check.
- One bucket, not one per chassis: the key count under `ratelimit:echo-ratelimit:` is 2 (two
  callers), and each caller called both chassis.
