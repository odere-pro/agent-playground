# deploy

Everything here follows ADR-001. Ask `platform-security` before changing anything that touches keys, network policy, or the sandbox.

- The chassis owns the public port. The workload listens on localhost only and is never in the Service. The chassis's public port binds to the pod IP, so localhost carries only the proxies.
- Secrets are mounted into the chassis container only. `automountServiceAccountToken: false`, or the token projected into the chassis container only.
- Default-deny egress per pod, allowing only what the chassis needs. The cloud metadata service and the Kubernetes API are named in the deny list.
- `remote` workloads run in their own pod under gVisor; the chassis pod stays outside the sandbox.
- Images pinned by digest, dependencies by hash. The chassis tag is set once, in the library chart.
- Local first: Compose for every PoC; kind only where a check needs it (PoC-4 container roles, PoC-5, optionally PoC-9).
- The Docker VM is shared with other projects. The PoC-4 scale stack goes only through `compose/scale.sh` (project `poc04`, `-p poc04 -f docker-compose.scale.yaml` on every call) and kind only through `kind/run.sh` (`--name poc04`, `--context kind-poc04`). PoC-5's cluster goes only through `kind/poc05/run.sh` (`make kind-poc05 ARGS="<verb>"`; `--name poc05`, `--context kind-poc05`), and only one agent at a time uses Docker or kind. Never prune, never touch another project's container or context.
- Generated secrets live in `compose/.env.poc04` (mode 600, git-ignored) or a kind Secret; never in a file under version control, never printed. Only the chassis (and daprd) get them; the workload gets none.

## kind PoC-5 (`kind/poc05/`)

- What is there: `base/` (gVisor RuntimeClass, namespaces with Pod Security labels, default deny, agent-sandbox pinned), `platform/` (LiteLLM, Postgres, Valkey, MinIO, the fake servers, the code-runner dispatcher, `seed.sh`), `tools/` (the code-runner warm pool and template), `remote/` (the remote Sandbox), `agents/` (the chassis pods), `admission/` (the trust rule and the extension rules, with fixtures), `smoke/`.
- Rules: only `seed.sh` makes Secrets, through a pipe; none is in the repo. Our images are `kind.local/agent-platform/<name>:poc05`, built and loaded by `run.sh`, never pulled (`imagePullPolicy: Never`); third-party images are pinned by digest. Exactly one `ipBlock` exists: the dispatcher to the API server, filled by `run.sh` from a sentinel. A new edge, Secret mount, or image is a `platform-security` question.
- How to run and test: the `poc-05-operate` skill; problems in `docs/guides/poc-05-runbooks.md`. Static tests run offline in `make test-poc POC=05`.
- Do not: run `kubectl` without `--context kind-poc05`, print a Secret, add `-v` to `kctl`, or edit a refusal test to pass.
