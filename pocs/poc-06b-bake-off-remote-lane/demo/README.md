# PoC-6b demo

`demo.sh` runs the `remote` lane offline on localhost with the fake model server: `make ts-check`, then `uv run python -m bakeoff smoke --lane remote`, then a short `bakeoff run` on `echo-smolagents`, `echo-claude-agent`, and `echo-typescript` in the `remote` lane, then `make test-poc POC=06b`. It writes a dated record next to it, `YYYY-MM-DD-demo-remote-lane-offline.md`, with each command, its exit code, and the output tail.

```bash
pocs/poc-06b-bake-off-remote-lane/demo/demo.sh
```

It needs Node and the npm registry for `npm ci` the first time. It starts no Docker and no kind.

The kind half is not here. `kagent-adk` and the hostile and load suites run on kind in CI (`poc06-kind.yml`); the run table is in [../notes/2026-10-09-lanes-b-kind.md](../notes/2026-10-09-lanes-b-kind.md), and the load numbers are in [../notes/2026-10-10-remote-load-on-kind.md](../notes/2026-10-10-remote-load-on-kind.md). The hosted-model demo comes from the Mac run, `make poc06-mac`.

Latest record: [2026-10-28-demo-remote-lane-offline.md](2026-10-28-demo-remote-lane-offline.md) (every step ok, commit `7dc0774`).
