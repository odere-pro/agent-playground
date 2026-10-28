# packages/guardrail-server

The injection and secret check behind LiteLLM's generic guardrail API (PoC-7, ADR-008, 022 H-6), plus a stub of LiteLLM's `apply_guardrail` route for offline tests. Design: `docs/planning/adr/008-security-identity-guardrails-defense-in-depth.md`. Routes and classifiers: `README.md`.

## Rules

- Never imports `chassis` or `chassis_contracts`. It sits behind LiteLLM, not in the chassis. `make lint` (import-linter) enforces it.
- The `pattern` classifier is a stand-in. Never report its numbers as Prompt Guard 2 numbers.
- Long text is checked per window (512 tokens for Prompt Guard 2), with overlap, so an injection on a boundary is seen whole.
- `blocked_reason` and every error name the reason, never the text.
- No secret anywhere. Prompt Guard 2 needs a Hugging Face token; transformers reads it from its own environment.

## How to test

```bash
uv run pytest packages/guardrail-server -q
```

Tests run in process over an ASGI transport and open no socket. The `promptguard` extra (transformers, torch) is never installed in CI.

## Do not

- Add a route the chassis or LiteLLM does not call.
- Make the stub (`litellm_stub.py`) a second LiteLLM. It has the shape of one route and three guardrails, and `litellm/config.yaml` is the matching real config.
