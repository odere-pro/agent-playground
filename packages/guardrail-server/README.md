# guardrail-server

The injection and secret check behind LiteLLM's generic guardrail API (PoC-7, [ADR-008](../../docs/planning/adr/008-security-identity-guardrails-defense-in-depth.md)). LiteLLM's own `promptguard` hook calls a hosted vendor API. This server lets Llama Prompt Guard 2 run in our cluster instead.

```bash
guardrail-server --port 8095 --classifier prompt-guard   # needs the promptguard extra and Hugging Face access
guardrail-server --port 8095 --classifier pattern        # the offline stand-in
guardrail-server --host 0.0.0.0 --token-file /run/secrets/guardrail_token --classifier pattern
```

It binds to loopback by default (`--host`).

## Auth

`--token-file PATH` (default: the `GUARDRAIL_TOKEN_FILE` variable) names a file that holds the token. The server reads it once and strips white space. With a token, both POST routes need `x-api-key: <token>` or `Authorization: Bearer <token>`. LiteLLM's `generic_guardrail_api` sends its `api_key` param as `x-api-key`. Anything else gets a 401 with a fixed body. The 401 never echoes what was sent, and the log never holds it. `GET /health` needs no token.

The server refuses to start when:

- the token file is empty, or cannot be read. The message names the path, never the content.
- there is no token file and `--host` is not loopback (`127.0.0.1`, `::1`, `localhost`). An open check tells anyone which texts pass.

With no token file on loopback, it starts and logs a warning that auth is off.

## Routes

| Route | What it does | Auth |
| ----- | ------------ | ---- |
| `POST /beta/litellm_basic_guardrail_api` | LiteLLM's generic guardrail API. Answers `NONE` or `BLOCKED` with a `blocked_reason` (`prompt_injection`, `secret (<kind>)`). It never rewrites a text. `checks` in the guardrail's params picks `injection`, `secrets`, or both. It reads `texts`, and every string value in `tool_calls` and `structured_messages`. | Token, when set |
| `POST /v1/check` | `{text, input_type?, checks?}` to `{allowed, reasons}`. For a caller that is not LiteLLM: a smoke test, the red-team runner. | Token, when set |
| `GET /health` | Names the classifier, so a run can say which one answered. | Open |

What the generic route reads:

- `texts`: the strings LiteLLM pulled out of the call.
- `tool_calls` and `structured_messages`: lists of objects. Every string value is checked, at any depth: a tool call's `function.arguments`, a message's `content`, each part of a list of parts. Keys are not checked.
- A string that appears more than once is checked once. LiteLLM sends each message in `texts` and in `structured_messages`.

Limits and refusals:

- 413 when `texts` has more than `MAX_TEXTS` items (suggested: 256), when the distinct strings over all three fields are more than that, or when they hold more than `MAX_TOTAL_CHARS` characters (suggested: 4 Mi).
- 422 for an empty `checks` on either route, since it would check nothing. A 422 names the field, never the value.
- 503 with a fixed body when the classifier fails.

## Classifiers

- `prompt-guard`: `meta-llama/Llama-Prompt-Guard-2-86M` with transformers. It reads 512 tokens, so text is cut into 512-token windows, 64 tokens shared (`WINDOW`, `OVERLAP` in `classifier.py`). The chassis's `split_for_classifier` (384 and 32) is not used here. A text is blocked when any window scores at or over the threshold (suggested: 0.5). The model is gated on Hugging Face.
- `pattern`: a regex stand-in for offline tests. It windows on words (512 words, 64 shared). It is not a model, and its numbers say nothing about Prompt Guard 2.

The secret check is regex (`secret_scan.py`), on any text, in both modes. It first normalizes the text: NFKC, so full-width letters read as ASCII, and then it drops zero-width and format characters (U+200B to U+200D, U+2060, U+FEFF, U+00AD, the bidi controls). So `sk`, a zero-width space, and `-` still read as `sk-`. The vendor shapes use the same widths as the chassis `redact` SECRET list. A token starts only where no letter or digit is just before it. The kinds, in the order they are tried:

| Kind | Shape (suggested) |
| ---- | ----------------- |
| `api_key` | `sk-` and 16 or more of `A-Z a-z 0-9 _ -` |
| `aws_access_key` | `AKIA` or `ASIA` and 16 of `A-Z 0-9`, with no letter or digit after |
| `private_key` | `-----BEGIN ... PRIVATE KEY-----`, up to 40 characters between |
| `github_token` | `ghp_`, `gho_`, `ghu_`, `ghs_`, or `ghr_` and 20 or more letters and digits; or `github_pat_` and 20 or more |
| `slack_token` | `xoxb-`, `xoxp-`, `xoxa-`, `xoxr-`, or `xoxs-` and 10 or more |
| `jwt` | three base64url parts joined by dots, the first starting `eyJ` |
| `gitlab_token` | `glpat-` and 20 or more |
| `google_api_key` | `AIza` and 35 |
| `url_credentials` | a URL with a user and a password before the `@` of the host |
| `key_assignment` | a key name that ends in `password`, `passwd`, `pwd`, `secret`, `token`, `api_key` (or `apikey`, `api-key`), or `access_key`, in any case, quoted or not (`AWS_SECRET_ACCESS_KEY`, `"api_key"`), then `=` or `:` on the same line, then a value of 8 or more characters, quoted or not |
| `bearer` | `Bearer`, in any case, an optional `:`, and 20 or more token characters |

`key_assignment` does not flag `max_tokens: 1000` (the name goes on past `token`), prose with no `=` or `:` ("the token is valid"), or a value that starts with `<`, `*`, or `$` (a placeholder such as `<your-key>`, a masked `****`, or a `${VAR}`). It does flag a long word after `token:` in prose, and `PWD=/a/long/path` in a shell dump. Those are false positives we accept. This check blocks where `redact` only masks, so two shapes keep a longer minimum than `redact`: `sk-` takes 16 characters, and `bearer` takes 20, so "Bearer authentication" passes.

The chassis fake (`KeywordGuardrails`) holds a hand copy of these patterns and of the normalization. `packages/contract-suites/tests/test_guardrail_pattern_drift.py` checks that the two copies are the same.

## The LiteLLM stub

`guardrail_server.litellm_stub` has the shape of LiteLLM's `POST /guardrails/apply_guardrail` and the three guardrails the chassis names: `pii-mask` (a regex stand-in for LiteLLM's `presidio` hook), `prompt-guard`, and `secret-guard` (both through this server). `StubSettings.guardrail_token` is sent to this server as `x-api-key`, as LiteLLM sends its `api_key` param. A 401 from this server fails the call closed. `litellm/config.yaml` is the matching LiteLLM config. It is not run against a real LiteLLM here; check its field names on the Compose run.

## Tests

`uv run pytest packages/guardrail-server -q`. In process, no socket.
