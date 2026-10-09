# bakeoff

The PoC-6 benchmark kit: it will run the bake-off tasks on each engine and write the scorecard. It is a skeleton today. The runner, the measurements, and the scorecard template come in later tasks (plan P1, `docs/plans/2026-10-09-poc-06-bake-off.md`).

```bash
make bakeoff ARGS="..."     # uv run python -m bakeoff; prints the usage and exits 2 for now
```

No framework dependency. It has no Dockerfile.
