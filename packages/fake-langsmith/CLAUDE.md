# packages/fake-langsmith

A fake of LangSmith for offline tests: the OTLP trace route (CH-10, issue 129) and the dataset, example, and experiment routes (CH-12, issue 131). Routes: `README.md`.

## Rules

- Never imports `chassis` or `chassis_contracts`; `make lint` (import-linter) enforces it.
- Only what a CH-10 or CH-12 test needs. Feedback (130 CH-11) is not here yet.
- No secret anywhere: tests pass made-up keys such as `lsv2-test-key`.
- Tests start with the package name (`test_fake_langsmith_*`), run in process over an ASGI transport, and open no socket.
