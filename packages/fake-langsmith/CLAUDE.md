# packages/fake-langsmith

A fake of the LangSmith OTLP trace route (CH-10, issue 129) and feedback route (CH-11, issue 130) for offline tests. Routes: `README.md`.

## Rules

- Never imports `chassis` or `chassis_contracts`; `make lint` (import-linter) enforces it.
- Only what a CH-10 or CH-11 test needs: OTLP traces and the feedback route. Datasets (131 CH-12) are not here yet.
- No secret anywhere: tests pass made-up keys such as `lsv2-test-key`.
- Tests start with the package name (`test_fake_langsmith_*`), run in process over an ASGI transport, and open no socket.
