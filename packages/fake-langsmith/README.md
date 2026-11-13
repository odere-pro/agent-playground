# fake-langsmith

A fake of the LangSmith OTLP trace route (CH-10) and of its dataset and experiment routes (CH-12), for offline tests. It runs in process: a test builds `create_app(FakeLangSmith(api_key))` and reaches it through an ASGI transport, so no port opens.

| Route | What it does |
| ----- | ------------ |
| `POST /otel/v1/traces` | OTLP over HTTP, protobuf or JSON. The spans are decoded into `FakeLangSmith.recorded`. A body that is not OTLP is 400. |
| `GET /fake/traces/{trace_id}` | The recorded spans of one trace (`trace_id`, `span_id`, `parent_span_id`, `name`, `attributes`), or 404. Not a LangSmith route. |
| `GET /health` | No auth. |
| `POST /api/v1/datasets`, `GET /api/v1/datasets?name=` | Create a dataset (409 on a taken name), or list by name (CH-12). |
| `POST /api/v1/examples` | One example of a dataset; 404 for an unknown dataset. |
| `POST /api/v1/sessions` | An experiment: a session with `reference_dataset_id`, metadata in `extra.metadata`. |
| `POST /api/v1/runs` | One run of an experiment on an example of its dataset; 202. |

suggested: the `/api/v1` paths and field names are written from memory of the `langsmith` client, not checked against a real LangSmith yet. An empty value such as `inputs: {}` is accepted; a null one is 422. A second example with the same `id` is 409, so a rerun of `eval_kit.langsmith_datasets` on the same dataset stops there.

Every route but `/health` needs the `x-api-key` header; a missing or wrong key is 401 and stores nothing. A missing field is 422. `FakeLangSmith.trace_ids()` and `FakeLangSmith.spans(trace_id)` read the spans back in a test; `dataset_by_name`, `examples_of`, `experiments_of`, and `runs_of` read the datasets (`fake_langsmith/datasets.py`).
