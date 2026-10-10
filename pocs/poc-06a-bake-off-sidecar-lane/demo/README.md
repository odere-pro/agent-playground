# PoC-6a demo

`demo.sh` runs offline on localhost with the fake model server: `make ts-check`, then `uv run python -m bakeoff smoke`, then a short `bakeoff run` on four sidecar engines. It writes a dated record next to it, `YYYY-MM-DD-demo-bakeoff-offline.md`, with each command, its exit code, and the output tail.

```bash
pocs/poc-06a-bake-off-sidecar-lane/demo/demo.sh
```

It needs Node and the npm registry for `npm ci` the first time. It starts no Docker and no kind.

The hosted demo is not here. It comes from the Mac run, `make poc06-mac` (see `deploy/compose/README.md`, "PoC-6: the one Mac command"). `measure_extra.py` is the helper that took the RSS and cold-start numbers in the scorecard.

Latest record: [2026-10-10-demo-bakeoff-offline.md](2026-10-10-demo-bakeoff-offline.md) (every step ok, commit `d8ea82d`).
