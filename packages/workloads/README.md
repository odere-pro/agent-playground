# workloads

What runs behind the chassis: business logic in any framework or language, served over A2A. One folder per workload, each with its own dependencies and Dockerfile.

| Folder | Arrives in | What |
| ------ | ---------- | ---- |
| `echo-python/` | PoC-1 walking skeleton | The simplifier in plain Python, with its own MCP tool loop. In process (`inprocess`) by the chassis's template server; next to the chassis (`sidecar`) by `packages/workload-a2a` |
| `echo-typescript/` | PoC-2 | The simplifier in TypeScript, served by its own port of the template server on `@a2a-js/sdk` 1.3, to prove the contract has nothing Python in it. No tool |
| `echo-pydanticai/` | PoC-2 | The simplifier in PydanticAI, tools over MCP, served by `packages/workload-a2a` |
| `echo-langgraph/` | PoC-2 | The simplifier in LangGraph, tools over MCP through a stand-in for `langchain-mcp-adapters`, served by `packages/workload-a2a` |

"echo" is the repo's name for the simplifier plug-in (`agent: echo`). Every workload holds no key and calls the chassis's proxies on `127.0.0.1:8090`. PoC-6 adds the rest of the shortlist.
