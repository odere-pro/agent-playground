# Point a client at the router

How to send a model call through the LiteLLM router. For a service author, a test author, or a client app team. The router is the one endpoint for all model calls ([002 G-1](../planning/issues/002-G-1-litellm-router.md)). No key value appears here, only variable names.

## Who calls what

Three callers exist. Each uses a different address.

| Caller | Calls | Key it holds |
| ------ | ----- | ------------ |
| A workload (the agent's own code) | The chassis model proxy, at `CHASSIS_MODEL_URL` | None |
| The chassis | The router, at `LITELLM_BASE_URL` | Its own virtual key, `LITELLM_API_KEY` |
| A developer or a test, by hand | The router, at `http://127.0.0.1:4000/v1` in Compose | A virtual key you were given |

A workload never calls the router and never holds a key. The chassis does, and only the chassis ([ADR-001](../planning/adr/001-chassis-delivery-model.md), hard requirement 1).

## From a workload

1. Read the base URL from `CHASSIS_MODEL_URL`. In Compose it is `http://127.0.0.1:8090/v1`, on the chassis loopback. The port is never published.
2. Send an OpenAI chat request to `POST {CHASSIS_MODEL_URL}/chat/completions`. The chassis also serves Anthropic's `POST /v1/messages` on the same port.
3. Set `model` to a route name, such as `big-default`. The chassis picks the route for the run, so a different name is sent to the run's route.
4. Send the `traceparent` header you received. The chassis ties the call to its run by it.
5. Send no `Authorization` header. The chassis never forwards one and adds its own key.

Streaming and complete both work. Set `"stream": true` for a stream. The Anthropic format reaches the router as OpenAI format, because the chassis maps it first ([ADR-003](../planning/adr/003-chat-formats-onto-the-canonical-request.md)).

A call can fail with 429 `budget_exhausted` (the run has no tokens left), or 403 `model_route_denied` (the key may not use the route). Details: [chassis reference, model_proxy.py](chassis-reference.md#model_proxypy).

## By hand against the router

Use this for a check in a dev stack. The router listens on `127.0.0.1:4000` only.

1. Start the stack: `docker compose -f deploy/compose/docker-compose.yaml up`.
2. Put your virtual key in an environment variable, for example `ROUTER_KEY`. Do not paste it into a command line, a file in the repo, or a chat.
3. Call the router:

```
curl -s http://127.0.0.1:4000/v1/chat/completions \
  -H "Authorization: Bearer $ROUTER_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model": "big-default", "messages": [{"role": "user", "content": "Hello"}]}'
```

4. Any OpenAI SDK works. Set its base URL to `http://127.0.0.1:4000/v1` and its API key to the value of `ROUTER_KEY`.

A call with no key, or a wrong one, is refused (proved on the kind stack by `test_litellm_refuses_the_workload_without_the_chassis_key`). The `fake` Compose variant runs with no master key at all, so it accepts any call; it is for offline tests only (`deploy/compose/SECURITY.md`).

## Routes

A route is the `model` name a client sends. The router config maps it to a backend.

| Route | Where it points |
| ----- | --------------- |
| `big-default` | A big model. In the `fake` variant, the scripted fake model server. In the `local` variant, the provider named by `BIG_DEFAULT_MODEL` and `OPENAI_API_BASE` |
| `local-small` | A small model. In the `local` variant, llama.cpp |

Each config lives in `deploy/compose/litellm/` (`config.yaml` for `fake`, `config.local.yaml` for `local`). The PoC stacks have their own under `deploy/compose/poc06`, `deploy/compose/poc08-litellm`, and `deploy/kind`.

## The rules

- A service uses its own virtual key. It names the routes the service may use, a budget, and a rate limit. Never share one.
- The master key stays in the router container. It mints virtual keys. No client gets it.
- The `local` Compose variant is the exception: it gives the chassis the master key, because it has no database to mint a virtual key. [002 G-1](../planning/issues/002-G-1-litellm-router.md) keeps that open.
- Provider keys (`OPENAI_API_KEY` and the like) are read by the router from its environment as `os.environ/<NAME>`. They are never written in a config.
- `packages/chassis/tests/test_litellm_configs_static.py` fails if a config under `deploy/` holds a key value.

## What the guide does not cover

- Named routes per task and the keys' route scopes: [003 G-1b](../planning/issues/003-G-1b-named-model-routes.md).
- Token and cost counts: [004 G-2](../planning/issues/004-G-2-token-cost-counting.md).
- A live call to Claude, Gemini, and OpenAI. It needs provider keys and has not been run.
