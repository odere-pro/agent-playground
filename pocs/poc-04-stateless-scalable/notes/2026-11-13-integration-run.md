# `make test-integration` with testcontainers (2026-11-13)

## Why

Backs the last open box of [015 H-8](../../../docs/planning/issues/015-H-8-testing-kit.md): "A real adapter and its fake pass the same port contract suite, one with testcontainers and one in memory." Port: `StatePort`. Suite: `chassis_contracts.state.StatePortContract`. Real adapter: `ValkeyState` against Valkey in a testcontainer, bound in `packages/chassis/tests/integration/test_valkey_state_contract.py::TestValkeyState`. Fake: `InMemoryState`, bound in `packages/chassis/tests/test_state_contract.py::TestInMemoryState`. The suites and the `network` scenario tests come from PoC-4, so the note sits here.

## Setup

- Commit `a6d03dc` (origin/main), dirty: the harness fix below in `pocs/poc-04-stateless-scalable/tests/test_config_minio.py`.
- Machine: macOS 27.0.1, Apple M1 Pro. Docker Desktop 29.9.3, 10 CPUs, VM memory 8319438848 bytes (7.75 GiB, `docker info --format '{{.MemTotal}}'`).
- Tools: uv 0.10.10, Python 3.12.13, pytest 9.1.1, testcontainers 4.15.0.
- No Compose project, no kind context: testcontainers starts each container on the default bridge network and Ryuk removes it. `COMPOSE_STACK` and `KIND_DRILLS` unset, so those drills skip. No `LANGFUSE_*` variables, so the live Langfuse binding skips.

Images started (pinned by digest in `packages/contract-suites/src/chassis_contracts/containers/`; local image IDs from `docker image inspect <repo>@<digest>` after the run):

| Image | Digest | Local image ID |
| ----- | ------ | -------------- |
| `valkey/valkey:8.1.4-alpine` | `sha256:e706d1213aaba6896c162bb6a3a9e1894e1a435f28f8f856d14fab2e10aa098b` | `sha256:b9cc2f089926` |
| `pgsty/minio:RELEASE.2026-08-04T00-00-00Z` | `sha256:b6bfe7239bfc83fb90d31612d9704d86039dd714f7904b3f1ad68f211e602372` | `sha256:919a89f4c86f` |
| `confluentinc/cp-kafka:7.6.0` | `sha256:24cdd3a7fa89d2bed150560ebea81ff1943badfa61e51d66bb541a6b0d7fb047` | `sha256:57debb9adf7a` |
| `daprio/daprd:1.16.0` | `sha256:e5c959c05d406c8dd5b0cb8132b3f1aacf6f81fbd64efc0adb430dd533d32c05` | `sha256:0a1166a030e3` |
| `testcontainers/ryuk:0.8.1` | `sha256:bf3f74a47dee0acda89aba4b2fc9c7fdcf994a084db02a2d06566f07baae022e` | `sha256:e812af3238e5` |

## First run: failed on a harness defect

`make test-integration ARGS="-v -rs"`, exit 2: `51 passed, 18 skipped, 246 deselected, 2 xfailed, 4 errors in 87.14s`. All 15 Valkey cases passed. The 4 errors were at teardown of the `running` fixture in `tests/test_config_minio.py`:

```
RuntimeError: Attempted to exit cancel scope in a different task than it was entered in
```

The fixture entered the chassis lifespan (FastMCP's session manager opens an anyio task group) in the fixture's setup task. pytest-asyncio 1.4 runs the teardown in another task. The fix uses the existing `poc_harness.apps.running`, which holds the lifespan in a task of its own for exactly this case. No test body, assertion, or product code changed.

## Command

```
make test-integration ARGS="-v -rs"
```

(`scripts/check_integration.sh -v -rs`: `pytest --strict-markers -m network --record-mode=none` on `packages/chassis/tests/integration` and `pocs/poc-04-stateless-scalable/tests`, sockets on, `*_API_KEY`, `*_TOKEN`, `*_SECRET`, `*_PASSWORD` stripped. `-v -rs` only adds per-test lines and skip reasons.)

## Output

Exit 0. The Valkey binding, verbatim:

```
packages/chassis/tests/integration/test_valkey_state_contract.py::TestValkeyState::test_get_of_a_missing_key_is_none PASSED [ 50%]
packages/chassis/tests/integration/test_valkey_state_contract.py::TestValkeyState::test_set_then_get_round_trips_any_bytes PASSED [ 52%]
packages/chassis/tests/integration/test_valkey_state_contract.py::TestValkeyState::test_set_overwrites PASSED [ 53%]
packages/chassis/tests/integration/test_valkey_state_contract.py::TestValkeyState::test_set_if_absent_claims_once_and_never_overwrites PASSED [ 54%]
packages/chassis/tests/integration/test_valkey_state_contract.py::TestValkeyState::test_concurrent_set_if_absent_has_one_winner PASSED [ 56%]
packages/chassis/tests/integration/test_valkey_state_contract.py::TestValkeyState::test_a_value_expires_after_its_ttl PASSED [ 57%]
packages/chassis/tests/integration/test_valkey_state_contract.py::TestValkeyState::test_an_expired_claim_can_be_claimed_again PASSED [ 59%]
packages/chassis/tests/integration/test_valkey_state_contract.py::TestValkeyState::test_compare_and_set_replaces_only_on_a_match PASSED [ 60%]
packages/chassis/tests/integration/test_valkey_state_contract.py::TestValkeyState::test_compare_and_set_on_a_missing_key_is_false PASSED [ 61%]
packages/chassis/tests/integration/test_valkey_state_contract.py::TestValkeyState::test_compare_and_set_with_none_deletes_on_a_match PASSED [ 63%]
packages/chassis/tests/integration/test_valkey_state_contract.py::TestValkeyState::test_compare_and_set_renews_the_ttl PASSED [ 64%]
packages/chassis/tests/integration/test_valkey_state_contract.py::TestValkeyState::test_delete_of_a_missing_key_is_fine PASSED [ 66%]
packages/chassis/tests/integration/test_valkey_state_contract.py::TestValkeyState::test_a_store_failure_is_state_unavailable PASSED [ 67%]
packages/chassis/tests/integration/test_valkey_state_contract.py::test_a_wrong_password_is_state_unavailable_and_not_echoed PASSED [ 69%]
packages/chassis/tests/integration/test_valkey_state_contract.py::test_spec_adapters_state_valkey_builds_a_working_port PASSED [ 70%]
```

The tail (the per-test lines of the other files trimmed; every one is PASSED, SKIPPED for a drill or Langfuse, or one of the two known Dapr strict XFAILs):

```
SKIPPED [1] packages/contract-suites/src/chassis_contracts/feedback.py:86: needs LANGFUSE_BASE_URL or LANGFUSE_HOST, LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY
SKIPPED [1] packages/contract-suites/src/chassis_contracts/feedback.py:98: needs LANGFUSE_BASE_URL or LANGFUSE_HOST, LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY
SKIPPED [1] pocs/poc-04-stateless-scalable/tests/test_compose_scale.py:73: a drill: set COMPOSE_STACK=1
SKIPPED [2] pocs/poc-04-stateless-scalable/tests/test_compose_scale.py: a drill: set COMPOSE_STACK=1
SKIPPED [1] pocs/poc-04-stateless-scalable/tests/test_compose_scale.py:146: a drill: set COMPOSE_STACK=1
SKIPPED [4] pocs/poc-04-stateless-scalable/tests/test_kind.py:69: a drill: set KIND_DRILLS=1
SKIPPED [4] pocs/poc-04-stateless-scalable/tests/test_kind.py:86: a drill: set KIND_DRILLS=1
===== 51 passed, 18 skipped, 246 deselected, 2 xfailed in 78.62s (0:01:18) =====
```

The in-memory half, the same commit, offline (sockets off):

```
$ scripts/check_offline.sh packages/chassis/tests/test_state_contract.py -q -k TestInMemoryState
.............                                                            [100%]
13 passed, 14 deselected in 0.49s
```

## Reading

The criterion passes. `StatePortContract` has 13 cases. `TestValkeyState` passed all 13 against a real Valkey in a testcontainer, and `TestInMemoryState` passed the same 13 in memory. The 2 xfails are the Dapr strict xfails recorded in the PoC-4 README. The 18 skips are drills and the live Langfuse binding, none of them in the state suite.

## Controls

Not a refusal, so no control run is needed. The Valkey cases ran against Docker and were not skipped: a test skips when Docker is not reachable, and each Valkey line above reads PASSED, not SKIPPED. `docker image inspect` also finds all five pinned images locally after the run.
