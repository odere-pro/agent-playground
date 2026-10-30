# deploy/kind/poc06

The PoC-6 engines on the PoC-5 kind cluster. `run.sh` (`make kind-poc06 ARGS="<verb>"`) brings them up and tests them. How it works: [the PoC-6 guide](../../../docs/guides/poc-06-how-it-works.md), section 6.

## Copies

Some manifests are copies of each other. A hardening change made in one must be made in each copy.

| Files | What differs |
| ----- | ------------ |
| `agents/agent-openai-agents.yaml`, `agents/agent-typescript.yaml` (208 lines each) | Names and labels, the image, the workload probe command (`python -c` or `node -e`), and the workload memory limit (384Mi or 256Mi). 21 changed lines |
| `agents/chassis-smolagents-remote.yaml`, `agents/chassis-claude-agent-remote.yaml`, `agents/chassis-typescript-remote.yaml` (180 lines each) | Names, labels, Secret and ConfigMap names, and the proxy Service `clusterIP` (`10.96.85.92`, `.93`, `.94`). Nothing else |

Each remote chassis `clusterIP` is also written in its remote, as the model and MCP URLs in `remote/remote-<engine>.yaml`. Change both files together.

`kagent/chassis-kagent-adk-remote.yaml` is not a copy. It has the plain-A2A settings and differs from the other remote chassis files in most lines.

Check the copies with `diff`, for example `diff agents/agent-openai-agents.yaml agents/agent-typescript.yaml`.
