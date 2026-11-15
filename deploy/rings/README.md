# deploy/rings

The chassis rollout rings ([056 CH-7](../../docs/planning/issues/056-CH-7-chassis-release-rings.md), [ADR-001](../../docs/planning/adr/001-chassis-delivery-model.md) items 9 and 10). A chassis release is one image tag, set once in the library chart. It reaches the services in three rings, with no service image rebuilt: `canary`, then `ten-percent`, then `all`.

| File | What |
| ---- | ---- |
| `canary.yaml`, `ten-percent.yaml`, `all.yaml` | One values overlay per ring: `ring.name` and `ring.chassisTag` |
| `../helm/agent-rings/` | One Argo CD Application per service, its ring's overlay last in its value files, in its ring's sync wave (canary 0, ten-percent 1, all 2) |
| `../prometheus/rules/rings.yaml` | The promotion gate: two alerts that compare a reached ring with the ring still on the previous tag |

The ring sizes are suggested: one non-critical service in `canary` (`agent-rings` refuses any other count), about 10% of the services in `ten-percent`, the rest in `all`.

## How a ring holds a tag

A service layers its ring's overlay on its own values. `agent-lib` then picks the chassis tag, first to last:

1. The service's own `chassis.image.tag`: a pin. It wins over the ring.
2. The ring's `ring.chassisTag`. Empty follows the library.
3. The library's `chassis.image.tag` (`agent-lib/templates/_values.tpl`).

Each must be in the library's `chassis.image.supportedTags`, the current tag and the previous one. The render refuses any other tag, and so does admission rule 9. `ring.name` labels the chassis pod `agents.platform/chassis-ring`, which the gate groups by.

Between releases every overlay's `chassisTag` is empty, so every service runs the library's tag.

## A release, step by step

Each step is one change, and each is a commit that Argo CD syncs.

1. **Release.** Bump `chassis.image.tag` and `supportedTags` in the library (`[new, current]`). In the same change, set `chassisTag` to the current tag in `ten-percent.yaml` and `all.yaml`. Only the canary moves. For one ring cycle, set `admission.chassisVersionAction: Warn` on the platform's chart if the release raises the minimum (026 CH-4); the binding then carries `agents.platform/chassis-version-ring: warn`.
2. **Promote to ten-percent.** When the gate is quiet for the canary for the hold time (suggested: one hour), clear `chassisTag` in `ten-percent.yaml`.
3. **Promote to all.** When the gate is quiet for `ten-percent` for the hold time, clear `chassisTag` in `all.yaml`. Set `chassisVersionAction` back to `Deny`.

## A pin and a rollback

- **Pin one service.** Set `chassis.image.tag` to the previous tag in the service's own values. Its ring no longer moves it. Remove the line to follow the ring again. A pin lasts one release: the next release drops the old tag from `supportedTags`, and the render refuses it.
- **Roll back a ring.** Set its `chassisTag` to the previous tag. One value.

## A gate alert fires

`ChassisRingErrorRateAboveBaseline` or `ChassisRingLatencyAboveBaseline` names the ring. The ring the release has reached does worse than the ring still on the previous tag.

1. Do not promote. The next ring keeps the previous tag, because its overlay still holds it.
2. If it holds, roll the ring back: set its `chassisTag` to the previous tag.
3. Find the cause in the ring's traces and logs before the next release.

The gate needs the `ring` relabel rule in the scrape job (the header of `rings.yaml`). Without it the records have no `ring` label, and the gate never fires.

## The urgent-fix order

ADR-001 item 10. A fix that cannot wait:

1. **Change the central limits first.** The LiteLLM key (revoke it, or lower its budget), the MCP allow-list, or a NetworkPolicy. They take effect at once, with no chassis release.
2. **Then build one chassis image and roll it out in fast rings.** Do the release step, and clear every overlay's `chassisTag` in the same change. The sync waves still order the rings: Argo CD syncs the canary's wave first and waits for it to be healthy. That needs the health check for `argoproj.io/Application` in `argocd-cm`; without it the waves do not wait.

## Not here

- Argo CD itself and its health check: the platform's install, not in this repo yet.
- The relabel rule in a scrape job: 117 X-3, with the kind Prometheus.
- SLO alerts per ring (117 X-3) and a runbook per alert (119 X-6).
- The proof on kind: the issue's open boxes.
