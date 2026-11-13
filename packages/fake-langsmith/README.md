# fake-langsmith

A fake of the LangSmith OTLP trace route, for offline tests (CH-10). It runs in process: a test builds `create_app(FakeLangSmith(api_key))` and reaches it through an ASGI transport, so no port opens.

| Route | What it does |
| ----- | ------------ |
| `POST /otel/v1/traces` | OTLP over HTTP, protobuf or JSON. The spans are decoded into `FakeLangSmith.recorded`. A body that is not OTLP is 400. |
| `GET /fake/traces/{trace_id}` | The recorded spans of one trace (`trace_id`, `span_id`, `parent_span_id`, `name`, `attributes`), or 404. Not a LangSmith route. |
| `POST /api/v1/feedback` | One feedback record (130 CH-11), upserted by `id` into `FakeLangSmith.feedback`. No `id` or no UUID `run_id` is 422. `FakeLangSmith.unavailable` makes it 503. suggested: path and fields follow `LangSmithFeedback`. |
| `GET /fake/feedback/{id}` | One feedback record, or 404. Not a LangSmith route. |
| `GET /fake/runs/{run_id}/feedback` | `{"feedback": [...]}` on one run; the run id as a UUID or 32 hex. Not a LangSmith route. |
| `GET /health` | No auth. |

Both POST routes need the `x-api-key` header; a missing or wrong key is 401. `FakeLangSmith.trace_ids()`, `FakeLangSmith.spans(trace_id)`, and `FakeLangSmith.feedback_on(run_id)` read the record back in a test.
