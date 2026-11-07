# Kind drill: the library chart with an HPA (optional scope item)

Date: 2026-11-05. On the Mac: Docker 29.9.2 (10 CPUs, 8 GB), kind with `kindest/node:v1.37.0`, helm v4.3.1 (CI pins v3.19.0), metrics-server chart 3.13.0.

## Commands

```bash
export KUBECONFIG=<scratch>/kind-poc09.kubeconfig   # the user's kube context is not touched
kind create cluster --name poc09 --image kindest/node:v1.37.0@sha256:a1ed56cfb0e7b93589bdf97c8cd566405a265939e3620fc4f5de89adff580ae5
docker build -f packages/chassis/Dockerfile -t kind.local/agent-platform/chassis:poc09 .
docker build -f packages/workloads/echo-python/Dockerfile -t kind.local/agent-platform/echo-python:poc09 .
kind load docker-image kind.local/agent-platform/chassis:poc09 kind.local/agent-platform/echo-python:poc09 --name poc09
helm upgrade --install metrics-server metrics-server --repo https://kubernetes-sigs.github.io/metrics-server/ \
  --version 3.13.0 -n kube-system --set 'args={--kubelet-insecure-tls}' --wait
kubectl create namespace agents
helm install example-agent deploy/helm/example-agent -n agents -f deploy/helm/example-agent/values-kind.yaml --wait
```

Load: 24 threads inside one pod's chassis container, `POST /v1/run` to that pod's own address, for 150 s.

## Result

- Two pods, each `2/2 Running`. The chassis is the native sidecar (an init container with `restartPolicy: Always`), the workload the main container. Both use `imagePullPolicy: Never` (PoC-5 admission rule 8).
- In the workload container: no service account token. The only key-shaped variable is `GPG_KEY`, the Python base image's public signing-key ID, not a credential.
- The HPA went from 2 to 4 replicas (its maximum) at 203% of its 70% CPU target, and the 4 pods were `2/2 Running`. 2394 calls, 0 errors.

```
14:10:30 cpu: 55%/70% 2 4 2
14:10:42 cpu: 203%/70% 2 4 2
14:10:55 cpu: 208%/70% 2 4 4
14:13:21 cpu: 18%/70% 2 4 4
```

## Limits

- The load came from one pod to itself, so it shows the HPA acting on CPU. It does not show traffic spread over the replicas through the Service.
- The `sidecar` lane only. The remote lane (gVisor, two pods) was not run on kind.
- No NetworkPolicy check: this drill applied no policy. kind's default CNI, kindnet, does enforce NetworkPolicy (ADR-005 decision 7), and PoC-5 runs on it.
- Scale-down was not waited for.
