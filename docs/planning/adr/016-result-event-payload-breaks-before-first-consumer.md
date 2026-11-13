# ADR-016: Result-event payload schemas may break in place until the first consumer ships

- **Date:** 2026-11-13
- **Deciders:** the user (product owner)
- **Format:** Michael Nygard's template: Status, Context, Decision, Consequences
- **Related:** [019 H-17](../issues/019-H-17-event-port.md), [020 X-8](../issues/020-X-8-event-broker.md), [021 H-20](../issues/021-H-20-event-consumer-adapter.md), [040 D-0](../issues/040-D-0-recorder-agent.md), [072 C-3](../issues/072-C-3-audit-log-agent.md), [ADR-004](004-events-through-a-broker-client.md), `docs/specs/contract.md` section 6

## Status

Accepted 2026-11-13, decided by the user.

## Context

The result event payload is `TaskResult`, published as `packages/chassis/schemas/task-result.v1.json` on the topics `agents.task.completed.v1` and `agents.task.failed.v1`. The schema is closed (`additionalProperties: false`).

On 2026-11-13 the user decided two fields in 019 H-17. `input` is left out of the event unless `spec.events.include_input` is true. The model-source field is `original_model`. Both change the v1 payload. A strict v1 reader with the old closed schema refuses the new event: it has an unknown field (`original_model`), and by default it lacks a field that was required (`input`).

No consumer of `agents.task.*.v1` exists yet. The recorder (040 D-0) and the audit log (072 C-3) are not built, and the event consumer adapter (021 H-20) is not built.

| Option | For | Against |
| ------ | --- | ------- |
| Change `task-result.v1.json` in place | No second schema file and no second set of topics before anyone reads them; `spec.events.produces` stays as it is | A break under the v1 name; only safe while no consumer exists |
| Publish `task-result.v2.json` on `agents.task.*.v2` topics | Follows the rule the event schema uses (a break bumps the major) | Two schemas and two topic sets to keep, for no reader; every config's `produces` changes |

## Decision

1. Until the first consumer of a result-event topic ships, a result-event payload schema may change in place, breaks included. suggested: "ships" means 019's consumer side (021 H-20) or the broker with topic ACLs (020 X-8), whichever comes first.
2. Each such change is recorded as a break, not as an additive change, in `docs/specs/contract.md` section 6, with its date and the reason.
3. After the first consumer ships, a change that a closed-schema reader of the previous version would refuse needs a new major: a new `task-result.vN.json` and new `agents.task.*.vN` topics. The previous major stays published while it has readers.
4. The 2026-11-13 change (`input` out by default, `original_model` added) is the first break made under this rule.

## Consequences

- The v1 payload can still be fixed cheaply while nothing reads it.
- A test records the break as known and decided: a new default event does not validate against the previous v1 shape (`packages/chassis/tests/test_result_event_fields.py`).
- Whoever ships the first consumer must close this window, by updating this ADR's status or the contract spec. After that, the rule in item 3 applies.
- A reader written against a pre-consumer copy of `task-result.v1.json` may refuse events. No such reader is supported.

## Revisit

When the first consumer of `agents.task.*` ships (021 H-20, 040 D-0, 072 C-3, or 020 X-8 in production), this window closes and item 3 applies to every later change.
