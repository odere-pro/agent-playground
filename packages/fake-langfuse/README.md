# fake-langfuse

A fake of the Langfuse public API routes the chassis uses, for offline tests (PoC-7). It runs in process: a test builds `create_app(FakeLangfuse(public_key, secret_key))` and reaches it through an ASGI transport or a Unix socket, so no port opens.

| Route | What it does |
| ----- | ------------ |
| `POST /api/public/scores` | A JSON list of scores. 202 when all are taken, 207 with counts when some are refused. Upsert by `id`. |
| `GET /api/public/v3/scores?id=a,b` | The stored scores, each with `subject: {kind: trace, id}`. |
| `GET /api/public/scores/{id}` | One stored score as sent, or 404. |
| `POST /api/public/otel/v1/traces` | OTLP over HTTP. The body is kept as sent in `FakeLangfuse.traces`. |
| `GET /api/public/health` | No auth. |

Every route but health needs HTTP Basic auth with the project keys. `FakeLangfuse.unavailable = True` makes them answer 503. The shapes are from the `langfuse` SDK 5.0.4 generated client; the module docstring of `fake_langfuse/server.py` names the files.
