# Guardrail server auth, PII masking that fails closed, and the fail-closed drill

Date: 2026-11-06. Status: the offline checks pass; the stack run is pending CI (`.github/workflows/poc08-litellm.yml`). Not run on a stack yet. The numbers in [2026-10-30-stack-drills.md](2026-10-30-stack-drills.md) are from before this change.

## What changed

**The guardrail server needs a token** (`deploy/compose/poc08-litellm`).

- `stack.sh up` makes `GUARDRAIL_TOKEN` with `openssl rand -hex 24` into the git-ignored `.env.poc08-litellm` (mode 600). An older env file without it gets one added.
- LiteLLM gets it as `GUARDRAIL_API_KEY`. `litellm.yaml` sets `api_key: os.environ/GUARDRAIL_API_KEY` on `prompt-guard` and `secret-guard`, and LiteLLM v1.103.0's `generic_guardrail_api` sends it as `x-api-key`.
- The guardrail server gets it as the Compose secret `guardrail_token`. The secret's source is the environment, not a file: Compose copies it into the container at `/run/secrets/guardrail_token`, owned by uid 10007, mode 0400. A `file:` secret is bind-mounted as it is and ignores `uid` and `mode`. A mode-600 file owned by the runner user would then be unreadable by uid 10007 on Linux. The image's ENTRYPOINT passes `--token-file /run/secrets/guardrail_token`.
- The chassis gets nothing new. `stack.sh logs` masks the token, since it masks every env file value.
- The same `api_key` is in `packages/guardrail-server/litellm/config.yaml`, the PoC-7 reference config.

**PII masking fails closed.** LiteLLM v1.103.0's presidio hook (`_fail_on_invalid_response`) blocks on an analyzer HTTP error or invalid answer only when `pii_entities_config` is set. With none, it logs a warning and the text passes unmasked. `pii-mask` now sets it in both configs: ADR-008's set, each `MASK`. The set is `EMAIL_ADDRESS`, `PHONE_NUMBER`, `CREDIT_CARD`, `IBAN_CODE`, `US_SSN`, `PERSON`, `LOCATION`.

- What this narrows: LiteLLM now sends the analyzer only these entities. Before, every entity it found was masked (`URL`, `DATE_TIME`, `IP_ADDRESS`, and others too). The drill's PII values (email, phone, card) and the tokens it looks for are all in the list (`test_every_pii_value_the_drill_checks_is_a_listed_entity`). The red-team row `pii_mask_changes_it` can change, for example on `exfil-04-markdown-image`, whose URL is no longer masked. Those numbers are recorded, not gated.

**Every guardrail runs on every call** (ADR-008 item 4): `default_on: true` on all three guardrails in `packages/guardrail-server/litellm/config.yaml`, as `poc08-litellm` already had.

**The fail-closed drill.** `stack.sh stop <service>` and `stack.sh start <service>` act on `guardrail-server`, `presidio-analyzer`, or `presidio-anonymizer` only, always with `-p poc08-litellm`. `start` waits for the service's health, polled from inside LiteLLM. Two new `network` tests in `test_poc08_stack_litellm.py`:

- `test_a_stopped_guardrail_server_refuses_the_call`: with the guardrail server stopped, a call straight to LiteLLM with the chassis's key is not a 2xx and holds no model answer. A run through the chassis holds no model answer either.
- `test_a_stopped_presidio_analyzer_refuses_the_call`: with the analyzer stopped, a PII call straight to LiteLLM is not a 2xx and the model's raw or masked answer is not in it. Not known yet: whether a refused connection takes the same fail-closed path as an HTTP error in LiteLLM's hook. The CI run answers it.

Each test starts the service again in a `finally`.

## Checks

Offline, in `make test-poc POC=08` (`test_poc08_litellm_static.py`): `test_every_guardrail_is_on_for_every_call`, `test_pii_mask_names_every_adr008_entity_with_mask`, `test_every_pii_value_the_drill_checks_is_a_listed_entity`, `test_the_generic_guardrails_send_the_token_from_the_environment`, `test_the_guardrail_token_reaches_litellm_and_the_guardrail_server_only`, `test_the_guardrail_server_image_reads_its_token_file`, `test_stack_sh_can_stop_and_start_only_the_allowed_guardrail_services`, `test_no_token_literal_in_the_guardrail_config`. The drill now collects 6 tests.

## How to rerun

```bash
deploy/compose/poc08-litellm/stack.sh up && deploy/compose/poc08-litellm/stack.sh wait
export "$(deploy/compose/poc08-litellm/stack.sh env)"
COMPOSE_STACK=1 uv run pytest -m network -v -s \
    pocs/poc-08-cross-cutting-build/tests/test_poc08_stack_litellm.py
deploy/compose/poc08-litellm/stack.sh down
```

The image needs the guardrail server's `--token-file` flag (`packages/guardrail-server/src/guardrail_server/cli.py`). Build from a tree that has it.
