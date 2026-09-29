# workloads

What runs behind the chassis: business logic in any framework or language, served over A2A. One folder per workload, each with its own dependencies and Dockerfile.

| Folder | Arrives in | What |
| ------ | ---------- | ---- |
| `echo-python/` | PoC-1 walking skeleton | Plain Python `handle`, served by the template A2A server |
| `echo-typescript/` | PoC-2 | The same echo in TypeScript, to prove the contract has nothing Python in it |

PoC-2 adds PydanticAI and LangGraph workloads; PoC-6 adds the rest of the shortlist.
