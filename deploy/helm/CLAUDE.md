# deploy/helm

The shared library chart (backlog 024 CH-3) and one example chart that includes it. `deploy/CLAUDE.md` still applies. Ask `platform-security` before changing anything that touches a Secret, a NetworkPolicy, the RuntimeClass, or a security context.

- `agent-lib/` is `type: library`: named templates only, in `templates/_*.tpl`. It renders nothing on its own.
- The chassis image tag is set once, in `agent-lib.defaults` (`templates/_values.tpl`). A service pins the previous tag with one value, `chassis.image.tag`. Never `latest`.
- Secrets go to the chassis container only, through `chassis.secretEnv`, `chassis.envFromSecrets`, or `chassis.secretVolumes`. The workload has an allow-list of keys and plain env only; `templates/_validate.tpl` fails the render otherwise. Do not add a workload key that can carry a Secret, a volume, or a security context.
- The uids are not values: chassis 10001, workload 10002 (the uid table in `deploy/README.md`).
- The lane and the trust level come from the chassis config (`chassis.config.spec.engine.connector`, `chassis.config.spec.trust`). `untrusted` needs `remote`. The remote lane is two pods; the workload pod runs under gVisor with its own token only (ADR-005).
- Egress peers are labels. No `ipBlock`, no rule without a `to`.
- No secret value in any file here. Secrets are made outside the chart.
- Test: `make test-poc POC=09` runs `pocs/poc-09-agent-mvp-template/tests/test_poc09_chart.py`. It skips with a reason when `helm` is not on PATH; `.github/workflows/poc09-helm.yml` installs a pinned helm and runs it. A refusal test never changes to pass; fix the chart.
- A service chart's `templates/` files are `<object>.tpl` with no leading underscore: Helm renders every file there not named `_*` as a manifest, and the `.tpl` suffix keeps raw Go templates out of the repo's `deploy/**/*.y*ml` scans (`test_poc06b_kind_static.py`).
- Do not: install helm globally, commit `charts/` or `Chart.lock`, or edit a rendered manifest by hand.
