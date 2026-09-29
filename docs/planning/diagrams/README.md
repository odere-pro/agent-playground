# Diagrams

PNG figures for slides and documents. Each one has a Mermaid source in [src/](src/).

| Figure | File | Shows |
| ------ | ---- | ----- |
| 1 | `diagram-1-grand-design.png` | The grand design, with every agent behind the service chassis |
| 2 | `diagram-2-stateless-agent.png` | A stateless agent: a pod with the chassis container and the workload container |
| 3 | `diagram-3-orchestrator-agent.png` | The orchestrator agent, with the Temporal worker in the chassis |
| 4 | `diagram-4-chassis-lanes.png` | The three lanes (`sidecar`, `remote`, `inprocess`), the trust rule, and where the hard limits live |

Figures 1 to 3 started as the epic's Figures 1 to 3. They are redrawn for [ADR-001](../adr/001-chassis-delivery-model.md), so they now differ from the Mermaid figures inside the epic. The epic is read-only, so its own figures still show the design before ADR-001. Figure 4 is new.

To re-render after editing a source (run from this folder; mermaid-cli comes from npm):

```bash
npx -y @mermaid-js/mermaid-cli@11 -c src/mermaid-config.json -i src/diagram-4-chassis-lanes.mmd -o diagram-4-chassis-lanes.png -w 1400 -b white
```

If Puppeteer has no browser, pass `-p puppeteer.json` with an `executablePath` that points at a local Chrome or Chromium.
