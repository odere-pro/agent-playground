# Compose stack: key debt (2026-09-29)

Recorded per `deploy/compose/SECURITY.md`, section 1 and review checklist item 10.

- **Master key as the chassis key.** LiteLLM issues scoped virtual keys only with a Postgres behind it, and a `docker compose up` that needs a key-generation step breaks "no manual steps". So in the `local` variant `LITELLM_API_KEY` in `.env` is set to the same value as `LITELLM_MASTER_KEY`. The two names stay separate, so the switch to a real virtual key is a `.env` edit. This fails G-1's criterion "no service config holds the master key". Deadline: PoC-5 (one scoped key per service is an exit criterion). It blocks PoC-7 budgets.
- **No master key in the `fake` variant.** LiteLLM 1.103.0 reads `general_settings.get("master_key", get_secret("LITELLM_MASTER_KEY"))`, so an empty value from the config or the env is "auth on" and every call gets 401 `No api key passed in.` (verified in `docker compose logs litellm`). `litellm/config.yaml` therefore sets no `master_key` and `docker-compose.yaml` passes no key env; the proxy is open on `127.0.0.1:4000` and fronts a scripted fake. The `local` override passes both keys and sets `master_key`. Revisit with the virtual-key work in PoC-5.
- **Token counts without a database.** `/spend/logs` and `/spend/tags` answer `No connected db.` A custom callback (`litellm/token_log.py`) prints one line per call instead. PoC-7 (budgets) decides whether LiteLLM gets a Postgres.
- **The agent tag is spoofable** (SECURITY.md section 5): it comes from the request until the virtual key carries it. Debt for G-2.

Re-run: `cd deploy/compose && ./demo.sh`.
