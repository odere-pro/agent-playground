# deploy

How the chassis and its workloads run.

| Folder | Arrives in | What |
| ------ | ---------- | ---- |
| `compose/` | PoC-1 walking skeleton | Docker Compose: chassis and workload as two containers on one network namespace, LiteLLM, MinIO, the observability stack |
| `kind/` | PoC-5 | A local kind cluster with native sidecars, a CNI that enforces NetworkPolicy, a gVisor RuntimeClass, and the admission policies |
| `helm/` | PoC-9 | The shared library chart that adds the chassis container with a pinned tag (024 CH-3) |
