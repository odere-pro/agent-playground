---
name: adr
description: Write an architecture decision record in docs/planning/adr from the template, link it from the planning docs, and update the issues it changes. Use when a PoC or a review reaches a decision that outlives it.
---
# ADR

1. Copy `docs/templates/adr.md` to `docs/planning/adr/NNN-<slug>.md`, next number after the last.
2. Fill Status, Context, Decision, Consequences (Nygard). Plain words. Mark values the epic does not give with `suggested:`. Add a Revisit section: the measurement that would reopen it.
3. Link it: from `docs/planning/poc/000-plan.md` where the topic lives, from the issues it changes (`## Why` or `## What`, as `[ADR-NNN](../adr/NNN-<slug>.md)`), and from `docs/planning/issues/000-plan.md` through `docs/planning/tools/plan-template.md`.
4. Run `make planning-sync planning-check`.
5. If the decision changes order, size, or dependencies of issues, edit `docs/planning/tools/backlog.py` `ROWS`, never the frontmatter.
6. Record it in `docs/plans/` only if it came from a plan; otherwise the ADR is the record.
