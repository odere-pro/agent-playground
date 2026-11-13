# fake-langsmith

A fake of the LangSmith OTLP trace route, for offline tests (CH-10). It runs in process: a test builds `create_app(FakeLangSmith(api_key))` and reaches it through an ASGI transport, so no port opens.

| Route | What it does |
| ----- | ------------ |
| `POST /otel/v1/traces` | OTLP over HTTP, protobuf or JSON. The spans are decoded into `FakeLangSmith.recorded`. A body that is not OTLP is 400. |
| `GET /fake/traces/{trace_id}` | The recorded spans of one trace (`trace_id`, `span_id`, `parent_span_id`, `name`, `attributes`), or 404. Not a LangSmith route. |
| `GET /health` | No auth. |

The OTLP route needs the `x-api-key` header; a missing or wrong key is 401. `FakeLangSmith.trace_ids()` and `FakeLangSmith.spans(trace_id)` read the record back in a test.
