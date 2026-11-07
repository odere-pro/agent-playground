# The Collector's OTLP receiver takes the chassis's token only

Date: 2026-11-06. Review R7-15. Status: the offline checks pass; the stack run is pending CI (`.github/workflows/poc07-stack.yml`). Not run on a stack yet.

## What changed

- `deploy/compose/poc07/otel-collector.yaml`: the OTLP/HTTP receiver on 4318 has a server authenticator, `bearertokenauth` (otel/opentelemetry-collector-contrib 0.163.2; config keys `token` and `scheme` from the extension's README at `v0.163.0`). A call without `Authorization: Bearer <token>`, or with a wrong token, answers 401. Before this, anyone who reached 4318 could write spans into Langfuse under the exporter's Basic auth.
- The token is `OTLP_CHASSIS_TOKEN`. `stack.sh up` makes it with `openssl rand -hex 24` into the git-ignored `.env.poc07` (mode 600). An older env file without it gets one added. The Collector reads it as `${env:OTLP_CHASSIS_TOKEN}`, never from the YAML. Of the Compose services, only `otel-collector` gets it.
- Only the chassis sends it: the stack tests run the chassis on the host and set `OTEL_EXPORTER_OTLP_HEADERS=Authorization=Bearer%20<token>` from the env file. A workload gets no token, so its own OTLP export to this Collector is refused.
- `stack.sh logs` masks every env file value, and now any `Bearer <value>` too.
- The health check on 13133 has no auth, so `stack.sh wait` works as before.
- Not changed: `deploy/compose/poc08-litellm`'s Collector. It has no exporter credential and publishes no host port for 4318; only the chassis and LiteLLM reach it, on the `edge` network. Adding a token there means LiteLLM's `otel` callback must send one too. That is not a small change, so it is left for later.

## Checks

Offline, in `make test-poc POC=07` (`pocs/poc-07-cross-cutting-decisions/tests/test_poc07_stack_static.py`):

- `test_collector_otlp_receiver_requires_a_bearer_token`
- `test_collector_bearer_token_comes_from_the_environment_never_the_file`
- `test_compose_gives_the_otlp_token_to_the_collector_only`
- `test_stack_sh_generates_the_otlp_token`
- `test_the_stack_tests_send_the_token_to_the_collector`

On the stack (`network`, `COMPOSE_STACK=1`), in `test_poc07_stack.py`:

- `test_collector_refuses_otlp_without_the_chassis_token[none]` and `[wrong]`: a POST to `127.0.0.1:4318/v1/traces` answers 401.
- `test_collector_takes_otlp_with_the_chassis_token`: the same empty export with the token answers 200.
- The existing span and feedback tests now export to the Collector with the token.

## How to rerun

```bash
deploy/compose/poc07/stack.sh up && deploy/compose/poc07/stack.sh wait
export "$(deploy/compose/poc07/stack.sh env)"
COMPOSE_STACK=1 uv run pytest -m network -v \
    pocs/poc-07-cross-cutting-decisions/tests/test_poc07_stack.py
deploy/compose/poc07/stack.sh down
```
