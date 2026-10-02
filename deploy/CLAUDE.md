# deploy

Everything here follows ADR-001. Ask `platform-security` before changing anything that touches keys, network policy, or the sandbox.

- The chassis owns the public port. The workload listens on localhost only and is never in the Service. The chassis's public port binds to the pod IP, so localhost carries only the proxies.
- Secrets are mounted into the chassis container only. `automountServiceAccountToken: false`, or the token projected into the chassis container only.
- Default-deny egress per pod, allowing only what the chassis needs. The cloud metadata service and the Kubernetes API are named in the deny list.
- `remote` workloads run in their own pod under gVisor; the chassis pod stays outside the sandbox.
- Images pinned by digest, dependencies by hash. The chassis tag is set once, in the library chart.
- Local first: Compose for every PoC; kind only where a check needs it (PoC-4 container roles, PoC-5, optionally PoC-9).
- The Docker VM is shared with other projects. The PoC-4 scale stack goes only through `compose/scale.sh` (project `poc04`, `-p poc04 -f docker-compose.scale.yaml` on every call) and kind only through `kind/run.sh` (`--name poc04`, `--context kind-poc04`). Never prune, never touch another project's container or context.
- Generated secrets live in `compose/.env.poc04` (mode 600, git-ignored) or a kind Secret; never in a file under version control, never printed. Only the chassis (and daprd) get them; the workload gets none.
