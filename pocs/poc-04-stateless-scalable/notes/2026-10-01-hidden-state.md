# PoC-4 hidden state, 2026-10-01

Exit criterion 8: "engines that keep hidden state are listed, with a way to move the state out or a note to reject them."

Sources: the docstring and tests in `tests/test_hidden_state.py`, and the workload sources under `packages/workloads/` and `packages/workload-a2a/`. The check is the 057 H-19 kept-data check with the fake model: run A carries a marker and goes to replica 0; run B then goes to replica 0 (the reused workload) and to replica 1 (a fresh one). Nothing from A may show in B's answer or in what B sends the model, and no module global of a workload package may change.

No run of `test_hidden_state.py` is quoted in this note. The pass or fail evidence goes into `README.md` at close, with the command and its output.

## Engines

| Engine | What it could keep | What the PoC workload does | Pinned by | Way out or note |
| ------ | ------------------ | -------------------------- | --------- | --------------- |
| All Python engines (`workload_a2a`) | The A2A SDK's `InMemoryTaskStore`: every task and its events, saved before they reach a subscriber. | `PruningRequestHandler` deletes a task from the store once it is terminal (`packages/workload-a2a/src/workload_a2a/server.py`). The test waits `PRUNE_WAIT_S` (3 s), because the prune runs in a task of its own just after the answer. | `test_run_b_sees_nothing_of_run_a_on_a_reused_workload[<engine>]`: no task is left after the runs. | Keep the prune. A task that never ends (a hung run) stays until the process restarts; the chassis's `budget.timeout_ms` cancels it, which makes it terminal. Nothing a retry needs lives here: a retry goes through the chassis's idempotency cache in `StatePort`. |
| echo-python | Module global `echo_python.tools.list_failures`: a process-wide counter of failed MCP tool listings. | Counts and logs; holds no request data; moves only when the tool endpoint is unreachable. | `test_run_b_sees_nothing_of_run_a_on_a_reused_workload[echo-python]`: no module global of a workload package changes over A and B. | Move out: make it a telemetry counter. The workload has no telemetry port today, so the log line is the record. suggested: replace the global with the log line only, or report it through the chassis when workload metrics arrive. Harmless meanwhile: it never changes an answer. |
| echo-langgraph | LangGraph keeps a run's messages only with a checkpointer or a store on the compiled graph. Also the module global `echo_langgraph.tools.list_failures`, as in echo-python. | Compiles its graph per call with neither (`echo_langgraph/handle.py`: `graph.compile()`). The counter is as above. | `test_langgraph_compiles_its_graph_without_a_checkpointer_or_store`, plus the reused-workload case for the globals. | Move out: an agent that needs memory across calls passes a checkpointer backed by `StatePort` behind the chassis, never `MemorySaver` or a local SQLite file. Reject: a workload that compiles with an in-process checkpointer or store, since a retry on another replica would not see it. |
| echo-pydanticai | PydanticAI keeps a conversation only when the caller passes `message_history`, or when an `Agent` is reused with state on it. | Builds its `Agent` per call and passes no history (`echo_pydanticai/handle.py`). | `test_pydanticai_builds_its_agent_per_call_and_passes_no_history`, plus the reused-workload case. | Move out: history comes in with the request (the input or `context_ref`) and is stored behind the chassis in `StatePort`. Reject: a workload that keeps `message_history` in a module global or on a long-lived `Agent`. |
| echo-typescript | Its own A2A server's `InMemoryTaskStore` (`src/a2a_server.ts`), and any module-level `let`. | One Node process per workload. `PruningTaskStore` (`src/a2a_server.ts`) forgets a task `PRUNE_DELAY_MS` (3 s, suggested) after it is saved in a final state; `test/prune.test.ts` checks it. Its `running` map is cleared per task. No module-level request data. | `test_run_b_sees_nothing_of_run_a_on_a_reused_workload[echo-typescript]`: behavior only (skips when `node_modules` is absent). Module globals are not inspected. | Closed: a finished task is pruned after 3 s. A task that never ends stays until the chassis's `budget.timeout_ms` cancels it, which makes it final. Recheck on an `@a2a-js/sdk` bump: the prune reaches the SDK's internal bucket. |

Files: nothing is written into a workload's package folder (the test compares file mtimes). The real check of "nothing outside `/tmp`" is the read-only root in Compose and kind; `notes/2026-10-01-drills.md`, section 5, shows a write to `/` failing in all eight containers.

## The chassis

| What | Where | Why it is not hidden state |
| ---- | ----- | -------------------------- |
| In-flight runs and their budgets | `RunRegistry` in `server/correlation.py`, `app.state.runs` | Lives only while the run is open. A killed replica loses its runs; the idempotency lease lets a retry take over (`test_killed_replica.py`). |
| The idempotency cache with `state: memory` | `InMemoryState`, one per process | Correct for one replica and for tests only. `configs/local.yaml` and `sidecar.yaml` use it; the scale stack and the `local` and `cloud` profiles default to `valkey`. `cloud` refuses `memory`. |
| The active config | `app.state.config` | A copy of the store document. Every replica that reads the same bytes reports the same `versions.config`. |
| The readiness result | `ReadinessMonitor` | A cache of this pair's workload probe; it describes this replica only. |
| Pending result events | `ResultPublisher` | Background publishes, awaited up to 5 s at shutdown (suggested); the rest are counted as `chassis.events.publish_abandoned`. A kill loses them: delivery is at least once only after the broker takes the event. |
