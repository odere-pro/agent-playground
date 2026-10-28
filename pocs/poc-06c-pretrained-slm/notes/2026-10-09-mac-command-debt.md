# Debt: the Mac command uses the LiteLLM master key as the chassis key, 2026-10-09

Applies to `scripts/poc06_mac.sh` (`make poc06-mac`) and `deploy/compose/poc06/`.

## What the debt is

The script generates one random key per run. LiteLLM takes it as its master key, and the host chassis processes send it as their LiteLLM key. The master key administers LiteLLM; a chassis should hold a scoped key. The exception and its limits are in `deploy/compose/SECURITY.md`, section 1 ("PoC-6 exception").

## Why it is accepted for PoC-6

Local, single user, owner-run. LiteLLM listens on loopback only. The key is random, per run, and never written to a file. Only two fixed routes exist. Workloads get no key. Hosted spend is capped on the provider side: set a provider budget before the run.

## The proper fix

Mint a virtual key per run through `POST /key/generate` with `models: [<route>]`, a short `duration`, and a `max_budget`, and give only that key to the chassis. It needs a database behind LiteLLM, as in the kind stack. Owner: [026 CH-4](../../../docs/planning/issues/026-CH-4-chassis-only-credentials-egress.md) (one scoped key per service).
