# 2026-10-29: template ops (Compose profiles, debuggers, template CI)

Scope items: "Docker Compose profiles (`fake`, `local`) ... with a debugger attached to each", "CI in the template", "suggested: image signing with cosign". Files: `packages/agentctl/src/agentctl/template/{deploy/compose/,.vscode/,.github/workflows/ci.yml.jinja,.gitlab-ci.yml.jinja}`. Test: `tests/test_poc09_template_ops.py`.

## Decisions

- **Debuggers without changing an image.** A one-shot `debugpy` service fills a named volume with debugpy (pinned by hash) and a `sitecustomize.py`. The chassis and the workload mount it read-only with `PYTHONPATH=/opt/debugpy`. The hook listens only in PID 1, so healthchecks and the debugpy adapter skip it. The chassis image stays the pinned one.
- **Both debug ports sit on the chassis service** (5678 chassis, 5679 workload), on `127.0.0.1`. The workload shares the chassis's namespace, so it cannot publish anything itself.
- **The chassis debugger is off by default in `local`.** debugpy has no auth. The workload shares the namespace, so a listening chassis debugger would hand it `LITELLM_API_KEY`. It is on in `fake`, which holds no key. Ask `platform-security` before changing this.
- **Egress is not enforced in Compose**, as in the platform's `deploy/compose/SECURITY.md` section 6. The deny rule belongs to the Helm chart.
- **CI suites run as a matrix** (`load-smoke`, `hostile`, `redteam`, `eval`), one job each. Push, SBOM (BuildKit attestations), and cosign keyless signing run on the default branch only. The GitLab pipeline has the same stages and signs with an `id_tokens` token for Sigstore.
- **Platform checkout in CI:** `PLATFORM_REPOSITORY`/`PLATFORM_REF` (GitHub vars) or `PLATFORM_REPO_URL`/`PLATFORM_REF` (GitLab). It defaults to `main`. suggested: pin it to a SHA.

## Pins resolved 2026-10-29

- Actions: `sigstore/cosign-installer` v4.1.2 `6f9f1778…`, `docker/setup-buildx-action` v4.4.1 `f87e5991…`, `docker/login-action` v4.7.0 `81c6f93d…` (`gh api repos/<repo>/commits/<tag> --jq .sha`).
- cosign v3.1.4 linux-amd64 sha256 `424e2c97…` (`cosign_checksums.txt` from the release).
- Images (`docker buildx imagetools inspect`): `docker:29.1.3-cli`, `docker:29.1.3-dind`, `ghcr.io/astral-sh/uv:0.11.23-python3.12-trixie-slim`.
- debugpy 1.8.22: `uv pip compile --generate-hashes` (command in `debug/requirements.txt`).

## Open

- Not run live: no Docker daemon on this Mac today. `docker compose config` passes for both profiles.
- The workload path mapping (`/app/src`) assumes agentctl's Dockerfile layout.
- A `remote` connector agent still gets the sidecar Compose file. The remote lane runs on kind.
