# packages/fake-langfuse

A fake of the Langfuse public API for offline tests (PoC-7): scores and OTLP traces. Design: `docs/plans/2026-10-28-poc-07-cross-cutting.md`. Routes: `README.md`.

## Rules

- Never imports `chassis` or `chassis_contracts`; `make lint` (import-linter) enforces it.
- Only what a PoC-7 test needs. A new route copies its shape from the `langfuse` SDK's generated API client and names the file it came from.
- No secret anywhere: tests pass made-up keys such as `pk-lf-test`.
- Tests start with the package name (`test_fake_langfuse_*`), run in process over an ASGI transport, and open no socket.
