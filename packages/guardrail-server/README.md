# guardrail-server

The injection and secret check behind LiteLLM's generic guardrail API (PoC-7, [ADR-008](../../docs/planning/adr/008-security-identity-guardrails-defense-in-depth.md)). LiteLLM's own `promptguard` hook calls a hosted vendor API. This server lets Llama Prompt Guard 2 run in our cluster instead.

```bash
guardrail-server --port 8095 --classifier prompt-guard   # needs the promptguard extra and Hugging Face access
guardrail-server --port 8095 --classifier pattern        # the offline stand-in
```

It binds to loopback by default (`--host`).

## Routes

| Route | What it does |
| ----- | ------------ |
| `POST /beta/litellm_basic_guardrail_api` | LiteLLM's generic guardrail API. Answers `NONE` or `BLOCKED` with a `blocked_reason` (`prompt_injection`, `secret`). It never rewrites a text. `checks` in the guardrail's params picks `injection`, `secrets`, or both. |
| `POST /v1/check` | `{text, input_type?, checks?}` to `{allowed, reasons}`. For a caller that is not LiteLLM: a smoke test, the red-team runner. |
| `GET /health` | Names the classifier, so a run can say which one answered. |

## Classifiers

- `prompt-guard`: `meta-llama/Llama-Prompt-Guard-2-86M` with transformers. It reads 512 tokens, so text is cut into 512-token windows that overlap. A text is blocked when any window scores at or over the threshold (suggested: 0.5). The model is gated on Hugging Face.
- `pattern`: a regex stand-in for offline tests. It windows on words (512 words, 64 shared). It is not a model, and its numbers say nothing about Prompt Guard 2.

The secret check is regex (`secret_scan.py`), on any text, in both modes.

## The LiteLLM stub

`guardrail_server.litellm_stub` has the shape of LiteLLM's `POST /guardrails/apply_guardrail` and the three guardrails the chassis names: `pii-mask` (a regex stand-in for LiteLLM's `presidio` hook), `prompt-guard`, and `secret-guard` (both through this server). `litellm/config.yaml` is the matching LiteLLM config. It is not run against a real LiteLLM here; check its field names on the Compose run.

## Tests

`uv run pytest packages/guardrail-server -q`. In process, no socket.
