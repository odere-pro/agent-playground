# Fix plan

The single source the apply PRs work from. Written by consolidation session C1 on 2026-11-06 from the ten findings files, the product plan (`docs/plans/2026-11-06-agent-container-product.md`), and `docs/planning/tools/backlog.py`. It changes nothing by itself.

## User decisions, 2026-11-06

The user decided four items after C1 wrote this plan. Each one overrides every edit below that says "P-n decides" or "stays open until P-n is decided". Apply sessions read this section first.

| Item | Decision | What changes below |
| ---- | -------- | ------------------ |
| P-1, an evaluator outage | **Fail open everywhere**, as built (ADR-010 item 4). No code change | 017 H-4: the sentence reads "An evaluator outage fails open as built (ADR-010 item 4), decided 2026-11-06." 042 A-2 box 5 is rewritten to "An evaluator outage fails open: the attempt ships unscored and counts as `chassis.gate_failed_open`," and is ticked with the gate's fail-open test. 117 X-3 gains a box: "An alert fires on `gate_failed_open / gate_runs` above suggested: 1% over 15 minutes." |
| P-2, a rate-limit store outage | **Fail open everywhere**, as built (`rate_limit_on_unavailable: fail_open`). No code change | 022 H-6: no fail-closed box. Add one sentence to `## What`: "A store outage lets calls through unlimited, decided 2026-11-06 (P-2)." 117 X-3 gains a box: "An alert fires when the rate-limit store is unavailable." |
| P-6, drain on SIGTERM | **Rewrite the box to the contract** (option 1). No code change | 011 H-2 box 7 becomes "`/ready` goes 503 first; in-flight runs finish; the workload stops after the chassis has drained," ticked with the drain tests. 011 H-2 retires (wave `WD`). |
| 023 C-5, the client guide | **Phase A**, before the AI Act Art. 50(2) date, 2026-12-02 | 023 C-5 moves from `later` to phase A. Its open box is the client guide (size S, 1 day). Phase A becomes 9 issues, 21.5 days; `later` becomes 72 issues, 190.5 days. |

## How to read

- **Each finding id is assigned once**, in bold, at the start of a line or in a table cell: a `ROWS` change (section 1), a new issue or a gap home (section 2), a body edit (section 3), a proposal (`proposals.md`), "no change", or a docs fix (both at the end). Other mentions of an id are cross-references, never in bold.
- **C1** marks a decision this session made where the findings disagree, or where a split became a re-scope.
- **Numbers are kept.** Retired rows go to wave `WD`, parked rows to `WP`, and new rows go after 127, so no issue is renumbered.
- **Tick lines** are pasted as the findings wrote them. In the issue, the box becomes `- [x] <criterion> · Delivered in PoC-N: <evidence>`, with each path in backticks, as PR-2's `check.py` rule asks.
- **Names the findings use for halves.** Pasted text may say 008a, 008b, 025 H-10a, 043a, 055 CH-6a, 058 CH-8a, 059 H-15a, 113a, X-5a, or X-7a. Only X-5b is a new row. Every other half means the re-scoped issue with the same number, as section 1 "Splits and re-scopes" says; the C1 lines in section 3 win where they differ.
- **Proposals** (`P-n`) are decisions for the user. A box a proposal touches stays open until it is decided.
- Product phases A to F are the product plan's "The order of work"; `later` means no phase needs the issue. C2 owns `phase-map.md`; the phases here match it on the 127 rows.

## Corrections

W7, W89, and W1012 ran out of usage before re-reading their files. Three read-only checks compared each row's verdicts with its findings. What C1 fixed, in this plan (the findings files are not edited):

1. W1012-06 made 120 X-7 depend on 122 X-5, a higher number (rules 4 and 12). Dropped: the sidecar's cost line moves into 122 X-5's report box.
2. W1012-01 gave 122's box 6 (a report per run) to neither half. It stays in 122 and is copied to X-5b.
3. W1012: 117 X-3 has 6 boxes today, not 7; 7 after the rewrite adds the weekly note.
4. W1012: `resize` on 117 (M), 119 (M), 120 (S), and 122 (M) changes nothing. None is a `ROWS` change.
5. W1012: 120 X-7 box 1 is tick-blocked in the Ticks section but not in the row. Section 3 carries it.
6. W1012: 116 X-1b's `parked` covers only its GPU bullets. 116 stays live, phase `later`.
7. W1012: the 502 item is a box in 122 (W1012-19), not `## Out of scope` as the folded bullet says.
8. W1012 and W7: the halves are named X-5a, X-5b, X-7a, X-7b, and "122b". C1 keeps one new ID, X-5b; 122 keeps the ID X-5 so no file is renamed and no `NNN ID` reference goes stale. 120 is re-scoped, not split.
9. W89: 113 O-9 depends on 068 D-3 only for the feedback box, which W89-11 removes. D-3 is dropped from 113's deps (section 1).
10. W89: the `tick-blocked` verdict belongs to 107 O-6, not 102 O-7. W89-16 names three tests; the 107 test gets a suggested name, `test_a_run_budget_counts_steps_and_cost_across_calls`.
11. W89: W89-10 is a code conflict, so 095 P-4 gains `conflict`; it is proposal P-21.
12. W89: 094 and 099 depend on 091 R-11 through 093 and 092, and 091 depended on parked 036 S-7. Cleared by dropping S-7 from R-11.
13. W89: W89-18 says "if 059 splits". 059 is re-scoped, not split; its five kinds move to 008 H-14, which 099 already depends on, and 099 gains 059.
14. W89: 096 P-2a's evidence names D-7, D-3, and D-2; its `ROWS` deps are G-6, D-4, P-4, and A-5. The deps stay. 006 G-6 is live (phase E), so the edge is fine.
15. W89: 097 P-3's dataset resource waits on 069 D-7, which is `later`, not parked. It stays under `## Out of scope` until 069 lands.
16. W7: 089 R-15's row says two of four event types have payload schemas; W7-21 says three of four. Section 3 uses three.
17. W7: 077 C-1's `conflict` rests on a spec sentence, not on 077's code. It is proposal P-16 with W5-04; 077's box stays open.
18. W7 and W2a disagree on the home of the E.4 governance fields (W7-02: 077 or 010; W2a-21: 077). C1: 077 C-1.
19. W7: 078 R-16, 084 R-3, and 093 R-14 are size S with 7 open boxes and no resize finding. Not resized; re-check when W7 leaves `later`.
20. W01-02 (a new issue) and W2a-02 (012 H-3) disagree on the route map's home. C1: 012 H-3, because 017 H-4 and 042 A-2 sit above it and need it.
21. W01-22, W4-05, and W1012-04 name three homes for the container dashboard (117, 046, a new X-9). C1: 046 A-5. The weekly record is 117 X-3's box 7.
22. W2b-08 and W5-01 both want the chart in the generated repo. C1: 025 H-10 holds the chart and the pin; 059 H-15 renders them.

Verdict-label and severity nits the checks also found (for example `keep` beside a change, `parked-dep`, a LOW that the rubric grades MEDIUM) change no edit, so this plan does not list them one by one.

## 1. ROWS changes

All in PR-R, in `docs/planning/tools/backlog.py`. Old values are `ROWS` today. Deps are listed as the new full list.

| Issue | Field | Old | New | Finding |
| ----- | ----- | --- | --- | ------- |
| 011 H-2 | `wave` | W2 | WD | **W2a-19**; with W2a-22 and P-6 |
| 011 H-2 | `note` | (empty) | Was W2. Retired 2026-11-DD: delivered by PoC-3 and PoC-4. | W2a-19 |
| 005 G-3 | `wave` | W1 | WP | **W01-03** |
| 027 S-1 | `wave` | W3 | WP | W3 row (rule 13) |
| 029 S-2 | `wave` | W3 | WP | W3 row (rule 13) |
| 030 S-4 | `wave` | W3 | WP | **W3-03** |
| 031 G-5 | `wave` | W3 | WP | W3 row (rule 13) |
| 032 M-1 | `wave` | W3 | WP | W3 row (rule 13) |
| 033 S-5 | `wave` | W3 | WP | W3 row (rule 13) |
| 034 C-8 | `wave` | W3 | WP | **W3-18** |
| 035 S-6 | `wave` | W3 | WP | W3 row (rule 13) |
| 036 S-7 | `wave` | W3 | WP | W3 row (rule 13) |
| 039 X-2 | `wave` | W3 | WP | **W3-16** |
| 041 A-1 | `wave` | W4 | WP | W3 and W4 rows (rule 13) |
| 043 A-3 | `wave` | W4 | WP | **W4-06** (C1: re-scope, not split; live boxes move to 019 H-17) |
| 044 G-4 | `wave` | W4 | WP | **W4-13** |
| 045 A-4 | `wave` | W4 | WP | W4 row (rule 13) |
| 047 M-3 | `wave` | W4 | WP | **W3-20**; W4 row |
| 048 M-4 | `wave` | W4 | WP | **W4-14** |
| 049 M-5 | `wave` | W4 | WP | W4 row (rule 13) |
| 063 S-8 | `wave` | W5 | WP | **W5-20** |
| 073 E-1 | `wave` | W6 | WP | **W6-04** |
| 074 E-2 | `wave` | W6 | WP | **W6-05** |
| 075 E-3 | `wave` | W6 | WP | **W6-06** |
| 127 L-3 | `wave` | W12 | WP | **W1012-16** |
| 003 G-1b | `size` | S | M | **W01-15** |
| 006 G-6 | `size` | M | S | **W01-04** |
| 007 H-1 | `size` | L | S | **W2a-16** |
| 008 H-14 | `size` | M | L | **W2a-08** (C1: re-scope, not split; 059's five kinds move in) |
| 009 CH-1 | `size` | M | S | W2a row: one open box left (W2a-18) |
| 012 H-3 | `size` | M | L | **W2a-02** (the route map lands here) |
| 014 H-7 | `size` | S | M | C1: three boxes added (sampling, guard-stage span, SDK cost; W2a-13, W1012-04) |
| 015 H-8 | `size` | L | M | **W2a-27** |
| 017 H-4 | `size` | L | M | **W2b-05** |
| 020 X-8 | `size` | M | L | **W2b-15** |
| 021 H-20 | `size` | S | M | **W2b-16** |
| 024 CH-3 | `size` | M | L | **W2b-23** |
| 025 H-10 | `size` | L | M | **W2b-08** (C1: re-scope, not split) |
| 026 CH-4 | `size` | M | L | **W2b-24** |
| 028 S-3 | `size` | M | S | W3 row: the live half is two boxes |
| 037 M-2 | `size` | M | S | **W3-05** |
| 040 D-0 | `size` | M | L | **W4-08** |
| 042 A-2 | `size` | S | M | **W4-02** |
| 046 A-5 | `size` | S | M | **W4-05** |
| 051 H-13 | `size` | S | M | **W5-17** |
| 053 H-22 | `size` | S | M | **W5-16** |
| 054 H-16 | `size` | M | L | **W5-12** |
| 055 CH-6 | `size` | L | S | **W5-03** (C1: re-scope, not split) |
| 056 CH-7 | `size` | M | L | **W5-22** |
| 057 H-19 | `size` | S | M | **W5-18** |
| 058 CH-8 | `size` | M | L | **W5-05** (C1: re-scope, not split; the ADRs go to new CH-9) |
| 059 H-15 | `size` | M | L | C1: phase F boxes plus the trial box (W5-24) |
| 077 C-1 | `size` | S | M | **W7-10** |
| 086 R-10 | `size` | S | M | W7-10 |
| 094 P-1 | `size` | S | M | **W89-04**; W89-07 |
| 100 O-2 | `size` | M | L | **W89-03** |
| 101 O-13 | `size` | S | M | W89-04 |
| 104 O-3 | `size` | M | L | W89-04; W89-22 |
| 107 O-6 | `size` | S | M | W89-04; W89-20 |
| 109 O-8 | `size` | S | M | W89-04 |
| 111 O-14 | `size` | M | L | W89-04; W89-21 |
| 125 L-1 | `size` | L | M | **W1012-17** |
| 126 L-2 | `size` | L | M | W1012-17 |
| 014 H-7 | `deps` | 007 H-1, 012 H-3, 009 CH-1 | 007 H-1, 009 CH-1 | **W2a-07** |
| 016 H-9 | `deps` | 010 H-12, 011 H-2, 014 H-7, 009 CH-1 | 011 H-2, 009 CH-1 | **W2a-29** (box 1 no longer needs MinIO, W2a-28) |
| 026 CH-4 | `deps` | 002 G-1, 020 X-8, 022 H-6, 025 H-10 | 002 G-1, 024 CH-3 | **W2b-01**; W01-24 |
| 028 S-3 | `deps` | 027 S-1 | none | W3 row (rule 13: S-1 parked) |
| 037 M-2 | `deps` | 028 S-3, 030 S-4, 032 M-1, 020 X-8 | 028 S-3 | W3-05 |
| 042 A-2 | `deps` | 017 H-4, 028 S-3, 041 A-1 | 017 H-4, 028 S-3 | **W4-03** |
| 046 A-5 | `deps` | 014 H-7, 042 A-2 | 001 DEC-1, 014 H-7, 042 A-2 | C1: the north star row needs the DEC-1 north star box (W01-06) |
| 053 H-22 | `deps` | 018 H-18, 021 H-20 | 018 H-18, 021 H-20, 040 D-0 | W5-16 |
| 054 H-16 | `deps` | 012 H-3, 018 H-18, 013 CH-2 | 018 H-18, 017 H-4, 022 H-6 | **W5-07** |
| 055 CH-6 | `deps` | 009 CH-1, 026 CH-4, 010 H-12, 038 X-1a | 009 CH-1, 026 CH-4, 010 H-12, 024 CH-3 | W5-03; **W3-09** |
| 056 CH-7 | `deps` | 024 CH-3, 055 CH-6, 014 H-7 | 024 CH-3, 055 CH-6, 014 H-7, 051 H-13, 038 X-1a | W5-22 |
| 057 H-19 | `deps` | 025 H-10 | 025 H-10, 015 H-8 | W5-18 |
| 058 CH-8 | `deps` | 015 H-8, 025 H-10 | 015 H-8 | **W5-08** |
| 059 H-15 | `deps` | 008 H-14, 025 H-10, 051 H-13, 057 H-19, 058 CH-8, 055 CH-6 | 025 H-10, 057 H-19, 058 CH-8, 055 CH-6, 024 CH-3 | **W5-01** (C1: re-scope, not split) |
| 060 H-23 | `deps` | 021 H-20 | 021 H-20, 053 H-22 | **W5-19** |
| 062 H-11 | `deps` | 011 H-2, 012 H-3, 058 CH-8 | 011 H-2, 013 CH-2, 058 CH-8, 025 H-10 | **W5-26** |
| 064 D-1 | `deps` | 017 H-4, 040 D-0, 042 A-2 | 017 H-4, 040 D-0, 042 A-2, 028 S-3 | **W6-11** |
| 068 D-3 | `deps` | 067 D-5, 030 S-4 | 067 D-5, 037 M-2 | **W6-02**; W3-03 |
| 076 R-1 | `deps` | 008 H-14, 051 H-13, 019 H-17, 022 H-6 | 008 H-14, 051 H-13, 019 H-17, 022 H-6, 059 H-15 | **W7-09** |
| 079 C-2 | `deps` | 077 C-1 | 077 C-1, 078 R-16 | **W7-18** |
| 080 R-7 | `deps` | 076 R-1 | 076 R-1, 025 H-10 | **W7-05** |
| 091 R-11 | `deps` | 076 R-1, 090 R-13, 003 G-1b, 036 S-7 | 076 R-1, 090 R-13, 003 G-1b | **W3-01**; W7-03 |
| 092 R-12 | `deps` | 091 R-11 | 091 R-11, 086 R-10 | **W7-17** |
| 093 R-14 | `deps` | 051 H-13, 092 R-12 | 051 H-13, 092 R-12, 080 R-7 | W7-17 |
| 099 O-1 | `deps` | 008 H-14, 092 R-12, 098 O-10 | 008 H-14, 092 R-12, 098 O-10, 059 H-15 | **W89-18** |
| 110 C-6 | `deps` | 065 D-2, 072 C-3, 109 O-8 | 065 D-2, 072 C-3, 109 O-8, 077 C-1 | **W89-14** |
| 113 O-9 | `deps` | 100 O-2, 068 D-3 | 100 O-2 | C1 correction: feedback leaves 113 (W89-11), so D-3 goes |
| 117 X-3 | `deps` | 014 H-7, 038 X-1a, 056 CH-7 | 014 H-7 | **W1012-03** |
| 120 X-7 | `deps` | 004 G-2, 104 O-3 | 004 G-2 | **W89-01** |
| 121 X-4 | `deps` | 038 X-1a, 069 D-7 | 038 X-1a | **W1012-11** |
| 122 X-5 | `deps` | 091 R-11, 104 O-3 | none | **W7-01**; W1012-05 |
| 123 C-4 | `deps` | 032 M-1, 051 H-13, 052 H-21, 078 R-16 | 051 H-13, 052 H-21, 078 R-16 | **W3-14**; W1012-09 |
| 126 L-2 | `deps` | 070 D-4, 075 E-3 | 070 D-4, 042 A-2 | **W6-03**; W1012-14 |
| 006 G-6 | `title` | Savings dashboard with live token spend | Token and cost dashboard with live spend | W01-04 |
| 012 H-3 | `title` | Model port to the LLM router, plus direct vLLM and llama.cpp adapters | Model port to the LLM router, with a route map per task | **W2a-01** |
| 028 S-3 | `title` | Simplifier metrics: tokens, readability, semantic similarity, facts_kept, banned words | `facts_kept` and banned-word scoring for the eval gate and the judge | **W3-04** |
| 037 M-2 | `title` | Eval gate in CI: a new model ships only if it beats the current one | Eval gate in CI: a change ships only if it passes the golden set against the baseline | W3-05 |
| 046 A-5 | `title` | Simplifier agent dashboard: pass rate, fallback rate, tokens saved, latency | Container dashboard and per-agent gate panels | W4-05 |
| 122 X-5 | `title` | Load tests for agent pools and the registry | Load tests: throughput and sidecar overhead on a Linux host | W1012-01 |
| 008 H-14 | `note` | (empty) | Phase later (review 2026-11-06): lane and trust are built and ticked; class, kind, modules, the B.5 table, and the five agentctl kinds are open. | W2a-08 |
| 025 H-10 | `note` | The registry registration step is a stub until the registry exists (R-1, R-2). | Registration is by label, through the controller (083 R-2). | **W7-15** |
| 043 A-3 | `note` | Last P0 story: first measured savings. | Was W4. Parked 2026-11-06 with the SLM track. The live result-event boxes moved to 019 H-17. | W4-06 |
| 113 O-9 | `note` | (empty) | Run status moved to 100 O-2 on 2026-11-06; this issue is long-term memory. | **W89-15** (C1: re-scope, not split) |
| 120 X-7 | `note` | (empty) | Per-pool cost moved to 104 O-3 on 2026-11-06; rates are config, not 031 G-5. | **W1012-06** (C1: re-scope, not split) |
| 122 X-5 | `note` | (empty) | Phase A: the PoC-4 criterion 4 rerun on Linux. Pool, registry, and event-burst tests are X-5b. | W1012-01 |

Every parked row also gets `note` = "Was Wn. Parked 2026-11-06 with the SLM track (product plan).", with its old wave. 043 A-3's note is the one in the table.

### Splits and re-scopes

The rule: split only when the open work is over 5 days or has two owners. A split adds an ID after 127, so nothing above 127 can depend on the new half. That rules out most splits here.

| Issue | Findings asked | C1 decision | Why |
| ----- | -------------- | ----------- | --- |
| 001 DEC-1 | box 8 into 8a and 8b | box split, no new ID | a box split; section 3 |
| 008 H-14 | 008a done, 008b later | re-scope: one live issue, phase `later`, size L | 008b is M with one owner; 040, 072, 076, 099 depend on it, so it cannot move after 127 |
| 012 H-3 | vLLM half parked; route map in | re-scope: vLLM text to `## Out of scope`; three route-map boxes; size L | one owner; 017 and 042 need it below them |
| 025 H-10 | H-10a (F), H-10b (later) | re-scope: phase F boxes stay; cluster boxes go to 038 X-1a, 024 CH-3, 021 H-20, 083 R-2; size M | each later box has a home that exists |
| 043 A-3 | 043a (later), 043b (parked) | re-scope: park the issue; two live boxes go to 019 H-17 | 019 is the result-event issue already |
| 055 CH-6 | CH-6a (A), CH-6b (later) | re-scope: the managed runtime goes to `## Out of scope` (ADR-006); size S | ADR-006 put managed runtimes out of scope |
| 056 CH-7 | move box 4 | re-scope: box 4 to 026 CH-4 | a box move |
| 058 CH-8 | CH-8a, CH-8b (both C) | re-scope: one issue, size L; the two ADRs are new CH-9 | one owner for the code; the ADRs have their own owner |
| 059 H-15 | H-15a (F), H-15b (later) | re-scope: phase F boxes stay; the kinds go to 008 H-14; size L | 008 holds class, kind, and modules already |
| 113 O-9 | 113a run state, 113b memory | re-scope: run status to 100 O-2; feedback out (ADR-009); size L stays | 114 and 125 depend on 113, so neither half can move after 127 |
| 117 X-3 | move alert bullets | re-scope: alerts for parts not built go to `## Out of scope` | no split was proposed |
| 120 X-7 | X-7a (E), X-7b (later) | re-scope: per-pool cost to 104 O-3 | 104 holds the pools |
| 122 X-5 | X-5a (A), X-5b (later) | **split**: 122 keeps X-5 (phase A); new X-5b at 133 | two owners (the user's Linux host; the platform) and two phases; no lower issue can take boxes that need 117 |

### New rows, paste-ready

Append after the `L-3` row, in this order. The index is the position.

```python
    ('CH-9', 'LangGraph checkpointer and interrupts: one ADR each', 'langgraph-checkpointer-interrupts', 'W5', '0', 'harness', 'P2', 'S', ['CH-8'], ['F.1', 'B.5'], 'story', 'From the product plan, phase C (gap P3). Owner: chassis-architect.'),
    ('CH-10', 'LangSmith as a trace backend: Collector exporter, fake-langsmith, stack tests', 'langsmith-trace-backend', 'W5', '0', 'harness', 'P1', 'M', ['H-7'], ['G.2'], 'story', 'From the product plan, phase D (gap P1). ADR-007 item 10.'),
    ('CH-11', 'LangSmith feedback adapter behind FeedbackPort', 'langsmith-feedback-adapter', 'W5', '0', 'harness', 'P1', 'S', ['CH-10'], ['G.2'], 'story', 'From the product plan, phase D (gap P1). ADR-009 item 3a.'),
    ('CH-12', 'Golden cases in Langfuse and LangSmith datasets', 'golden-cases-datasets', 'W6', '2', 'lifecycle', 'P1', 'S', ['M-2', 'CH-10'], ['E.5', '#principles'], 'story', 'From the product plan, phase D. ADR-010 trial rule.'),
    ('CH-13', 'OWASP agentic threat list (ASI01 to ASI10) mapped to the controls', 'owasp-agentic-map', 'W11', '9', 'governance', 'P1', 'S', ['CH-4', 'H-6'], ['E.5', 'R12'], 'story', 'From the product plan, phase E (gap P2). Owner: platform-security.'),
    ('X-5b', 'Load tests for agent pools, the registry, and event bursts', 'load-tests-pools-registry', 'W10', '8', 'infra', 'P1', 'M', ['R-11', 'O-3', 'X-3'], ['G.4'], 'story', 'Split from X-5 on 2026-11-06: 122 X-5 keeps the phase A rerun on Linux.'),
```

| Index | Row | Finding |
| ----- | --- | ------- |
| 128 | CH-9 | **W89-05**; W5-05 |
| 129 | CH-10 | **W2a-09** |
| 130 | CH-11 | product plan gap P1 (phase D); W2a-09 |
| 131 | CH-12 | **W6-01** |
| 132 | CH-13 | **W7-19**; W1012-04 |
| 133 | X-5b | **W1012-01** (the split); deps **W1012-05** |

### Dependencies on parked rows

Every live edge into a row that becomes `WP`, and its fix. After the fixes the snippet in section 4 finds none.

| Live issue | Parked dependency | Fix |
| ---------- | ----------------- | --- |
| 028 S-3 | 027 S-1 | dropped; new deps: none |
| 037 M-2 | 030 S-4 | dropped; new deps: 028 S-3 |
| 037 M-2 | 032 M-1 | dropped; new deps: 028 S-3 |
| 042 A-2 | 041 A-1 | dropped; new deps: 017 H-4, 028 S-3 |
| 068 D-3 | 030 S-4 | dropped; new deps: 067 D-5, 037 M-2 |
| 091 R-11 | 036 S-7 | dropped; new deps: 076 R-1, 090 R-13, 003 G-1b |
| 123 C-4 | 032 M-1 | dropped; new deps: 051 H-13, 052 H-21, 078 R-16 |
| 126 L-2 | 075 E-3 | dropped; new deps: 070 D-4, 042 A-2 |

### Tool changes these rows need (PR-2)

- `check.py` coverage: `re.sub(r"^(X-1|X-5|P-2)[ab]$", ...)` and `want_k = 2 if sid in ("X-1", "X-5", "P-2")`. 122 keeps `X-5`, and `X-5b` maps to `X-5`, so the story counts twice.
- `check.py`: refuse a live issue that depends on a `WP` row (the plan's "Retire mechanism").
- `sync.py`: `MVP_PATH` and `MVP_DAYS` read `best[id2["A-3"]]`. 043 A-3 is parked, so the anchor must change. suggested: the critical path to 059 H-15 (template GA, phase F), labeled as such in `plan-template.md`.
- `sync.py`: `core` and the critical path leave out `WD`, `WP`, and `W12`.
- `backlog.py`: add `CH-9`, `CH-10`, and `CH-11` to the `CHASSIS` layer set. `CH-12` and `CH-13` stay `platform`.
- `sync.py` creates a skeleton with the `<!-- BODY: ... -->` marker for each new row, and `check.py` fails until the body is written. So PR-R writes the six bodies from section 2.

## 2. New issues

### Where each product-plan gap lands

Checked against "The order of work" and "Gaps the PoC track did not cover". Most gaps get a home in an existing issue; five need a new one.

| Gap | Phase | Home | Finding |
| --- | ----- | ---- | ------- |
| Route map per task (`ModelSpec.routes`, `judge_route`) | B | 012 H-3, re-scoped (three boxes); `agentctl`'s route check goes to 059 H-15 | **W01-02**; W2a-02 |
| The judge on by default; its cost, latency, and agreement from the Mac run | B | 042 A-2 (cloud profile, outage rule, metrics, Mac numbers); the agreement box is 028 S-3 box 5 | **W3-02**; W4-02 |
| The retry stage on a model error | B | 017 H-4 (two new boxes) | W2b-05 |
| Default-deny egress and the admission rules in the chart | A | 026 CH-4 (two new boxes, plus 056's minimum-version box) | W2b-02; P-16 |
| LangGraph default, MCP adapters, trace join, `create_agent` | C | 058 CH-8, re-scoped | W5-05 |
| Checkpointer and interrupts ADRs | C | new CH-9 | W89-05 |
| LangSmith exporter, `fake-langsmith`, two stack tests | D | new CH-10 | W2a-09 |
| `langsmith` feedback adapter | D | new CH-11 | product plan |
| LangSmith datasets on the 20 golden cases | D | new CH-12 | W6-01 |
| Container dashboard (north star, inputs, counters) | E | 046 A-5, re-scoped; 006 G-6 is its token and cost input; no X-9 | **W01-22**; W4-05 |
| Weekly record, SLOs, alerts | E | 117 X-3 (box 7 is the weekly note) | W1012-02 |
| Guard-stage span, Collector sampling, OTel SDK cost | E | 014 H-7 (three new boxes) | **W1012-04**; W2a-13; W2b-19 |
| OWASP agentic list mapped to the controls | E | new CH-13 | W7-19 |
| `/metrics` open by default | E | 022 H-6 box 1, after P-4 | W2b-09 |
| The per-case scriptable fake model | F | 015 H-8 (one new box) | W2a-10 |
| The chart in the generated repo, a published platform pin | F | 025 H-10 (two new boxes); 059 H-15 renders it | W2b-08; W5-01 |
| A trial by a person new to the code | F | 059 H-15 (one new box) | product plan |

### 128 CH-9: LangGraph checkpointer and interrupts: one ADR each

- Wave W5, size S, priority P2, deps 058 CH-8. Product phase C. Owner `chassis-architect`.
- Finding: W89-05; W5-05.

What:

- Two ADRs: where a LangGraph agent keeps state between runs (none, the store behind `StatePort`, or a LangGraph saver), and how an interrupt maps onto the `handle` contract (A2A `input-required`, or a refused feature).
- Each ADR closes one ADR-006 Revisit trigger. 100 O-2, 109 O-8, 095 P-4, and 125 L-1 cite them once they land.

Out of scope:

- Building a checkpointer store. The orchestrator's run state (100 O-2).

Acceptance criteria:

- [ ] The checkpointer ADR is accepted and linked from ADR-006.
- [ ] The interrupt ADR is accepted and linked from ADR-006 and the contract spec.
- [ ] The LangGraph template has a test for the chosen interrupt mapping, or a test that the chassis refuses an interrupt with a clear error.
- [ ] ADR-006's two Revisit triggers are closed or restated with a date.

### 129 CH-10: LangSmith as a trace backend: Collector exporter, fake-langsmith, stack tests

- Wave W5, size M, priority P1, deps 014 H-7. Product phase D. Owner `observability-expert`.
- Finding: W2a-09.

What:

- The Collector gets an `otlphttp/langsmith` exporter (ADR-007 item 10), with the LangSmith key only in the Collector. `fake-langsmith` takes OTLP and records traces for offline tests.
- Two stack tests show one run as one trace in LangSmith, with LangGraph's steps under the run span, and no LangSmith key in any other container or log.

Out of scope:

- The feedback adapter (CH-11). Datasets (CH-12). Choosing the primary trace view (DEC-1).

Acceptance criteria:

- [ ] `deploy` holds the exporter behind a profile switch; the default stack runs without it.
- [ ] `fake-langsmith` (a package beside `fake-langfuse`) records an OTLP trace, and an offline test reads it back.
- [ ] A stack test: one run is one trace in `fake-langsmith`, with the graph steps under the run span.
- [ ] A stack test: the LangSmith key is in the Collector only; the chassis, the workload, and the logs hold none.
- [ ] The primary trace view per environment is written where DEC-1 records it.

### 130 CH-11: LangSmith feedback adapter behind FeedbackPort

- Wave W5, size S, priority P1, deps 129 CH-10. Product phase D. Owner `observability-expert`.
- Finding: product plan gap P1 (phase D); W2a-09.

What:

- A `langsmith` adapter behind `FeedbackPort` (ADR-009 item 3a) writes a score onto the run's LangSmith trace.
- It binds the `FeedbackPort` contract suite with the fake and the Langfuse adapter. Where the score goes when both backends run follows DEC-1.

Out of scope:

- Subject binding, a reviewer role, comment redaction (ADR-009 open questions, the user's).

Acceptance criteria:

- [ ] The adapter passes the `FeedbackPort` contract suite against `fake-langsmith`.
- [ ] `POST /v1/feedback` with the adapter selected writes one score that shows on the run's trace.
- [ ] The adapter is picked by `spec.adapters` only, and the key stays in the chassis.
- [ ] With both backends on, the score goes where DEC-1 says, and a test shows it.

### 131 CH-12: Golden cases in Langfuse and LangSmith datasets

- Wave W6, size S, priority P1, deps 037 M-2, 129 CH-10. Product phase D. Owner `eval-expert`.
- Finding: W6-01.

What:

- The 20 golden cases and one run per engine land in a LangSmith dataset through `fake-langsmith`, and the Langfuse dataset gets the stack test ADR-010 item 2 waits on.
- A Makefile target or a workflow runs the upload; the dataset name comes from config. 070 D-4's export design is not changed.

Out of scope:

- A dataset store of our own (069 D-7). Export formats (070 D-4).

Acceptance criteria:

- [ ] The 20 cases and one run per engine land in a LangSmith dataset through `fake-langsmith`.
- [ ] The verdicts match on two engines (the ADR-010 note of 2026-11-06).
- [ ] A stack test covers the Langfuse dataset and its runs (`eval_kit.langfuse_datasets`).
- [ ] A workflow or a Makefile target calls the upload.
- [ ] The dataset name comes from config, as in 037 M-2 box 6.

### 132 CH-13: OWASP agentic threat list (ASI01 to ASI10) mapped to the controls

- Wave W11, size S, priority P1, deps 026 CH-4, 022 H-6. Product phase E. Owner `platform-security`.
- Finding: W7-19; W1012-04.

What:

- Map the OWASP agentic threat list (ASI01 to ASI10) and the vault's threat model to the controls the chassis and the chart enforce, in `docs/specs/security.md`, each with its test or note.
- Tool-description poisoning maps to Snyk Agent Scan in CI (`deploy/agent-scan/`; no real scan has run) and to 081 R-8 (`later`). Read `~/Git/sdlc-vault` first and name the note used.

Out of scope:

- The AI Act and ISO 42001 table (124 C-9).

Acceptance criteria:

- [ ] A table lists ASI01 to ASI10, each with its control and its evidence (a test id or a dated note), or "gap" with the issue that owns it.
- [ ] The tool-description poisoning row names Snyk Agent Scan and 081 R-8.
- [ ] Every gap has an open box in an existing issue or a new issue.
- [ ] `platform-security` reviews the table, and the review is recorded.

### 133 X-5b: Load tests for agent pools, the registry, and event bursts

- Wave W10, size M, priority P1, deps 091 R-11, 104 O-3, 117 X-3. Product phase later. Owner `developer`.
- Finding: W1012-01 (the split); deps W1012-05.

What:

- The load tests that need agent pools, the registry, and the broker: scale to zero and back, a pool's p95 at the target load, registry search at 5,000 entries, and an event burst.
- Split from 122 X-5 on 2026-11-06. Pass or fail is against the 117 X-3 SLOs.

Out of scope:

- The Linux rerun and the sidecar overhead (122 X-5).

Acceptance criteria:

- [ ] The pool test runs on a cluster and shows scale-up from 0 and back to 0.
- [ ] An agent pool meets its p95 latency SLO at the target load (suggested: twice the current peak).
- [ ] Registry search meets its p95 latency SLO with at least 5,000 entries.
- [ ] Registry search keeps the correct main tool in the top 3 for at least 90% of the search test set under load.
- [ ] The event burst drains with no lost events and nothing in the dead-letter topic.
- [ ] Each run produces a report, and each bottleneck found is filed as an issue.

## 3. Body edits, per apply PR

One worktree per PR. No `ROWS` and no template edit, so the seven PRs do not conflict. Where a box moves between issues in two different PRs, the receiving PR adds it and the giving PR removes it with a pointer; both land before PR-R. A pointer to a row that PR-R adds (CH-9, X-5b) is plain text, not a link, until PR-R.

In each block: **Ticks** paste as they are; **Ticks after the rewrite** paste once the edit in the same block lands; **Tick-blocked** boxes stay open, and the line names the test that would prove them; **Fold** removes the "Status after PoC-N" bullet and puts each fact where the line says; **Edits** are the findings' fixes.

### PR W01: issues 001 to 006 (`docs/backlog-ticks-W01`)

Inputs never applied, as the findings read them (context for this PR):

- Inputs never applied: the PoC-5 owner item "H31 waits on 004 G-2" (`pocs/poc-05-sandboxed/notes/backlog-changes.md`, and 011 section 3) goes to box 6. The PoC-6c line on 003 G-1b ("the name stays") is folded above. The PoC-7 draft and the PoC-9 W2–W5 report name none of 001–006.

#### 001 DEC-1: Resolve the open decisions that block the backlog

Phase E. Size S. Boxes met after the ticks: 2/10.

Ticks:

- `001 DEC-1`, box 8a "The Dapr decision from PoC-4 is recorded" (after the split in W01-07): `· Delivered in PoC-4: pocs/poc-04-stateless-scalable/notes/2026-10-01-dapr-vs-broker.md; ADR-004 Decision 1, accepted 2026-10-08 (a broker client in the chassis behind EventPort)`
- `001 DEC-1`, box "The choices ADR-001 leaves to planning are recorded": `· Delivered in PoC-2, PoC-5, PoC-6b: ValidatingAdmissionPolicy, no Kyverno (ADR-005 item 4; pocs/poc-05-sandboxed/demo/2026-10-09-demo-sandboxed.md section 5); gVisor (pocs/poc-05-sandboxed/notes/2026-10-09-gvisor-overhead.md); no managed runtime, kagent-adk in plain-A2A mode (pocs/poc-06b-bake-off-remote-lane/notes/2026-10-09-kagent-probe.md; ADR-006, AgentCore and Vertex out of scope on 2026-10-09); echo-typescript in the sidecar lane (pocs/poc-02-two-engines-one-contract/tests/test_lanes.py::test_typescript_echo_gives_the_same_stream_as_echo_python); agent-sandbox, one sandbox per call (ADR-005 item 6; pocs/poc-05-sandboxed/notes/2026-10-09-close-runs.md); guardrails per route in open-source LiteLLM, no Enterprise now (ADR-008 item 4 and S10)`

Fold:

- `001 DEC-1`, "Status after PoC-4": tick (box 8a, the Dapr half). The broker product stays open under DEC-1; ADR-004 item 8 already holds the bar a NATS adapter must pass, so the measurement detail leaves the issue body for a link to the Dapr note.

Edits:

- **W01-06** [MEDIUM, rule 14]: rewrite box 6 to "The north star is chosen, with draft targets for its inputs (product plan, open item 1)"; add a box for the trace view (open item 2, ADR-007 open question 4, ADR-009 open question 5); phase E.
- **W01-07** [MEDIUM, rule 1]: split box 8 into 8a (the Dapr decision, ticked) and 8b (each other reuse pick accepted or replaced: suggested, accept Langfuse per ADR-010 item 2, defer lakeFS or DVC to 069 D-7 and the registry base to 076 R-1).
- **W01-08** [MEDIUM, rule 5]: add "Decided in ADR-005 (items 4, 6), ADR-006, and ADR-008 (item 4, S10)" to the bullet and tick the box (Ticks).
- **W01-09** [MEDIUM, rule 5]: rewrite box 3 to "The first cloud is written into 038 X-1a, with the cluster needs PoC-5 proved (gVisor RuntimeClass, a CNI that enforces NetworkPolicy, ValidatingAdmissionPolicy, Kubernetes 1.33 or later)", plus "Decided in ADR-005" for the auth.
- **W01-10** [MEDIUM, rule 13]: move both to `## Out of scope` as "parked with the SLM track"; for the product, the router runs in every deploy stack (`deploy/compose/poc08-litellm/`, `deploy/kind/poc05/platform/`); drop 002 G-1's `suggested: the existing infrastructure` host bullet with it.
- **W01-11** [MEDIUM, rule 13]: keep box 7 for the three live owners; move the GPU and training-data owners to `## Out of scope`, parked.

#### 002 G-1: LiteLLM router in front of all model calls

Phase B. Size M. Boxes met after the ticks: 2/7.

Ticks:

- `002 G-1`, box "The router starts with `docker compose up`" (already ticked; PR-2 fixes the form): `· Delivered in PoC-1: pocs/poc-01-walking-skeleton/tests/test_compose.py::test_chassis_waits_for_a_healthy_router; pocs/poc-01-walking-skeleton/demo/2026-09-29-demo-fake-variant.md`
- `002 G-1`, box 6 as rewritten in W01-05 "Every service calls the router with its own virtual key, minted at deploy": `· Delivered in PoC-5: pocs/poc-05-sandboxed/tests/test_poc05_kind_hardreq1.py::test_litellm_refuses_the_workload_without_the_chassis_key, run in pocs/poc-05-sandboxed/demo/2026-10-09-demo-sandboxed.md; PoC-8: pocs/poc-08-cross-cutting-build/tests/test_poc08_litellm_static.py::test_the_master_key_reaches_litellm_only_and_the_virtual_key_the_chassis_only`

Fold:

- `002 G-1`, "Status after PoC-1": tick (box 1). "Only the OpenAI format; `/v1/messages`, Claude, and Gemini untested" goes to open boxes 2 and 3. The `fake` variant's missing master key is a fact for `deploy/compose/SECURITY.md`, not a box: out of scope.
- `002 G-1`, out-of-scope bullet "Debt from PoC-1" (deadline PoC-5): PoC-5 paid it on kind and PoC-8 on its stack, but the `local` Compose variant still carries it. Goes to `proposals.md` (W01-01).

Edits:

- **W01-05** [MEDIUM, rule 8]: rewrite to "Every service calls the router with its own virtual key, minted at deploy" and tick it with the PoC-5 kind test and the PoC-8 static test (Ticks).
- **W01-12** [MEDIUM, rule 1]: `tick-blocked`; the proving test is a `network` test on the PoC-8 stack that makes a call and greps the LiteLLM container log for the key (suggested: `test_no_provider_key_in_the_router_logs`).
- **W01-13** [MEDIUM, rule 8]: rewrite box 2 to "An OpenAI-format call through the router reaches a big model, streaming and complete; the Anthropic format reaches the router as OpenAI format through the chassis"; it closes with the Mac run (phase B), as does box 3 (one call per provider the product uses).
- **W01-23** [LOW, rule 1]: PR-2 rewrites the line as in Ticks.
- **W01-24** [LOW, rule 4]: keep the dependency (it points to a lower number) and note in CH-4 that the G-1 part it needs is met; the W2b session decides whether to drop it.
- P-3 (platform-security): 002 G-1 box 5 stays open, verdict `conflict`, until P-3 lands.

#### 003 G-1b: Named model routes, swapped in router config only

Phase B. Size S to M. Boxes met after the ticks: 0/6.

Fold:

- `003 G-1b`, "Status after PoC-1": the tool-call flag, route versions, and the unknown-route error stay open boxes. The rename question is moot: PoC-6c keeps `local-small` (`pocs/poc-06c-pretrained-slm/notes/backlog-changes.md`). Drop it.
- `003 G-1b`, "Status after PoC-6": out of scope, a fact. The hosted run on `big-default` is the Mac run in [011-what-is-left.md](../../planning/poc/011-what-is-left.md) section 1.

Edits:

- **W01-14** [MEDIUM, rule 13]: rewrite box 1 to "`big-default` and `local-small` answer through the router by name" (`pocs/poc-01-walking-skeleton/demo/2026-09-29-demo-fake-variant.md` shows both on the fake model; tick after one hosted run), drop the two SLM routes from `## What` as parked, and drop the rename question.
- With the `ROWS` change (W01-15): resize to M in `ROWS`. The swap test differs from `pocs/poc-06c-pretrained-slm/tests/test_poc06c_route_switch.py::test_only_the_route_changes_and_every_task_passes_both_times`, which changes the chassis's route, not the model behind it.

#### 004 G-2: Token and cost counting per request, user, and agent

Phase E. Size M. Boxes met after the ticks: 1/7.

Ticks:

- `004 G-2`, box "A streamed call makes a usage record with real token counts": `· Delivered in PoC-1: pocs/poc-01-walking-skeleton/demo/2026-09-29-demo-fake-variant.md (streamed runs on both routes, 42 and 9 tokens in the router's token_log); packages/chassis/tests/test_metrics.py::test_model_tokens_and_cost_come_from_the_proxy_usage (fake model)`

Fold:

- `004 G-2`, "Status after PoC-1": tick (box 3, streamed counts). The spoofable tag goes to box 6. "Real cost unverified" goes to box 2.
- `004 G-2`, "Status after PoC-5": the uncharged early-closed stream is a fact for `## What` (`_Hold.release`; the abandoned-stream cases in `packages/chassis/tests/test_model_proxy_messages_budget.py`). H31 not run stays open box 6.

Edits:

- **W01-16** [MEDIUM, rule 1]: rewrite to "labeled by agent and route; `user` is in the usage record only"; `tick-blocked` until a dated `pocs/poc-08-cross-cutting-build/notes/<date>-stack-metrics.md` records the run.
- **W01-17** [MEDIUM, rule 10]: keep open; name the proving test, a kind test `test_spoofed_agent_tag_counts_under_key_agent`; take H31 off the PoC-5 owner list in 011 section 3 with a link here.
- **W01-18** [MEDIUM, rule 13]: move box 8 and the SLM-price bullet to `## Out of scope`, parked with 005 G-3.
- **W01-21** [LOW, rule 6]: keep the marker; add "see ADR-008 open questions" so the owner list carries it.

#### 005 G-3: Baseline report: token spend and cost over at least two weeks

Phase parked. Size M. Boxes met after the ticks: 0/6.

Edits:

- With the `ROWS` change (W01-03): `parked` in `ROWS`; 031 G-5 (also SLM) may keep the dependency; rewrite DEC-1 box 6 (W01-06) and park 006 box 5 (W01-04) so no live box waits on it.

#### 006 G-6: Token and cost dashboard with live spend

Phase E. Size M to S. Boxes met after the ticks: 0/4.

Edits:

- **W01-19** [MEDIUM, rule 1]: rewrite to "The PoC-8 stack loads every dashboard with no manual steps"; `tick-blocked` until one dated stack record, which also covers box 1 (`test_every_panel_query_has_data`).
- **W01-20** [LOW, rule 6]: drop the marker and cite PoC-8.
- With the `ROWS` change (W01-04): move boxes 4 and 5 to `## Out of scope` as "parked with the SLM track"; retitle to "Token and cost dashboard with live spend" (suggested); phase E; resize S, from the 4 live boxes (two close with one dated PoC-8 stack run).

### PR W2a: issues 007 to 016 (`docs/backlog-ticks-W2a`)

Inputs never applied, as the findings read them (context for this PR):

- Inputs never applied, named for these issues: the PoC-7 draft's 014 H-7 block (W2a-12) and its 013 CH-2 forced-route line, and the PoC-6c "to apply" line for 013 CH-2 (W2a-24). The PoC-9 w2–w5 report names none of 007–016.

#### 007 H-1: Chassis package and image: ports, `handle` contract, JSON event schema, envelope

Phase A. Size L to S. Boxes met after the ticks: 6/10.

Ticks:

- `007 H-1`, box "The core package imports no web framework": `· Delivered in PoC-1: pocs/poc-01-walking-skeleton/tests/test_day0.py::test_import_rules_hold` (runs the import-linter contract "no product SDK or web framework in core and ports", `pyproject.toml`).
- `007 H-1`, box "`make test` passes in CI from the first commit": `· Delivered in PoC-1: pocs/poc-01-walking-skeleton/tests/test_day0.py::test_fake_profile_reaches_no_socket_when_the_offline_gate_runs`, `::test_fake_profile_sees_no_api_key_when_the_offline_gate_runs`; `scripts/tests/test_strip_secrets.py::test_check_offline_strips_every_credential_suffix`.
- `007 H-1`, box "An echo `handle` function streams": `· Delivered in PoC-1: packages/chassis/tests/test_collector.py::test_echo_streams_and_collects_the_same_text`; `packages/chassis/tests/test_contracts.py::TestFakeEngineHandle::test_stream_starts_and_ends`.
- `007 H-1`, box "A fixture event stream from a non-Python workload": `· Delivered in PoC-2: packages/chassis/tests/test_schemas.py::test_events_from_a_typescript_workload_validate`; `docs/contracts/contract-v1.md#events-eventsv0json`.
- `007 H-1`, box "The envelope models accept the example envelope": `· Delivered in PoC-1: packages/chassis/tests/test_envelope.py::test_epic_example_response_is_accepted`, `::test_unknown_status_is_rejected`.
- `007 H-1`, box "Every response carries the `versions` field": `· Delivered in PoC-1: packages/chassis/tests/test_envelope.py::test_versions_is_required`; `packages/chassis/tests/test_server.py::test_complete_run_returns_a_response_with_versions`; `docs/contracts/contract-v1.md#versions`.

Ticks after the rewrite:

- `007 H-1`, box "Every listed port exists" (after W2a-14): `packages/chassis/tests/test_contracts.py::TestScriptedModel`, `::TestFakeEngineHandle`, `::TestInMemoryTools`, `::TestInMemoryConfig`, `::TestInMemoryTelemetry`; `test_events_contract.py::TestInMemoryBus`; `test_state_contract.py::TestInMemoryState`; `test_contracts_cross_cutting_ports.py::TestStaticTokens`, `::TestKeywordGuardrails`, `::TestScriptedEvaluator`, `::TestInMemoryFeedback` (all under `packages/chassis/tests/`).
- `007 H-1`, the unknown-version half of box 7 (after W2a-15): `packages/chassis/tests/test_events.py::test_unknown_schema_version_is_refused_with_a_clear_error`; `packages/chassis/tests/test_contract_freeze.py::test_both_servers_refuse_an_unknown_schema_version`.

Edits:

- **W2a-14** [MEDIUM, rule 12]: Rename `EnginePort` to `EngineConnector`. Move `RegistryPort` to 076 R-1 and `StorePort` to the data-agent issue, both under `## Out of scope`. Tick box 2 as listed.
- **W2a-15** [MEDIUM, rule 8]: Rewrite to "an unknown `schema_version` is refused with a clear error; when `"1"` ships, `"0"` stays accepted (`docs/contracts/contract-v5.md#c3-what-counts-as-breaking-from-now-on`)". Tick the unknown half.
- P-10 (chassis-architect): 007 H-1: the tick on the import rule stands.

#### 008 H-14: One agent interface for all classes (stateless, orchestrator, data)

Phase later. Size M to L. Boxes met after the ticks: 3/9.

Ticks:

- `008 H-14`, box "`spec.trust: untrusted` with `spec.engine.connector` set to `sidecar` or `inprocess`": `· Delivered in PoC-5: pocs/poc-05-sandboxed/tests/test_poc05_trust_config.py::test_untrusted_outside_the_remote_lane_is_refused`; `docs/contracts/contract-v4.md#trust`.

Ticks after the rewrite:

- `008 H-14` (008a), box "Class, kind, … can be read at run time", reduced to lane, trust, and chassis version (after W2a-08): `packages/chassis/tests/test_manifest.py::test_manifest_matches_the_config_and_the_openapi_spec`, `::test_manifest_agent_trust_shows_untrusted_with_the_remote_lane`.

Fold:

- `008 H-14`, "Status after PoC-2": tick (default lane, `inprocess` refusal, now trust too). Class, kind, and modules: open boxes in 008b.
- `008 H-14`, "Status after PoC-6": eight engines and the frozen `schema_version "0"` move into `## What`, citing contract v5 part C. "Proposed default: PydanticAI" is stale (W2a-17). Hosted numbers: the owner list (Mac run, [what is left](../../planning/poc/011-what-is-left.md) section 1).

Edits:

- C1: Keep the issue whole: no 008a or 008b (a new row would sit after 127, and 040, 072, 076, and 099 need this one). Apply the ticks below; box 8 is reduced to lane, trust, and chassis version, as the after-edit line says.
- C1: Add the box moved from 059 H-15 box 1: "`agentctl new demo --kind <kind>` works for all five kinds, and each result builds and passes the template CI, the stateless check included, with no hand edits." Merge 059 boxes 2 to 4 into boxes 5, 6, and 7 here: each of those gains "in a config `agentctl` generates".
- C1: Phase `later`. Size L (ROWS).
- **W2a-17** [MEDIUM, rule 5]: "Decided in ADR-006 (amended 2026-11-06): LangGraph is the default; PydanticAI stays supported." In 008b, write the module rule against the existing flags, or decide `spec.modules` in a contract change.

#### 009 CH-1: Engine connectors `inprocess` and `sidecar` over A2A, plus the template A2A server that wraps `handle`

Phase A. Size M to S. Boxes met after the ticks: 6/8.

Ticks:

- `009 CH-1`, box "One trace ID links the logs of the chassis and the workload": `· Delivered in PoC-8: pocs/poc-08-cross-cutting-build/tests/test_poc08_workload_logs.py::test_the_workload_logs_json_with_the_chassis_runs_trace_id`, `pocs/poc-08-cross-cutting-build/tests/test_poc08_c3_trace.py::test_every_call_has_metrics_and_json_logs_with_the_trace_id`.

Ticks after the rewrite:

- `009 CH-1`, box "A timeout cancels the A2A task", scoped to `sidecar` (after W2a-05): `packages/chassis/tests/test_a2a_sidecar.py::test_timeout_mid_run_yields_a2a_timeout_and_cancels`; `packages/workload-a2a/tests/test_workload_a2a_server.py::test_cancel_while_the_handle_is_suspended_at_a_yield_is_one_canceled`.

Fold:

- `009 CH-1`, "Status after PoC-2": facts are ticks. `inprocess` cancel: rewrite (W2a-05). JSON-RPC error: a new open box (W2a-18). The `a2a.unsupported_state` message is fixed: drop it. `inprocess` batching: out of scope (accepted for tests and local runs). The weak `test_cancel_is_clean`: a proposal (W2a-18).

Edits:

- C1: Box 3 moves to 024 CH-3 (one box there with 011 box 8 and 025 box 7). Size S (ROWS): one open box left, the JSON-RPC error box.
- P-7 (chassis-architect): 009 CH-1 box 5: scope and tick per the pick.
- P-9 (chassis-architect): 009 CH-1: add the open box "a JSON-RPC error answer from the workload surfaces as an `a2a.*` code"; drop the fixed `a2a.unsupported_state` item.

#### 010 H-12: Config loader: object storage, JSON Schema check, reload on change

Phase A. Size M. Boxes met after the ticks: 8/11.

Ticks:

- `010 H-12`, box "The echo agent starts with its config read from a MinIO bucket": `· Delivered in PoC-4: pocs/poc-04-stateless-scalable/notes/2026-10-01-drills.md` section 4; `pocs/poc-04-stateless-scalable/tests/test_config_minio.py::test_a_store_change_reaches_the_next_request_when_it_comes_from_s3` (`network`).
- `010 H-12`, box "A config that breaks the schema is refused at start": `· Delivered in PoC-4: packages/chassis/tests/test_config_loader.py::test_start_fails_on_an_invalid_document_and_names_the_field_not_the_value`.
- `010 H-12`, box "A valid change is picked up without a restart": `· Delivered in PoC-4: pocs/poc-04-stateless-scalable/tests/test_config_reload.py::test_a_change_takes_effect_on_the_next_request_with_no_restart`, `::test_a_bad_config_is_refused_and_the_last_good_one_stays`; `docs/contracts/contract-v3.md#config`.
- `010 H-12`, box "A `remote` config without `spec.engine.url`": `· Delivered in PoC-5: pocs/poc-05-sandboxed/tests/test_poc05_trust_config.py::test_the_remote_lane_requires_a_url`.

Fold:

- `010 H-12`, "Status after PoC-1" and "after PoC-2": facts are ticks; the MinIO and reload items are now ticked; `risk_class` and `vault://` stay open boxes (W2a-21).
- `010 H-12`, "Status after PoC-4": facts are ticks. Ceilings and `/openapi.json` after a reload: two new open boxes (W2a-20).

Edits:

- **W2a-20** [MEDIUM, rule 10]: Add two open boxes: "a reload that passes a ceiling from the bootstrap file is refused, and the last good config stays (suggested: ceilings for `spec.limits` and `ttl_s`)" and "`/openapi.json` shows the limits in force after a reload, or says it shows the start values". Phase A.
- **W2a-21** [MEDIUM, rule 8]: Box 4: test a bad `risk_class` here and move `human_oversight` and the other E.4 fields to 077 C-1. Box 5: rewrite to "a credential-shaped value anywhere in the config is refused", or send the `vault://` choice to DEC-1.
- P-8 (chassis-architect): 010 H-12 box 11: rewrite per the pick.

#### 011 H-2: Inbound adapters: native, OpenAI-compatible, Anthropic-compatible, with streaming

Phase done. Size L. Boxes met after the ticks: 5/8.

Ticks:

- `011 H-2`, box "The echo agent gives the same output through all three endpoints": `· Delivered in PoC-3: pocs/poc-03-one-interface-every-client/tests/test_interface_contract.py::TestInterfaces::test_every_interface_gives_the_same_answer`, `::TestInterfaces::test_stream_and_complete_agree`; `pocs/poc-03-one-interface-every-client/tests/test_exit_criteria.py::test_exit_2_only_the_two_legitimate_groups_of_cells_skip` (49 of 64 cells run; the 15 skips are MCP stream and `echo-typescript` in `inprocess`).
- `011 H-2`, box "Stock OpenAI and Anthropic client libraries": `· Delivered in PoC-3: pocs/poc-03-one-interface-every-client/tests/test_exit_criteria.py::test_exit_3_the_sdks_work_with_only_a_base_url_change`.
- `011 H-2`, box "An `error` event returns the right error shape": `· Delivered in PoC-3: pocs/poc-03-one-interface-every-client/tests/test_interface_contract.py::TestInterfaces::test_an_error_event_is_the_formats_error`; `docs/contracts/contract-v2.md#status-table`.
- `011 H-2`, box "Token counts appear in the `usage` field": `· Delivered in PoC-3: pocs/poc-03-one-interface-every-client/tests/test_interface_contract.py::TestInterfaces::test_every_cell_answers`.
- `011 H-2`, box "In the `sidecar` lane, `/ready` reports not ready": `· Delivered in PoC-4: packages/chassis/tests/test_readiness.py::test_the_sidecar_probe_is_false_on_a_bad_status_or_a_dead_workload`; `pocs/poc-04-stateless-scalable/tests/test_liveness.py::test_ready_goes_false_when_the_workload_hangs_and_health_stays_ok`.

Ticks after the rewrite:

- `011 H-2`, box "`/ready` reports not ready until …" (after W2a-22): `packages/chassis/tests/test_readiness.py::test_ready_goes_false_after_failed_probes_and_health_stays_ok`, `::test_ready_says_starting_then_ready_then_draining`; `docs/contracts/contract-v3.md#ready-and-health`.
- `011 H-2`, box "On SIGTERM …" (after W2a-04): `packages/chassis/tests/test_shutdown.py::test_sigterm_drains_the_in_flight_stream_after_ready_goes_false`; `pocs/poc-04-stateless-scalable/tests/test_graceful_shutdown.py::test_the_chassis_drains_in_flight_runs_and_ready_goes_false_first`; `pocs/poc-04-stateless-scalable/tests/test_workload_drain.py::test_workload_a2a_finishes_in_flight_handle_on_sigterm`; `docs/contracts/contract-v3.md#shutdown`.

Fold:

- `011 H-2`, "Status after PoC-1": facts are ticks. The readiness registry: rewrite (W2a-22). SIGTERM drain: rewrite (W2a-04).
- `011 H-2`, "Status after PoC-3": facts are ticks. The complete-mode disconnect is fixed (`docs/contracts/contract-v3.md#a-client-that-leaves-a-complete-call`): a fact for `## What`. MCP disconnect: out of scope. `stop`, `stop_sequences`, the message `name`, and unknown Anthropic keys: the owner list, with ADR-003's acceptance (W2a-23).
- 011 section 5 and the PoC-6c list: none names 076–093. The PoC-9 W2–W5 report names none either.
- `011-what-is-left.md` sections 3 and 5: nothing for these issues.
- 011 section 4, "The rare 502 under load (6a follow-up)", about 1 in 4,600 calls at 4 pairs: no issue owns it. It belongs in X-5a as an optional box or `## Out of scope` (W1012-19).

Edits:

- C1: Retire. First line under `## Acceptance criteria`: `Delivered by: PoC-3, PoC-4 (2026-11-DD)`.
- C1: Box 7 follows P-6: rewrite it to the contract, then tick it with the line under "Ticks after a body edit". If the user picks the other option in P-6, box 7 stays open and 011 stays in W2.
- C1: Box 8 moves to 024 CH-3.
- **W2a-22** [MEDIUM, rule 5]: Rewrite to the fixed list, then tick as listed.
- **W2a-23** [MEDIUM, rule 10]: Put the ADR-003 acceptance, with those four items, on the owner list (or DEC-1). Do not hold 011's retirement on it; the behavior is in the contract.
- With the `ROWS` change (W2a-19): Move both boxes to 024 CH-3, which phase A's sidecar-suite CI job closes. Then 011 H-2 retires after W2a-04 and W2a-22, and 009 CH-1 has one box left (W2a-18).
- P-6 (chassis-architect): 011 H-2 box 7: rewrite per the pick and tick; the retirement depends on it.

#### 012 H-3: Model port to the LLM router, with a route map per task

Phase B. Size M to L. Boxes met after the ticks: 2/10.

Ticks after the rewrite:

- `012 H-3`, box "Each model adapter …", reduced to the router and the fake (after W2a-01): `packages/chassis/tests/test_contracts.py::TestLiteLLMModel`, `::TestScriptedModel`.

Fold:

- `012 H-3`, "Status after PoC-1": facts are ticks. vLLM, llama.cpp, and the `cloud` refusal: parked. `latency_ms`, the virtual-key spend test, and temperature: open boxes 4, 5, and 9.

Edits:

- C1: Add three route-map boxes: "`ChassisConfig` holds a route map per task (`ModelSpec.routes`) and a `judge_route`, checked by the schema"; "admission enforces the resolved route and refuses another with 403 `model_route_denied`"; "the route map reloads with no restart". Source: `docs/specs/models.md`, "The route map per task".
- C1: Under `## Out of scope`: "`agentctl` refusing a route the service's key lacks: 059 H-15."
- With the `ROWS` change (W2a-02): Give it to 012 H-3 once W2a-01 lands: three boxes (the map in `ChassisConfig`, admission enforces the resolved route with 403 `model_route_denied`, the map reloads). Resize 012 to L. Check with W01 first; 003 G-1b (named routes) is the other candidate. C2 places it.
- With the `ROWS` change (W2a-01): Move the vLLM and llama.cpp text to `## Out of scope` as "Parked on 2026-11-06 (product plan; models spec, What is parked)". Rewrite box 10 to the router and the fake, then tick it. Retitle in `ROWS` to "Model port to the LLM router". A small model stays reachable as a LiteLLM route (`local-small`).
- P-5 (observability-expert): 012 H-3 box 6 stays open until P-5 is decided.

#### 013 CH-2: Outbound model proxy: an OpenAI- and Anthropic-compatible URL for workloads, forwarded with the service's scoped key

Phase B. Size M. Boxes met after the ticks: 6/8.

Ticks:

- `013 CH-2`, box "A model call with no trace id is served": `· Delivered in PoC-5: packages/chassis/tests/test_model_proxy.py::test_a_call_without_an_in_flight_run_is_served_and_counted`; `packages/chassis/tests/test_uncorrelated_cap.py::test_calls_are_served_until_the_window_has_spent_the_cap`; `docs/contracts/contract-v4.md#the-uncorrelated-cap`.
- `013 CH-2`, box "In the `fake` profile, the proxy answers from the fake": `· Delivered in PoC-1: packages/chassis/tests/test_model_proxy.py::test_complete_maps_the_openai_shape_both_ways`, `::test_stream_ends_with_usage_and_done`.

Fold:

- `013 CH-2`, "Status after PoC-2": facts are ticks. The uncorrelated cap is now built (ticked). The per-call timeout cap: a new open box (W2a-24). The Anthropic format: done in PoC-6, but the stock-SDK half of box 1 is open.
- `013 CH-2`, "Status after PoC-6": facts move into `## What`. No finish reason on `ModelPort`: out of scope, citing ADR-003 item 8.

Edits:

- **W2a-24** [MEDIUM, rule 10]: Add an open box for the timeout cap (with 017 H-4). Fold the forced-route fact and the six-key rule into `## What`. Point the two debt bullets at 026 CH-4, with no deadline.

#### 014 H-7: Observability: logs, traces, metrics, Langfuse

Phase E. Size S to M. Boxes met after the ticks: 6/8.

Ticks:

- `014 H-7`, box "Every log line from the echo agent is JSON": `· Delivered in PoC-8: packages/chassis/tests/test_json_logs.py::test_inside_a_run_a_line_carries_the_request_id_and_a_32_hex_trace_id`; `pocs/poc-08-cross-cutting-build/tests/test_poc08_workload_logs.py::test_the_workload_logs_json_with_the_chassis_runs_trace_id`.
- `014 H-7`, box "In the `sidecar` lane, spans from the chassis and the workload": `· Delivered in PoC-7: pocs/poc-07-cross-cutting-decisions/tests/test_poc07_obs_trace_join.py::test_one_trace_joins_chassis_workload_framework_model_and_tool`; `pocs/poc-08-cross-cutting-build/tests/test_poc08_c3_trace.py::test_a_tool_call_has_one_trace_across_run_model_and_tool_spans`.
- `014 H-7`, box "A `traceparent` header from the caller is continued": `· Delivered in PoC-7: packages/chassis/tests/test_otel_telemetry.py::test_traceparent_continues_the_callers_trace`; `packages/chassis/tests/test_otel_wiring.py::test_run_span_continues_the_callers_traceparent_and_names_the_session`.
- `014 H-7`, box "`GET /metrics` returns Prometheus metrics": `· Delivered in PoC-8: packages/chassis/tests/test_metrics.py::test_a_run_is_counted_by_interface_and_status_with_its_latency`, `::test_a_run_error_is_counted_by_code`, `::test_model_tokens_and_cost_come_from_the_proxy_usage`; `docs/contracts/contract-v7.md#a-get-metrics`.
- `014 H-7`, box "The core package has no OpenTelemetry import": `· Delivered in PoC-7: pocs/poc-01-walking-skeleton/tests/test_day0.py::test_import_rules_hold` (`opentelemetry` is forbidden in `chassis.core` and `chassis.ports`, and allowed only under `chassis.adapters`, `pyproject.toml`).
- `014 H-7`, box "Tests assert on spans with the in-memory span exporter": `· Delivered in PoC-7: pocs/poc-07-cross-cutting-decisions/tests/test_poc07_obs_telemetry_swap.py::test_switching_the_backend_is_exporter_config_only`; `packages/chassis/tests/test_otel_telemetry.py::test_traceparent_continues_the_callers_trace`.

Ticks after the rewrite:

- `014 H-7`, box "One call shows as one trace …" with the Collector's backend in place of Tempo (after W2a-11): `pocs/poc-08-cross-cutting-build/tests/test_poc08_stack_litellm.py::test_one_run_is_one_trace_with_a_litellm_span_under_the_model_proxy` (`network`); `pocs/poc-08-cross-cutting-build/notes/2026-10-30-stack-drills.md` (CI run 41011333394, passed).

Fold:

- `014 H-7`, "Proposed in PoC-7": rewrite to "Decided in ADR-007" (accepted 2026-10-30) (W2a-12).
- `014 H-7`, "Status after PoC-8": facts are ticks; the two "waits on run" notes are replaced by the stack-drills note (W2a-13). Sampling: an open box (phase E). `/metrics` with no token (PoC-8 Q1), the feedback panel (Q4), Presidio (Q6): DEC-1.

Edits:

- C1: Add two boxes from the product plan's phase E: "the guard stage emits its own span under the run span" and "the OTel SDK's CPU and latency cost is measured on the PoC-8 stack and recorded in a dated note". With the sampling box, size M (ROWS).
- **W2a-11** [MEDIUM, rule 5]: Rewrite to "one trace in the Collector's trace backend (Jaeger in CI, Tempo in production; Decided in ADR-007)". Tick as listed. Also fold the Compose list (Prometheus, Grafana, Loki, Tempo, Langfuse) in `## What` into the ADR-007 shape.
- **W2a-12** [MEDIUM, rule 10]: Replace the bullet with "Decided in ADR-007 (accepted 2026-10-30; item 10 amended 2026-11-06)", and fold the PoC-7 draft block in as facts and open boxes.
- **W2a-13** [MEDIUM, rule 10]: Cite the stack-drills note. Add an open box for sampling as a Collector policy (ADR-007 item 8; phase E). Send PoC-8 Q1, Q4, and Q6 to DEC-1.

#### 015 H-8: Testing kit: fake adapters, contract tests per API, record and replay

Phase F. Size L to M. Boxes met after the ticks: 4/10.

Ticks:

- `015 H-8`, box "The echo agent passes the contract suite for all three APIs": `· Delivered in PoC-3: pocs/poc-03-one-interface-every-client/tests/test_exit_criteria.py::test_exit_2_only_the_two_legitimate_groups_of_cells_skip`; `pocs/poc-03-one-interface-every-client/tests/test_interface_contract.py::TestInterfaces`.
- `015 H-8`, box "A recorded model call replays with no network": `· Delivered in PoC-3: packages/contract-suites/tests/test_recording.py::test_streamed_record_then_replay_gives_the_same_bytes_frame_by_frame`, `::test_replay_never_calls_inner_even_on_a_miss`.
- `015 H-8`, box "Recordings contain no API keys": `· Delivered in PoC-3: packages/chassis/tests/test_recorded_model.py::test_cassettes_hold_no_key`; `packages/contract-suites/tests/test_recording.py::test_save_raises_when_a_key_shaped_string_survives_the_scrub`.
- `015 H-8`, box "An engine that calls the model over HTTP runs offline": `· Delivered in PoC-2 and PoC-6: pocs/poc-02-two-engines-one-contract/tests/test_engines.py::test_python_engine_passes_offline_against_the_fake_model_server` (streaming); `pocs/poc-06a-bake-off-sidecar-lane/tests/test_poc06a_spec.py::test_the_lookup_task_calls_both_tools_in_order` (tool call).

Ticks after the rewrite:

- `015 H-8`, box "The whole suite runs with one command …" with a handle or endpoint in place of an image (after W2a-25): `packages/agent-testkit/tests/test_agent_testkit_isolation.py::test_the_kit_imports_with_the_chassis_unimportable`.

Fold:

- `015 H-8`, "Status after PoC-3": facts are ticks. Query-string filtering and key shapes other than `sk-` are fixed: drop them. The record-host guard: an open box. The vcrpy pin, the three socket runners, and the gate time: out of scope (W2a-27).

Edits:

- **W2a-10** [MEDIUM, rule 14]: Add a box to 015 H-8: "In the `fake` profile, a test scripts the model's answer per case, with no chassis import."
- **W2a-25** [MEDIUM, rule 5]: Rewrite to "against a workload, by handle or by A2A endpoint". Tick as listed.
- **W2a-26** [MEDIUM, rule 12]: Reword to "the suite runs in the `check` job on every PR" and tick it, or move the release half to 025 H-10.

#### 016 H-9: Local debug profile: Docker Compose, hot reload, debugger port

Phase F. Size S. Boxes met after the ticks: 6/9.

Ticks:

- `016 H-9`, box "The workload's A2A port is not published": `· Delivered in PoC-9: pocs/poc-09-agent-mvp-template/tests/test_poc09_template_ops.py::test_workload_is_hardened_keyless_and_unpublished`, `::test_every_published_port_is_on_loopback_and_only_on_the_chassis`.
- `016 H-9`, box "The chassis reads the config from MinIO": `· Delivered in PoC-4: pocs/poc-04-stateless-scalable/notes/2026-10-01-drills.md` section 4 (the Compose scale stack, through the echo workload).
- `016 H-9`, box "A debugger attaches to each container": `· Delivered in PoC-9: pocs/poc-09-agent-mvp-template/notes/2026-11-05-machine-checks.md` section 2 (breakpoints on 5678 and 5679); `pocs/poc-09-agent-mvp-template/tests/test_poc09_template_ops.py::test_a_debugger_is_attached_to_each_container`.
- `016 H-9`, box "One trace ID links the logs of both containers": `· Delivered in PoC-8:` the same two tests as the 009 CH-1 tick. The workload runs as a thread in those tests, not as its own container.
- `016 H-9`, box "Secrets from `.env` are set only in the chassis container": `· Delivered in PoC-2 and PoC-9: pocs/poc-02-two-engines-one-contract/demo/2026-10-01-demo-sidecar.md` ("key-like variables in the workload: none"); `pocs/poc-09-agent-mvp-template/notes/2026-11-05-machine-checks.md` section 3.
- `016 H-9`, box "No real secret is in the repo": `· Delivered in PoC-8: scripts/tests/test_check_secrets.py::test_check_secrets_fails_when_the_hook_finds_a_new_secret`; CI job `lint-extra` in `.github/workflows/ci.yml` runs `scripts/check_secrets.sh` on every push.

Edits:

- **W2a-28** [MEDIUM, rule 13]: Park box 8. Rewrite box 1 to the template stack ("`docker compose up` from a generated repo starts the chassis and the workload"), and move Postgres to the first issue that needs it. Box 4 (hot reload) stays open; phase F.

### PR W2b: issues 017 to 026 (`docs/backlog-ticks-W2b`)

Inputs never applied, as the findings read them (context for this PR):

- PoC-7 draft `backlog-changes.md`: no issue in 017–026 has a "Status after PoC-7" bullet. The draft's facts for 017, 022, and 023 are folded above. Its open item for 022 ("a test that a workload's `guardrails` key never reaches LiteLLM") becomes an open box in 022 H-6.
- PoC-9 `2026-10-30-w2-w5-report.md`: nothing on 017–024 or 026. Its Copier and Helm items are folded into 024 CH-3 and 025 H-10 above.

#### 017 H-4: Harness features: schema check, evaluator gate, retry, fallback, timeout, budget

Phase B. Size L to M. Boxes met after the ticks: 6/8.

Ticks:

- `017 H-4`, box "With `retries: 1` and a fake evaluator": `· Delivered in PoC-7, PoC-8: packages/chassis/tests/test_evaluator_gate.py::test_a_failing_answer_retries_once_then_falls_back, pocs/poc-07-cross-cutting-decisions/tests/test_poc07_evals_gate.py::test_poc07_evals_gate_retries_once_then_falls_back (sidecar), pocs/poc-08-cross-cutting-build/tests/test_poc08_c6_gate.py::test_the_gate_retries_then_falls_back_to_the_configured_route (every lane)`
- `017 H-4`, box "A call that runs past `timeout_ms`": `· Delivered in PoC-4: pocs/poc-04-stateless-scalable/tests/test_timeout_budget_per_engine.py::test_past_timeout_ms_the_run_ends_with_a2a_timeout_and_the_task_is_cancelled, packages/chassis/tests/test_a2a_sidecar.py::test_timeout_mid_run_yields_a2a_timeout_and_cancels`
- `017 H-4`, box "A call that would go past `budget.max_tokens`": `· Delivered in PoC-4: pocs/poc-04-stateless-scalable/tests/test_timeout_budget_per_engine.py::test_a_spent_budget_refuses_the_next_model_call_with_budget_exhausted, packages/chassis/tests/test_model_proxy.py::test_an_exhausted_run_is_refused_with_429_complete_and_streamed`
- `017 H-4`, box "A workload that ignores `ctx.budget`": `· Delivered in PoC-4, PoC-5: pocs/poc-05-sandboxed/tests/test_poc05_hostile_offline.py::test_h29_past_the_run_budget_is_429_and_a_run_with_budget_left_is_200; no workload reads ctx.budget.max_tokens`
- `017 H-4`, box "Retries and fallbacks show in metrics": `· Delivered in PoC-8: packages/chassis/tests/test_metrics.py::test_gated_runs_and_fallbacks_are_both_counted, docs/contracts/contract-v7.md#a3-the-families (chassis_gate_fallbacks_total over gate_runs)`
- `017 H-4`, box "With `evaluator_gate` off": `· Delivered in PoC-7: packages/chassis/tests/test_evaluator_gate.py::test_gate_off_makes_no_evaluator_call_and_does_not_wrap, pocs/poc-07-cross-cutting-decisions/tests/test_poc07_evals_gate.py::test_poc07_evals_gate_off_makes_no_evaluator_call`

Fold:

- `017 H-4`, "Status after PoC-4": tick (boxes 3, 4, 5). The `http_429` against `budget_exhausted` finding goes to proposals (W2b-12). The TypeScript strict xfail: out of scope (the TypeScript echo has no tool loop).
- `017 H-4`, "Proposed in PoC-7": tick (boxes 2, 7). Fold to one "Decided in ADR-010" sentence; drop "the user accepts it at the PoC-7 pull request" (ADR-010 is accepted).
- `017 H-4`, "Status after PoC-8": tick (box 2, every lane). Timeout across attempts and judge tokens in `budget.max_tokens`: new open box (W2b-05). Default mode and sample rate: DEC-1 (ADR-010 open question; phase B's "done when" settles it).
- `017 H-4`, the "Open concern" paragraph in `## Why`: out of scope. ADR-010 settled it: sample rate by request id, async mode, the judge's tokens as one more `metrics` event.

Edits:

- C1: Box 1 and the `harness.timeout_ms` and `harness.budget` bullets move to `## Out of scope` (not in `HarnessSpec`); see the W2b-05 text below.
- **W2b-27** [LOW, rule 6, 5]: drop the `suggested:` marker and cite ADR-010; replace the paragraph with "Decided in ADR-010; the default mode and sample rate are confirmed by phase B's judge numbers".
- With the `ROWS` change (W2b-05): add box "A scripted model error is retried up to `retries` times, then falls back to `fallback_route`, in both lanes, and the retry and fallback are counted"; add box "Across attempts the run keeps one `timeout_ms` and one `max_tokens`: each attempt gets what is left, and the judge's tokens count against the budget" (open from PoC-7, `evaluator_gate.py:45-47`); move box 1 and the `harness.timeout_ms` and `harness.budget` bullets (not in `HarnessSpec`) to Out of scope; resize L to M (two open boxes plus box 8).
- P-1 (eval-expert): 017 H-4 gets one sentence: "An evaluator outage fails open as built (ADR-010 item 4); P-1 decides." 042 A-2 box 5 stays open until P-1 is decided.
- P-11 (chassis-architect): 017 H-4: under `## Out of scope`, "the budget error code: P-11".

#### 018 H-18: Idempotency: key check, optional Valkey result cache, key passed to write tools

Phase A. Size M. Boxes met after the ticks: 7/8.

Ticks:

- `018 H-18`, box "Two calls with the same key and input": `· Delivered in PoC-4: pocs/poc-04-stateless-scalable/tests/test_idempotency_replicas.py::test_a_repeated_key_returns_the_same_result_on_any_replica, ::test_a_repeated_key_makes_no_second_model_call`
- `018 H-18`, box "The same key with a different input": `· Delivered in PoC-4: pocs/poc-04-stateless-scalable/tests/test_idempotency_replicas.py::test_same_key_other_input_is_a_conflict (422)`
- `018 H-18`, box "Two concurrent calls with the same key": `· Delivered in PoC-4: pocs/poc-04-stateless-scalable/tests/test_idempotency_replicas.py::test_concurrent_duplicates_run_once, pocs/poc-04-stateless-scalable/tests/test_idempotency_valkey.py::test_concurrent_duplicates_run_once_through_valkey`
- `018 H-18`, box "A cached result is returned as a stream": `· Delivered in PoC-4: pocs/poc-04-stateless-scalable/tests/test_idempotency_replicas.py::test_a_cached_result_replays_as_a_stream`
- `018 H-18`, box "Only the chassis is given the Valkey credential": `· Delivered in PoC-4, PoC-5: pocs/poc-04-stateless-scalable/tests/test_read_only_compose.py::test_no_key_in_any_workload_env, pocs/poc-05-sandboxed/tests/test_poc05_kind_hardreq1.py::test_valkey_refuses_the_workload_without_the_chassis_password (pocs/poc-05-sandboxed/notes/2026-10-08-sidecar-suite.md)`
- `018 H-18`, box "Cache entries expire": `· Delivered in PoC-4: pocs/poc-04-stateless-scalable/tests/test_idempotency_replicas.py::test_the_cache_expires_after_ttl, packages/chassis/tests/test_idempotency.py::test_a_cached_result_lives_ttl_s`
- `018 H-18`, box "The Valkey adapter and the in-memory fake": `· Delivered in PoC-4: packages/chassis/tests/test_state_contract.py::TestInMemoryState, packages/chassis/tests/integration/test_valkey_state_contract.py::TestValkeyState, packages/chassis/tests/test_profiles.py::test_overrides_pick_the_adapter_per_port`

Fold:

- `018 H-18`, "Status after PoC-4": tick (boxes 1 to 4, 6 to 8). The principal in the store key and fingerprint: new open box. Per-caller cap on new keys: open box in 022 H-6. `ttl_s` ceiling: built (`TTL_S_MAX`, `config.py:218`), a fact. Default `ttl_s` 3600 and `max_entry_bytes` 65536: DEC-1 (built as 86400 and 1 MiB). TLS to Valkey: out of scope (038 X-1a). Memory alert: out of scope (117 X-3).

Edits:

- **W2b-10** [MEDIUM, rule 1, 10]: box 5 is `tick-blocked` until a sidecar-lane test sends `Idempotency-Key` and asserts the echo workload returns it from `ctx`; add box "With auth on, the store key and the fingerprint include the principal: the same key from two callers runs twice".

#### 019 H-17: Event port: one-way CloudEvents result events to the broker

Phase later. Size M. Boxes met after the ticks: 5/9.

Ticks:

- `019 H-17`, box "With `result_events` on": `· Delivered in PoC-4: packages/chassis/tests/test_result_events.py::test_a_completed_run_publishes_one_valid_cloudevent, ::test_a_failed_run_publishes_the_failed_type`
- `019 H-17`, box "A broker outage does not fail": `· Delivered in PoC-4: packages/chassis/tests/test_result_events.py::test_a_failing_broker_does_not_fail_the_run (chassis.events.publish_failed)`
- `019 H-17`, box "The core has no broker client import": `· Delivered in PoC-4, PoC-5: pocs/poc-05-sandboxed/tests/test_poc05_events_agnostic.py::test_the_events_port_and_core_import_no_broker_client_in_source, pocs/poc-04-stateless-scalable/tests/test_swap_drill.py::test_event_port_swaps_by_config_only`
- `019 H-17`, box "The broker client adapter and the in-memory bus": `· Delivered in PoC-4: packages/chassis/tests/test_events_contract.py::TestInMemoryBus, packages/chassis/tests/integration/test_kafka_events_contract.py::TestKafkaEvents, pocs/poc-04-stateless-scalable/tests/test_swap_drill.py::test_event_port_swaps_by_config_only`
- `019 H-17`, box "The workload container is given no broker credential": `· Delivered in PoC-4, PoC-5: pocs/poc-05-sandboxed/tests/test_poc05_kafka_static.py::test_the_secret_reaches_only_the_chassis_container_of_the_events_agent, pocs/poc-05-sandboxed/tests/test_poc05_kind_hardreq1.py::test_kafka_refuses_the_workload_without_the_chassis_credential (pocs/poc-05-sandboxed/notes/2026-10-09-close-runs.md); no Dapr in any profile (ADR-004)`

Fold:

- `019 H-17`, "Status after PoC-4": tick (boxes 1, 4, 5, 7, 8). The DLQ bug: proposals (W2b-13). `input` opt-in: open box. `spec.events.consume` refused: out of scope (021 H-20).

Edits:

- C1: Add the live boxes from 043 A-3 (parked): "one result event per call on the native, OpenAI, and Anthropic interfaces" (tick-blocked: a counting test over the three interfaces) and "the model-source field of a result event is named once and documented (suggested: `original_model`)". The `traceparent` and `modelroute` box added here covers 043's second live box.
- C1: Carry 043's tick-blocked notes with them: box 1 (`test_result_events.py::test_a_completed_run_publishes_one_valid_cloudevent` covers native only), box 3 (the replay half, `test_result_events.py::test_a_replayed_idempotent_call_...`), box 4 (the PoC-5 secret check already ticked here).
- **W2b-11** [MEDIUM, rule 5]: rewrite box 3 to "matches `key_hash` of the call's key" with [contract v3](../../contracts/contract-v3.md#result-events) as the source; replace the two `## What` bullets with "Decided in contract v3"; add a box that checks `traceparent` and `modelroute` on a real result event (box 2 today checks only two extensions).
- **W2b-14** [MEDIUM, rule 10]: add box "`input` is left out of a result event unless `spec.events.include_input` is true (suggested name)".

#### 020 X-8: Event broker in Docker Compose and Kubernetes, with retention and consumer-lag alerts

Phase later. Size M to L. Boxes met after the ticks: 1/7.

Ticks:

- `020 X-8`, box "`docker compose up` starts the broker": `· Delivered in PoC-4: pocs/poc-04-stateless-scalable/notes/2026-10-01-dapr-vs-broker.md (kafka row: 2,134 of 2,134 result events, all 3 runs, scale stack)`

Fold:

- `020 X-8`, "Status after PoC-4": open boxes (topics from config, ACLs). Compose PLAINTEXT: out of scope (test-only stack, product plan "What is reused"); TLS and the controller listener: open boxes.
- `020 X-8`, "Status after PoC-5": open box 7 keeps only ACLs; the no-credential half is the 026 CH-4 tick. "Result events must not carry caller data on a shared broker until topic ACLs": joins the `input` opt-in box in 019 H-17.

Edits:

- With the `ROWS` change (W2b-15): resize M to L; rewrite box 2 to "installs on a Kubernetes cluster by the method DEC-1 picks (Strimzi, a managed service, or a chart)"; rewrite box 6 to "events with one partition key", as contract v3 decided.

#### 021 H-20: Event consumer adapter: agents started by CloudEvents, same core as REST

Phase later. Size S to M. Boxes met after the ticks: 0/8.

Edits:

- With the `ROWS` change (W2b-16): resize S to M; phase `later` (no product phase starts runs from events).

#### 022 H-6: Security middleware: auth, scopes, rate limits, PII redaction

Phase E. Size M. Boxes met after the ticks: 5/11.

Ticks:

- `022 H-6`, box "A valid caller without the needed scope": `· Delivered in PoC-7: packages/chassis/tests/test_security_auth.py::test_run_scope_is_needed_on_every_run_interface, ::test_read_scope_is_needed_for_the_manifest_and_docs (403 forbidden, contract v6 D.4)`
- `022 H-6`, box "An input over the size limit": `· Delivered in PoC-3: packages/chassis/tests/test_limits.py::test_oversized_body_is_413_before_validation, ::test_oversized_streamed_body_with_no_length_is_413`
- `022 H-6`, box "No secret value appears in config": `· Delivered in PoC-5, PoC-8: pocs/poc-08-cross-cutting-build/tests/test_poc08_litellm_static.py::test_no_key_literal_in_any_file, packages/chassis/tests/test_no_key_leak.py::test_a_client_key_never_leaks_from_any_interface`
- `022 H-6`, box "In the `sidecar` lane, the workload container holds no secret": `· Delivered in PoC-5: pocs/poc-05-sandboxed/tests/test_poc05_kind_sidecar_controls.py::test_h25_no_secret_value_reaches_the_workload, ::test_h26_chassis_environ_not_readable_from_the_workload (pocs/poc-05-sandboxed/notes/2026-10-08-sidecar-suite.md, "6 passed")`
- `022 H-6`, box "`AuthPort` and `GuardrailPort` each have a fake": `· Delivered in PoC-7: packages/chassis/tests/test_contracts_cross_cutting_ports.py::TestStaticTokens, ::TestKeywordGuardrails, packages/chassis/tests/test_oidc_adapter.py::TestOidcAuth, packages/chassis/tests/test_litellm_guardrails.py::TestLiteLLMGuardrails (against a LiteLLM stub)`

Fold:

- `022 H-6`, "Status after PoC-3": tick (box 4). Content parts per message, the shared event loop, raw exception text in logs and spans: owner list (no product phase names them). Public `/manifest`, `/openapi.json`, `/docs`: settled by the read scope (`test_read_scope_is_needed_for_the_manifest_and_docs`).
- `022 H-6`, "Status after PoC-4": open box (cap new idempotency keys per caller; the rate limit is a rate, not a cap, `rate_limit.py:20-21`).
- `022 H-6`, "Status after PoC-5": out of scope (per-replica uncorrelated cap; LiteLLM's key budget is the cluster cap, contract v4 Known gaps). The key-leak canary suffix and hash: open box, folded into box 9's evidence list once a test checks them.
- `022 H-6`, "Proposed in PoC-7": tick (boxes 2, 11). Fold to "Decided in ADR-008"; the 403 code is `forbidden` (contract v6 D.4), not `insufficient_scope`.
- `022 H-6`, "Status after PoC-8": tick (box 9 evidence). URL allow-list default (Q2) and rate limits with auth off (Q7): DEC-1. Prompt Guard 2 rerun: box 8, waits on the Mac run (011, section 1).

Edits:

- **W2b-17** [MEDIUM, rule 1]: `tick-blocked` until a stack test runs two chassis processes on one Valkey and shows one bucket.
- **W2b-18** [MEDIUM, rule 8]: move box 5 to Out of scope, to the orchestrator wave (W89 session to name the issue).
- **W2b-19** [MEDIUM, rule 1, 10]: all three `tick-blocked`; box 6 waits on Q6 (DEC-1); box 7 joins phase E's "the OTel SDK's cost unmeasured" measurement; box 8 waits on the Mac run (011, section 1).
- **W2b-20** [MEDIUM, rule 10]: add both as boxes.
- P-2 (platform-security): 022 H-6: add the box in the pick; it stays open until P-2 is decided.
- P-4 (platform-security): 022 H-6 box 1 splits: "OIDC bearer tokens on every run and read route" (ticks after P-4) and "caller API keys" (its own open box).

#### 023 C-5: AI-generated marking on outputs and a disclosure flag for client apps

Phase later. Size S. Boxes met after the ticks: 5/8.

Ticks:

- `023 C-5`, box "An agent with `risk_class: limited`": `· Delivered in PoC-7, PoC-8: packages/chassis/tests/test_marking.py::test_limited_marks_every_interface_streaming_and_complete, packages/chassis/tests/test_marking_body.py::test_the_openai_body_carries_the_flags_complete_and_on_every_chunk, ::test_the_anthropic_body_carries_the_flags_complete_and_on_message_start (headers everywhere; ai_marking body field in OpenAI and Anthropic, contract-v7.md#e1-the-body-field)`
- `023 C-5`, box "A `limited` or `high` agent with `ai_generated_marking: false`": `· Delivered in PoC-7: packages/chassis/tests/test_marking.py::test_marking_false_is_refused_for_limited_and_high`
- `023 C-5`, box "A `minimal` agent with `ai_generated_marking: true`": `· Delivered in PoC-7: packages/chassis/tests/test_marking.py::test_minimal_marks_only_when_asked`
- `023 C-5`, box "Result events carry the same": `· Delivered in PoC-8: packages/chassis/tests/test_marking_body.py::test_result_events_carry_the_aigenerated_extension, docs/contracts/contract-v7.md#e2-the-cloudevent-extension`
- `023 C-5`, box "The marking holds in the `inprocess` and `sidecar` lanes": `· Delivered in PoC-7: packages/chassis/tests/test_marking.py::test_inprocess_and_sidecar_give_the_same_flags`

Fold:

- `023 C-5`, "Proposed in PoC-7": tick (boxes 1, 2, 3, 7). Fold to "Decided in ADR-008".
- `023 C-5`, "Status after PoC-8": tick (boxes 1, 5). The older-consumer item: new open box, with Q3 to DEC-1. Legal on `minimal` with marking on: owner list (legal).

Edits:

- **W2b-21** [MEDIUM, rule 5, 10]: rewrite boxes 1, 5, and 8 to the built names with "Decided in ADR-008 and contract v7 E"; add box "a consumer on a chassis older than PoC-8 reads an event with `aigenerated`" and send Q3 (`extra="forbid"`, `ports/events.py:28`) to DEC-1; box 8 adds the body field.
- **W2b-22** [MEDIUM, rule 14]: phase `later` per rule 14, with a note for the consolidation session: the user decides whether the guide (an S task) joins phase A before 2026-12-02.

#### 024 CH-3: Shared Helm library chart: chassis sidecar with a pinned tag, secrets on the chassis only, Service on the chassis port

Phase A. Size M to L. Boxes met after the ticks: 3/9.

Ticks:

- `024 CH-3`, box "A chart that includes the library chart runs a pod": `· Delivered in PoC-9: pocs/poc-09-agent-mvp-template/tests/test_poc09_chart.py::test_sidecar_lane_renders_one_pod_with_the_chassis_and_the_workload, pocs/poc-09-agent-mvp-template/notes/2026-11-05-kind-hpa-drill.md (two pods, each 2/2 Running)`
- `024 CH-3`, box "No service account token is mounted": `· Delivered in PoC-5, PoC-9: pocs/poc-09-agent-mvp-template/notes/2026-11-05-kind-hpa-drill.md ("In the workload container: no service account token"), pocs/poc-05-sandboxed/tests/test_poc05_kind_sidecar_controls.py::test_h02_no_service_account_token_in_the_workload`
- `024 CH-3`, box "The chart runs on a local kind or k3d cluster": `· Delivered in PoC-9: pocs/poc-09-agent-mvp-template/notes/2026-11-05-kind-hpa-drill.md (sidecar lane, chassis as the native sidecar)`

Fold:

- `024 CH-3`, "Status after PoC-4": drain delay, grace period, exec probes: keep as `## What` values, cite the container-roles note. The container order: drift (W2b-06). Valkey sizing: out of scope (122 X-5).
- `024 CH-3`, "Status after PoC-5": the container order: tick-blocked box 8 (W2b-06). `podPidsLimit`: out of scope (a node setting, 038 X-1a). The deployer and pod-create: out of scope (055 CH-6, ADR-005).
- `024 CH-3`, "Status after PoC-9": tick (boxes 1, 9). "Rendered only" is stale. Admission rule 8 (image source): new open box in 026 CH-4 (W2b-02).

Edits:

- C1: Receives one box from 009 box 3, 011 box 8, and 025 box 7, which ask the same thing: "The workload port cannot be reached from another pod; only the chassis port is in the Service". Today only the bind refusal is tested (`packages/workload-a2a/tests/test_workload_a2a_server.py::test_non_loopback_host_is_refused`); phase A's sidecar-suite CI job closes it.
- C1: 025 box 6 (a chassis tag change restarts the services) is the same as this issue's tag-roll box; 025 points here.
- **W2b-06** [HIGH, rule 5, 1]: replace the bullet with "Decided in contract v4 (Bind order): the chassis is the native sidecar"; keep boxes 7 and 8 open until they run on the chart on kind; carry the drain delay and grace values with the container-roles note as their source.
- **W2b-25** [MEDIUM, rule 14]: in 026 CH-4, replace the deny-list bullet with "Decided in the chart: no `ipBlock`, only named peers, so the metadata service and the API server are never reachable" and keep the static check `pocs/poc-05-sandboxed/tests/test_poc05_netpol_static.py::test_poc05_netpol_no_rule_can_reach_metadata_or_api` as its evidence once it runs against `agent-lib`.
- With the `ROWS` change (W2b-23): tick boxes 1, 6, 9; resize M to L; rewrite box 4 to "on a CNI that enforces NetworkPolicy"; the README line goes to the docs owner (outside `docs/planning/`).

#### 025 H-10: Service template: workload Dockerfile, Helm chart on the chassis library chart, CI (tests, signing, deploy, registration)

Phase F. Size L to M. Boxes met after the ticks: 1/10.

Ticks:

- `025 H-10`, box "A new agent repo made from the template passes CI": `· Delivered in PoC-9: pocs/poc-09-agent-mvp-template/tests/test_poc09_scaffold.py::test_c4_ci_steps_pass_from_a_fresh_clone, pocs/poc-09-agent-mvp-template/notes/2026-10-30-open-items.md (hosted run 41087571278, all eight jobs)`

Fold:

- `025 H-10`, "Status after PoC-2": fact (the template depends on `workload-a2a`). "The Python template server cannot interrupt `handle` mid-await": owner list.
- `025 H-10`, "Status after PoC-6": fact (TypeScript bearer flag). Hand-kept `trustedRepositories`: out of scope (055 CH-6).
- `025 H-10`, "Status after PoC-9": tick (box 10). Kyverno, Argo CD, the cluster run: the `later` half of the split (W2b-08). Publishing the platform packages: new open box (phase F).

Edits:

- C1: Re-scope, not split. Keep boxes 1, 8, and 10 (box 10 ticks). Box 2 keeps "CI signs the workload image"; its second half, "the cluster refuses an unsigned image", moves to 038 X-1a.
- C1: Add two boxes: "the generated repo carries a chart on `agent-lib`" and "the template pins a published platform release, not the `.platform` link".
- C1: Move to `## Out of scope`, each with its pointer: box 3 and box 4 (Argo CD and the in-cluster interfaces: 038 X-1a), box 5 (event start in the cluster: 021 H-20 and 019 H-17), box 6 (tag roll: 024 CH-3), box 7 (moved to 024 CH-3), box 9 (registration is by label, through 083 R-2).
- C1: Phase F. Size M (ROWS).

#### 026 CH-4: Chassis-only credentials for every internal service, and default-deny egress per workload pod

Phase A. Size M to L. Boxes met after the ticks: 1/6.

Ticks:

- `026 CH-4`, box "From the workload container, calls to LiteLLM": `· Delivered in PoC-5: pocs/poc-05-sandboxed/tests/test_poc05_kind_hardreq1.py::test_litellm_refuses_the_workload_without_the_chassis_key, ::test_mcp_gateway_refuses_the_workload_without_the_chassis_key, ::test_valkey_refuses_the_workload_without_the_chassis_password, ::test_minio_refuses_the_workload_and_any_unsigned_call, ::test_kafka_refuses_the_workload_without_the_chassis_credential (pocs/poc-05-sandboxed/notes/2026-10-08-sidecar-suite.md "4 passed", pocs/poc-05-sandboxed/notes/2026-10-09-close-runs.md "1 passed")`

Fold:

- `026 CH-4`, "Status after PoC-1" and "PoC-2": tick (box 1 is the kind successor). `llama-cpp` with no key: settled by the PoC-5 kind suite (every model call needs the chassis key). The per-run credential: DEC-1 (suggested). The hostile cases for the public interfaces: open box 4.
- `026 CH-4`, "Status after PoC-3": open box 3 (per-key tool grants). The PoC-3 MCP demo listing 0 tools: owner list (rerun with a minted key).
- `026 CH-4`, "Status after PoC-4": open box 3 (one Valkey password; per-service user). Compose egress: out of scope (kind is the check). Dapr ports: out of scope (Dapr is parked).
- `026 CH-4`, "Status after PoC-5": tick (box 1). Cloud metadata on a real cloud: out of scope (038 X-1a). DNS in the sidecar lane (H20): open box 4. `serve --host 0.0.0.0` with no CLI rule (B6): proposal (W2b-26).
- `026 CH-4`, "Status after PoC-6": out of scope. The Mac command is a test-only stack; one scoped key per service stays box 3.
- `026 CH-4`, "Status after PoC-8": fact (`exfil-04` moved to 022 H-6). The drill's minted key: evidence for box 2, once MinIO joins it.

Edits:

- C1: Receives 056 CH-7 box 4: the admission box below reads "`agent-lib` carries the admission rules (untrusted must use `remote`; a minimum chassis version)", so the minimum chassis version ships in phase A.
- **W2b-02** [HIGH, rule 14]: add two boxes to 026 CH-4: "`agent-lib` carries the admission rules (untrusted must use `remote`; a minimum chassis version), and a render test plus a kind run refuse a pod that breaks them"; "the chart's defaults pass every PoC-5 admission rule, rule 8 (image by digest) included". Note under Out of scope that 055 CH-6 and 056 CH-7 extend them.
- With the `ROWS` change (W2b-24): resize M to L; rewrite box 4 to run against `agent-lib`'s NetworkPolicy on an enforcing CNI, not the PoC-5 manifests; box 6 names the workflow that runs all four kind suites.
- With the `ROWS` change (W2b-01): in `ROWS`, set CH-4 deps to `['G-1', 'CH-3']`; move the "topics" part of box 3 to 020 X-8 (topic ACLs are its box 7); drop CH-4 from 022's and 025's "Blocks".
- P-13 (platform-security): 026 CH-4: under `## Out of scope`, "the CLI bind rule: P-13".
- P-16 (platform-security): 056 CH-7 box 4 moves to 026 CH-4; 077 C-1's box on the rule stays open.

### PR W3W4: issues 027 to 049 (`docs/backlog-ticks-W3W4`)

Inputs never applied, as the findings read them (context for this PR):

- Inputs never applied: the PoC-7 draft names `037 M-2` and `031 G-5` (the encoder numbers plug into G-5; parked). The PoC-8 backlog-changes entry for `037 M-2` is the "Status after PoC-8" bullet above. The PoC-9 W2–W5 report names none of 027–039.
- No issue in 040–049 has a "Status after PoC-N" bullet. The PoC-6c "to apply" list, the PoC-9 `2026-10-30-w2-w5-report.md`, and sections 3 and 5 of [011-what-is-left.md](../../planning/poc/011-what-is-left.md) name none of these issues.
- PoC-7 draft `backlog-changes.md`, the 042 A-2 part (one line, never applied): tick the gate shape as decided; the open question (keep the agent or use `judge`) is closed as above; the outage question is DEC-1 (ADR-010 open question 1). Its 017 H-4 part (the outage fails open, the judge-token gap, no timeout shrink across attempts) is W2b's.
- PoC-7 draft, the `040 D-0` line ("may consume `agents.feedback.received.v1`"): a note in 040 `## Out of scope`; no phase needs it, and the consumer is `later`.
- PoC-7 draft, the 075 E-3 line ("the encoder adapter ... where their numbers plug in"): not W4's; 075 E-3 depends on 042, 046, and parked 047 (W6's call; the product plan parks `EncoderEvaluator`).

#### 028 S-3: `facts_kept` and banned-word scoring for the eval gate and the judge

Phase B. Size M to S. Boxes met after the ticks: 0/6.

Fold:

- `028 S-3`, "Status after PoC-6": out of scope, a fact. The bake-off string matches (`packages/bakeoff/src/bakeoff/tasks.py`) stand in for `facts_kept` in the bake-off only; they are not the score this issue builds. The PoC-6a note says the same (`pocs/poc-06a-bake-off-sidecar-lane/notes/backlog-changes.md`).

Edits:

- C1: Phase B. The live half is `facts_kept` and banned words; size S (ROWS).
- With the `ROWS` change (W3-04): rewrite the title to "`facts_kept` and banned-word scoring for the eval gate and the judge"; move tokens, readability, and similarity to `## Out of scope` as "parked with the SLM track"; rewrite box 1 to "One command scores a golden case file", box 3 to "banned words use the case's `banned:` list", box 6 to "the judge route and rubric version are in the report" (`test_judge_reports_usage_cost_and_route` covers the route, not the version: tick-blocked); drop the `S-1` dependency in `ROWS`; phase B; resize S from the 4 live boxes (box 2 `facts_kept` fixture test, box 4 repeat-run test, box 5 agreement, box 6).

#### 030 S-4: Hand review of a sample and a held-out test set of at least 300 records

Phase parked. Size M. Boxes met after the ticks: 0/6.

Edits:

- With the `ROWS` change (W3-03): in `ROWS`, replace `S-4` with `M-2` (the gate set, number 037) in 068 and 074; the W6 session confirms. Move 030's box 4 (reviewer, date, and reason per label) to 064 D-1 and 065 D-2 as the label metadata the golden set lacks today (the YAML headers name one author and a date, not one per case).

#### 031 G-5: Cost model: GPU hosting cost vs API savings, with the break-even point

Phase parked. Size S. Boxes met after the ticks: 0/6.

Fold:

- `031 G-5`, "Open concern (review 2026-09-29)": the break-even must count the evaluator gate. Out of scope while parked; the product's cost question is the judge at rate 0.1, already priced in ADR-010 (about $5 per million requests).

#### 034 C-8: Training compute logged per fine-tune run, checked against the GPAI threshold

Phase parked. Size S. Boxes met after the ticks: 0/5.

Edits:

- With the `ROWS` change (W3-18): add to 033's and 035's parked notes: "unpark 034 C-8 first"; 034 stays parked and keeps its dependency on 033.

#### 036 S-7: Serve the simplifier SLM with vLLM, with guided decoding for JSON

Phase parked. Size S. Boxes met after the ticks: 0/6.

Fold:

- `036 S-7`, "Status after PoC-6": out of scope on the parked issue. PoC-6c uses llama.cpp on `local-small`; the SLM numbers wait on the Mac run (`pocs/poc-06c-pretrained-slm/notes/2026-10-09-mac-command-debt.md`).
- `036 S-7`, PoC-6c "to apply" list, three items (`pocs/poc-06c-pretrained-slm/notes/backlog-changes.md`): the model server is the scale limit; the queue needs a bound; every number is CPU only. All three go into the parked issue's `## What` as "Status after PoC-6c", so they survive an unpark (W3-15). The same file's items for 063 S-8 and 013 CH-2 belong to the W5 and W2a sessions.

Edits:

- **W3-15** [MEDIUM, rule 10]: add one "Status after PoC-6c" bullet with the three facts and the records `pocs/poc-06c-pretrained-slm/notes/2026-10-28-scale-run.md` and `2026-10-28-slm-run.md`; keep the issue parked. Box 1 in `## Acceptance criteria` names `simplifier-slm`; do not edit it while parked.

#### 037 M-2: Eval gate in CI: a change ships only if it passes the golden set against the baseline

Phase B. Size M to S. Boxes met after the ticks: 3/6.

Ticks after the rewrite:

- `037 M-2`, box 1, rewritten to "A pull request starts the eval gate in CI with no manual step" (W3-06): `· Delivered in PoC-8: .github/workflows/poc08-evals.yml (on: pull_request with no paths filter; jobs evals, regression, redteam); pocs/poc-08-cross-cutting-build/tests/test_poc08_c1_ci.py::test_poc08_evals_runs_the_gate_and_the_redteam_set_on_every_supported_engine; run 40919685333 on 0457ae1 (pocs/poc-08-cross-cutting-build/README.md, exit criterion "Offline evals")`
- `037 M-2`, box 2, rewritten to "A change that drops facts fails the gate, and the merge is blocked with a report": `· Delivered in PoC-8: packages/eval-kit/tests/test_eval_kit_evalgate.py::test_eval_kit_evalgate_fails_the_drop_facts_logic_on_every_baseline_target and ::test_eval_kit_evalgate_fails_when_a_known_bad_answer_starts_to_pass; pocs/poc-08-cross-cutting-build/tests/test_poc08_c5_regression.py::test_the_gate_fails_drop_facts_and_passes_normal_on_every_baseline_target; pocs/poc-08-cross-cutting-build/notes/2026-10-29-eval-gate.md (drop-facts 0/50 against 0.780); evals/gate/baseline.json`
- `037 M-2`, box 7 merged with box 4, rewritten to "Eval results are attached to the CI run": `· Delivered in PoC-8: .github/workflows/poc08-evals.yml, upload-artifact steps poc08-eval-reports-<engine> and poc08-eval-reports-drop-facts (run 40919685333)`

Fold:

- `037 M-2`, "Proposed in PoC-7": decided. DeepEval is the one offline tool (ADR-010 item 1; trial in `pocs/poc-07-cross-cutting-decisions/notes/2026-10-28-eval-tool-trial.md`). The Langfuse dataset upload (`eval_kit.langfuse_datasets`, tested against a stub) is called by no workflow or Makefile target: an open item for phase D, not a box here.
- `037 M-2`, "Status after PoC-8": folds into ticks 1, 2, and 7. "As a required check on `main`" is a repository setting: owner list (011 section 2), not a box. "A real judge's agreement with the labels" and "the label of g08" go to the rewritten 028 S-3 box 5. "No tolerance while the model is scripted" stays a `suggested:` marker (W3-08).

Edits:

- **W3-06** [MEDIUM, rule 1]: rewrite boxes 1, 2, and 7 as in Ticks and tick them with the PoC-8 evidence; the PoC-8 README's `poc08-evals` run 40919685333 is the CI record (the eval-gate note itself has no run id).
- **W3-07** [MEDIUM, rule 1]: rewrite to "a pass rate below the baseline floor fails, with or without a previous run" (the `must_pass` list already does most of this; tick-blocked by a test `test_a_run_below_the_floor_fails_with_no_previous_report`); the absolute floor for a hosted judge is the phase B decision and goes to the DEC-1 pack; keep `suggested: no tolerance on the pass rate while the model is scripted` as is.
- **W3-08** [MEDIUM, rule 1]: keep both open. Name the proving tests: `test_a_changed_prompt_changes_the_gate_input` and `test_evalgate_reads_the_dataset_named_in_the_baseline`.
- **W3-21** [LOW, rule 10]: replace the bullet with "Decided in ADR-010 item 1: DeepEval"; keep the Langfuse dataset sentence as a phase D hand-off.
- With the `ROWS` change (W3-05): retitle "Eval gate in CI: a change ships only if it passes the golden set against the baseline"; rewrite `## Why`; box 4 (MLflow and the event) is moot, merge into 7; `ROWS` dependencies become `["028 S-3"]` (the floor in box 3 needs the score); phase B (the gate set and the judge's defaults); resize S from the 4 open boxes (3, 5, 6, the agreement run owned by 028).

#### 038 X-1a: Terraform module for the first cloud (AWS or GCP)

Phase later. Size L. Boxes met after the ticks: 3/10.

Ticks after the rewrite:

- `038 X-1a`, box 7, rewritten to "A test pod with the gVisor RuntimeClass runs and reports the gVisor kernel (on kind; the cloud rerun is part of box 1)": `· Delivered in PoC-5: pocs/poc-05-sandboxed/tests/test_poc05_kind_remote_controls.py::test_h28_remote_runs_on_gvisor_next_to_a_runc_pod; pocs/poc-05-sandboxed/demo/2026-10-09-demo-sandboxed.md sections 2 and 6; pocs/poc-05-sandboxed/notes/2026-10-08-remote-suite.md`
- `038 X-1a`, box 8, rewritten to "Default-deny egress holds on kind: a remote-lane pod reaches no public address and no other pod": `· Delivered in PoC-5: pocs/poc-05-sandboxed/tests/test_poc05_kind_remote_controls.py::test_h19_remote_reaches_no_outside_address and ::test_h30_remote_reaches_no_other_pod; pocs/poc-05-sandboxed/tests/test_poc05_netpol_static.py::test_poc05_netpol_default_deny_in_every_namespace; pocs/poc-05-sandboxed/notes/2026-10-08-remote-suite.md`
- `038 X-1a`, box 10, rewritten to "Each service's virtual key and tool grants are provisioned from code with no manual steps (kind: `seed.sh`)": `· Delivered in PoC-5: deploy/kind/poc05/platform/seed.sh; pocs/poc-05-sandboxed/tests/test_poc05_seed_static.py::test_no_litellm_config_grants_an_mcp_server_to_every_key and ::test_seed_is_idempotent_and_rotates_on_request; pocs/poc-05-sandboxed/tests/test_poc05_kind_tool_gateway.py::test_a_key_the_gateway_did_not_issue_gets_no_tool; pocs/poc-05-sandboxed/demo/2026-10-09-demo-sandboxed.md`

Edits:

- C1: Receives from 025 H-10 one box: "Argo CD deploys the echo agent from the library chart; it answers on the native, OpenAI, and Anthropic APIs; the cluster refuses an unsigned image". It joins box 9 (two containers from the library chart).
- **W3-11** [MEDIUM, rule 1]: rewrite and tick 7, 8, and 10 as in Ticks. Box 9 (two containers from the library chart) is `tick-blocked`: `pocs/poc-09-agent-mvp-template/tests/test_poc09_chart.py::test_sidecar_lane_renders_one_pod_with_the_chassis_and_the_workload` renders only; the proving test is a kind test that installs `deploy/helm/example-agent` and reads two ready containers (suggested: `test_the_chart_pod_runs_chassis_and_workload`). Box 6 (unsigned images) waits on 025 H-10; box 5 (Vault, External Secrets) has only the kind `seed.sh` stand-in and stays open.
- **W3-12** [MEDIUM, rule 5]: add "Decided in ADR-005 (items 1, 4) and ADR-006" and replace Kyverno with "the ValidatingAdmissionPolicy and its parameter file (`deploy/kind/poc05/admission/`)"; drop the IRSA bullet; keep Argo CD, Vault, and CloudNativePG, which no ADR touches.
- **W3-17** [MEDIUM, rule 13]: rewrite `## Why` to the cluster contract; move the GPU node pool and the MLflow bucket to `## Out of scope` as "parked with the SLM track".
- With the `ROWS` change (W3-09): drop `X-1a` from 117 X-3 (SLOs run on the Compose and kind stacks with Prometheus) and from 055 CH-6 (kind proves gVisor, the CNI, and admission); keep it for 116 X-1b and 121 X-4. The W5 and W1012 sessions confirm.

#### 039 X-2: GPU setup on Kubernetes (NVIDIA GPU Operator) and model weight storage with fast cold start

Phase parked. Size M. Boxes met after the ticks: 0/6.

Edits:

- With the `ROWS` change (W3-16): `parked`; unpark as `later` only if a self-hosted model returns (for example a judge that must run in the cluster). It stays on 038 and 036 in `ROWS`.

#### 040 D-0: Recorder agent: reads result events, removes PII, drops duplicates, writes records to Postgres

Phase later. Size M to L. Boxes met after the ticks: 0/9.

Tick-blocked:

- `040 D-0` box 3 (PII fixture redacted): `packages/chassis/tests/test_result_events_redaction.py::test_a_completed_runs_event_holds_no_pii` uses an email and a phone number. A test with a name fixture would prove the box, and the regex cannot pass it.
- `040 D-0` box 7 (ack after store): a recorder handler that stores, with `EventPortContract` redelivery on its failure, would prove it.

Edits:

- C1: Box 4 reads "the stored record has the event's model-source field (019 H-17)".
- **W4-09** [MEDIUM, rule 5, 1]: reword the dedupe to the event `id` (unique per run and type) and the key hash; read `trace_id` from `traceparent`; rename the model-source field (suggested: `original_model`) and decide it once with 043a; box 3 uses an email and a phone number and puts names under the open Q6 decision (named-entity masking, 011 section 4); add a box for defining `StorePort` ("a port in `chassis.ports`, with a fake and a contract suite"); note the open dead-letter bug (a failed dead-letter publish ends the subscription's consumer task, [W2b-13](findings-W2b-chassis-security-deploy.md)); the recorder relies on that path.
- **W4-10** [MEDIUM, rule 12]: move the `source` field to 043a (the recorder stores whatever the event carries; its box says "the stored record has the event's model-source field"); for box 6, depend on the tool gateway PoC-5 already runs (the MCP gateway behind the chassis, `pocs/poc-05-sandboxed/tests/test_poc05_kind_hardreq1.py::test_mcp_gateway_refuses_the_workload_without_the_chassis_key`) and say 054 H-16 extends it, or keep the box open with the dependency `054 H-16` stated and the issue `later`.
- With the `ROWS` change (W4-08): phase `later`; resize M to L (nine open boxes, nothing ticked). It becomes phase E only if the user picks events over Prometheus for the north star (an open decision in the product plan). Add 021 H-20 as a real dependency of the consume path (it is listed already) and note it is `later`.

#### 041 A-1: Simplifier business logic as a workload behind the chassis

Phase parked. Size M. Boxes met after the ticks: 0/6.

Edits:

- With the `ROWS` change (W3-01): drop `S-7` from the `ROWS` dependencies of A-1 and R-11; the W4 and W7 sessions decide whether A-1 keeps `simplifier-slm` in its text or calls a named route from the route map (phase B). If A-1 keeps rewrite rules, they are the golden set's `banned:` and `contains:` fields, not 027 S-1.

#### 042 A-2: Evaluator gate on facts_kept, one retry, then fallback to a big model

Phase B. Size S to M. Boxes met after the ticks: 3/7.

Ticks:

- `042 A-2`, box "An answer with `facts_kept` ≥ 0.95 returns `status: ok`": `· Delivered in PoC-7: packages/chassis/tests/test_evaluator_gate.py::test_a_passing_answer_goes_out_once_with_the_judge_metrics (status ok, one attempt, one model call; the threshold in the test is 0.7, so the box is reworded to the configured threshold)`
- `042 A-2`, box "A fixture that drops a fact triggers exactly one retry": `· Delivered in PoC-7, PoC-8: packages/chassis/tests/test_evaluator_gate.py::test_a_failing_answer_retries_once_then_falls_back, pocs/poc-07-cross-cutting-decisions/tests/test_poc07_evals_gate.py::test_poc07_evals_gate_retries_once_then_falls_back, pocs/poc-08-cross-cutting-build/tests/test_poc08_c6_gate.py::test_the_gate_retries_then_falls_back_to_the_configured_route (every lane; the fallback is the configured fallback_route, end status fallback)`
- `042 A-2`, box "No call makes more than one retry": `· Delivered in PoC-7: packages/chassis/tests/test_evaluator_gate.py::test_a_failing_answer_retries_once_then_falls_back (retries: 1 gives two scored attempts, then the unscored fallback; GATE_RETRIES == 1)`

Fold:

- `042 A-2`, "Proposed in PoC-7" (the last `## What` bullet): fold to "Decided in ADR-010 (accepted)"; drop "the user accepts it at the PoC-7 pull request". The open part, "keep the `evaluator-facts` agent behind `EvaluatorPort`", is settled by what was built: `judge` and `encoder` are in-chassis adapters, and there is no A2A evaluator adapter. Drift (W4-07).
- `042 A-2`, the "Open concern (review 2026-09-29)" paragraph in `## Why`: out of scope. ADR-010 items 4 to 10 settled it: sample rate by request id, async mode, the judge's tokens as a `metrics` event, and the recommended defaults. The numbers wait on the Mac run (011, section 1).

Edits:

- **W4-07** [MEDIUM, rule 5, 6, 10]: replace the flow bullets with the built flow (score, retry once on the same route below `threshold`, one unscored attempt on `fallback_route`, end status `fallback`; with no fallback route `error evaluator_gate_failed`), "Decided in ADR-010" in place of "Proposed in PoC-7", move the open concern to a one-line pointer to ADR-010 items 9 and 10, and mark `threshold: 0.95` as `suggested:` (built default 0.7; a facts-rubric value, not a platform value).
- **W4-12** [MEDIUM, rule 6]: cite the metrics spec for the 10% bar; list for the DEC-1 decision pack: outage rule (W4-01), default mode and sample rate (ADR-010 question 2), and where the judge score goes (ADR-010; Langfuse scores for async results are not written yet per the PoC-7 draft).
- With the `ROWS` change (W4-02): rewrite 042 for phase B and resize S to M. Phase B needs five things from it, each a box: (1) the judge on the route map: `spec.model.judge_route` read by `JudgeEvaluator` (it reads `CHASSIS_JUDGE_ROUTE` today), checked against the service's key; (2) the `cloud` profile sets `evaluator_gate: true`, `evaluator_mode: async`, `evaluator_sample_rate: 0.1`, `spec.adapters.evaluator: judge`, with a test per setting; (3) the outage rule, once DEC-1 decides (W4-01); (4) the judge's cost and tokens in `/metrics` (W4-04); (5) the judge's latency, cost, and agreement from the Mac run recorded in `pocs/poc-07-cross-cutting-decisions/notes/` and ADR-010 item 10 confirmed or changed. Reword boxes 1 to 3 to `threshold` and `fallback_route` (they are then ticked), reword box 4 to "the gate calls only the configured evaluator adapter" (the judge is not an agent), and reword box 6 to the 30-case gate set (`packages/eval-kit`, `golden/cases-gate.yaml`) in place of the S-4 held-out set.
- P-1 (eval-expert): 017 H-4 gets one sentence: "An evaluator outage fails open as built (ADR-010 item 4); P-1 decides." 042 A-2 box 5 stays open until P-1 is decided.
- P-14 (observability-expert): 042 A-2: add the box "the judge's tokens and cost count under the judge route, and a scrape test shows it".

#### 043 A-3: Result events with source, stored by the recorder

Phase parked. Size S. Boxes met after the ticks: 0/6.

Tick-blocked:

- `043 A-3` box 1 (one event per call on every API): `test_result_events.py::test_a_completed_run_publishes_one_valid_cloudevent` covers the native interface only. A test that counts events on the OpenAI and Anthropic interfaces would prove it.
- `043 A-3` box 3 (a repeat leaves one record): the replay half is `test_result_events.py::test_a_replayed_idempotent_call_publishes_nothing_new`; the record half needs 040.
- `043 A-3` box 4 (no credential in the workload): the chassis-level proof is the 019 H-17 tick (`pocs/poc-05-sandboxed/tests/test_poc05_kafka_static.py::test_the_secret_reaches_only_the_chassis_container_of_the_events_agent`); this box names the simplifier, which does not exist.

Edits:

- C1: Parked (WP). Add one line under the title: "Parked 2026-11-06 with the SLM track. The live result-event boxes moved to 019 H-17." Strike the two live boxes and leave the rest of the body as it is.

#### 044 G-4: History swap in the chassis: long past answers replaced by simplified versions

Phase parked. Size M. Boxes met after the ticks: 0/8.

Edits:

- With the `ROWS` change (W4-13): park 044 and 045 with one line in each: "Parked 2026-11-06 with the SLM track: the short versions come from the SLM simplifier's records; revisit as `later` if a route map and a recorder exist"; keep the pre-call-hook suggestion and cite ADR-001 item 6.

#### 046 A-5: Container dashboard and per-agent gate panels

Phase E. Size S to M. Boxes met after the ticks: 1/6.

Ticks:

- `046 A-5`, box "The dashboard is provisioned from code, not built by hand": `· Delivered in PoC-8: packages/chassis/tests/test_dashboards.py::test_the_generated_files_are_up_to_date, pocs/poc-08-cross-cutting-build/tests/test_poc08_stack_static.py::test_grafana_provisions_every_file_of_deploy_dashboards`

Tick-blocked:

- `046 A-5` box 1 (every panel, in Compose and on Kubernetes): the stack tests `pocs/poc-08-cross-cutting-build/tests/test_poc08_stack_metrics.py::test_every_panel_query_has_data` and `::test_grafana_has_the_agent_dashboard_with_every_panel` are `network`-marked and wait on run 41011346864 (`pocs/poc-08-cross-cutting-build/notes/backlog-changes.md:31`). The panels differ from the box's list. No Kubernetes path exists.

Edits:

- With the `ROWS` change (W4-05): rescope 046 to "Container dashboard and per-agent gate panels" (phase E), resize S to M. Keep the generic boxes: first-try pass rate (1 − (`chassis_gate_retries_total` + `chassis_gate_fallbacks_total`) / `chassis_gate_runs_total`), retry rate, fallback rate with a 10% target line, links to Langfuse and Loki, and "provisioned from code" (ticked). Move "tokens saved" and the G-3 baseline to Out of scope with the parked simplifier. Add three rows from the metrics spec (north star, inputs, counters) and a test that the file is up to date. The filter by config version and model route version needs a label the families do not carry ([contract v7 A.3](../../contracts/contract-v7.md#a3-the-families): only `agent`, `interface`, `status`, `code`, `route`, `direction`, `tool`, `mode`); make it an open box tied to a contract change, or drop it. Drop the template-repo box (`agentctl` ships no dashboards; that is 059 H-15) and the 100-calls-10-fallbacks box in favor of the PoC-8 stack drill.

#### 047 M-3: Shadow mode: new model runs next to the current one without serving users

Phase parked. Size M. Boxes met after the ticks: 0/7.

Edits:

- With the `ROWS` change (W3-20): the W4 session decides 047 and 049; 037's rewritten text says "no registry" so they can see why.

#### 048 M-4: Canary rollout and one-step rollback

Phase parked. Size S. Boxes met after the ticks: 0/6.

Edits:

- With the `ROWS` change (W4-14): park 048; point the metrics spec's chassis-release rollback to 056 CH-7 (rings), and note in 048 that a per-task model swap (phase B) needs no canary because it is a config reload of the route map. No box ticks.

### PR W5: issues 050 to 063 (`docs/backlog-ticks-W5`)

Inputs never applied, as the findings read them (context for this PR):

- Inputs never applied, named for these issues:
  - the PoC-6c "to apply" lines for 063 S-8 (W5-20);
  - the PoC-6b review items for 055 CH-6 and 054 H-16 (W5-15, W5-23);
  - the PoC-4 item for 051 H-13 (OpenAPI limits stale after a reload), which W2a-20 already places in 010 H-12;
  - the PoC-5 item "a CI job for the sidecar kind suite" (`pocs/poc-05-sandboxed/notes/backlog-changes.md` line 55). It is now half stale: the sidecar-controls and admission files are in `REMOTE_TESTS`, while `test_poc05_kind_tool_gateway.py` and `test_poc05_kind_hardreq1.py` are not. That belongs to 026 CH-4 (W2b).
  The PoC-7 draft names 054 H-16 only, for the two red-team misses, which the issue already carries. The PoC-9 w2–w5 report names 058 and 059 only. Its W2 "publish the packages" item feeds 025 H-10a and 059a, and its W5 item says "024 CH-3 and 058 CH-8 hold".

#### 051 H-13: OpenAPI 3.1 spec per agent, with MCP tools generated from it

Phase later. Size S to M. Boxes met after the ticks: 2/8.

Ticks:

- `051 H-13`, box "An MCP client lists the simplifier's tools": `· Delivered in PoC-3: pocs/poc-03-one-interface-every-client/tests/test_exit_criteria.py::test_exit_4_an_mcp_client_lists_the_agents_tool_and_runs_it; packages/chassis/tests/test_mcp_interface.py::test_calling_the_tool_returns_the_native_response_envelope` (the echo workloads are the simplifier, `packages/workloads/README.md`).
- `051 H-13`, box "A contract test fails if an MCP tool's input schema differs": `· Delivered in PoC-3: packages/chassis/tests/test_mcp_interface.py::test_tool_input_schema_is_the_openapi_request_schema_of_run; pocs/poc-03-one-interface-every-client/tests/test_exit_criteria.py::test_exit_5_mcp_tools_and_rest_come_from_one_openapi_spec`.

Ticks after the rewrite:

- `051 H-13`, box 6 reduced to "the chassis version, the lane, and the trust value" (after W5-17): `packages/chassis/tests/test_manifest.py::test_manifest_matches_the_config_and_the_openapi_spec, ::test_manifest_agent_trust_shows_untrusted_with_the_remote_lane`. The image digests become their own open box.

Fold:

- `051 H-13`, "Status after PoC-3": facts are ticks. FastMCP 4, `/v1/mcp`, and `spec.interfaces.mcp` move into `## What` (W5-17). Service operations, digests, scopes, and governance are open boxes. Auth on MCP calls is now built (contract v6, D.1), but the 403 test is missing (box 4, `tick-blocked`). The `isError` gap and the private `tool._client` reads are open boxes, from the PoC-3 debt note. The LiteLLM gateway grant is 026 CH-4's: out of scope.

Edits:

- With the `ROWS` change (W5-17): `tick-blocked` boxes 1 and 4: add an `openapi-spec-validator` test with the spec saved in CI, and a `/v1/mcp` 403 case. Split box 6, as in "Ticks after a body edit". Cite FastMCP 4 and contract v6, and drop the markers. Resize S to M. The phase is `later` (the registry readers 076 and 083 are `later`).

#### 052 H-21: AsyncAPI 3.0 spec per agent, generated next to the OpenAPI spec

Phase later. Size S. Boxes met after the ticks: 0/6.

Edits:

- **W5-25** [LOW, rule 7]: pick one AsyncAPI version (3.0 matches the epic), and name the real config keys, citing contract v3 for the extensions. The phase is `later`. The broker bindings wait on DEC-1.

#### 053 H-22: Event reliability: idempotent consumers, retries with backoff, dead-letter topic

Phase later. Size S to M. Boxes met after the ticks: 0/7.

Edits:

- With the `ROWS` change (W5-16): cite contract v3 for the name and the backoff, and drop the `suggested:` markers. Rewrite box 6 to "one partition key". Strike the broker-native line, citing ADR-004 item 8. Add W2b-13's box. Resize S to M. In `ROWS`, add `D-0` to the deps. The phase is `later`.
- P-12 (chassis-architect): 053 H-22: add the box "a failed dead-letter publish is counted and retried, and the consumer keeps running".
- P-18 (chassis-architect): 053 H-22 box 4 stays open until P-18 lands.

#### 054 H-16: Tool port: native tool calls or JSON via guided decoding; read and write modes

Phase E. Size M to L. Boxes met after the ticks: 3/11.

Ticks:

- `054 H-16`, box "A tool that is not on the key's allow-list is refused by the gateway": `· Delivered in PoC-5: packages/chassis/tests/test_tool_gateway_contract.py::test_the_unlisted_probe_is_refused_and_its_marker_never_seen; pocs/poc-05-sandboxed/tests/test_poc05_kind_tool_gateway.py::test_unlisted_probe_is_refused_with_its_control, pocs/poc-05-sandboxed/tests/test_poc05_kind_hardreq1.py::test_mcp_gateway_refuses_the_workload_without_the_chassis_key (kind, by hand); pocs/poc-05-sandboxed/notes/2026-10-09-close-runs.md` section 6. The chassis has no allow-list of its own (W5-06), so "with the chassis check switched off" holds by construction.
- `054 H-16`, box "Code sent to the code-execution tool runs in its sandbox": `· Delivered in PoC-5: pocs/poc-05-sandboxed/tests/test_poc05_kind_code_runner.py::test_code_runs_on_gvisor_with_no_egress, ::test_files_do_not_cross_calls` (kind, `remote-lane.yml`).

Ticks after the rewrite:

- `054 H-16`, the first half of box 11, "The MCP adapter and the fake tools pass the same `ToolPort` contract suite, and are picked by `spec.adapters.tools`" (after W5-11): `packages/chassis/tests/test_contracts.py::TestInMemoryTools`, `packages/chassis/tests/test_tool_gateway_contract.py::TestMcpGatewayTools`, `packages/chassis/tests/test_tool_endpoint.py::test_fake_profile_builds_the_fake_tools`.

Fold:

- `054 H-16`, "Status after PoC-2": facts are ticks (box 6). Charging tool calls to the run is open box 9.
- `054 H-16`, "Status after PoC-5": facts are ticks (boxes 3, 10). The bare-name and undeclared-key gaps, and the two `suggested:` fixes (`unknown_tool` outside a run; resolve a bare name to the one `<server>-<name>` match), become one new open box (W5-23). B12 closed for the code runner: a fact for `## What`.
- `054 H-16`, "Status after PoC-6": `acronym_expand` and the `<server>-<tool>` naming move into `## What`. Plain-A2A tool calls invisible to the chassis: already in contract v5 B.8, so drop the bullet.
- `054 H-16`, "Status after PoC-8": the two red-team misses stay with box 8 (`tool-04-base64`, `exfil-03-write-tool-gets-secret`, `pocs/poc-08-cross-cutting-build/notes/2026-10-29-redteam.md`).

Edits:

- **W5-11** [MEDIUM, rule 1]: `tick-blocked`. Name the tests that would prove each half:
- Box 2: a log or metric line carrying `tool_denied`.
- Box 5: a client retry with one `Idempotency-Key` that produces one side effect on the fake write tool.
- Box 8: a red-team case that counts tool calls before and after the injection.
- Box 11: the `ToolPortContract` bound through `/mcp` in `inprocess` and `sidecar`.

Split box 11, so its first half ticks as listed.
- **W5-23** [MEDIUM, rule 10]: add an open box: "a bare tool name resolves to its one `<server>-<name>` match, or is refused; outside a run, an unknown name answers `unknown_tool`". Send `follow_redirects` to `proposals.md`.
- With the `ROWS` change (W5-12): resize M to L, and set phase E. Box 7 (retry, then the fallback route on a malformed tool call) can join phase B's retry stage with 017 H-4, if C2 prefers.
- With the `ROWS` change (W5-07): move box 1 and the guided-decoding text to `## Out of scope` as "Parked on 2026-11-06 (product plan)". In `ROWS`, drop `H-3` and `CH-2` from 054's deps. Add `H-4` (box 7, the fallback route) and `H-6` (box 8, the `tool_output` guard).
- P-17 (platform-security): 054 H-16 box 4: rewrite per the pick; box 2's "logged" half stays open either way.

#### 055 CH-6: Remote lane and the trust rule: sandboxed pod or managed runtime over A2A, cloud auth adapter, admission check

Phase A. Size L to S. Boxes met after the ticks: 5/7.

Ticks:

- `055 CH-6`, box "A fake workload runs through the `remote` lane in CI on every commit": `· Delivered in PoC-5: pocs/poc-05-sandboxed/tests/test_poc05_remote_lane_contract.py::TestPoc05ThreeLanes` (offline, the same `LaneContract` as `packages/chassis/tests/test_lane_contract.py::TestChassisLanes`); `pocs/poc-05-sandboxed/tests/test_poc05_kind_remote_lane.py::test_remote_gives_the_sidecar_events_for_the_same_request` (kind, `remote-lane.yml`).
- `055 CH-6`, box "A remote pod without its credential is refused by the proxies": `· Delivered in PoC-5: packages/chassis/tests/test_remote_auth.py::test_no_token_and_a_wrong_token_are_401_with_one_body; pocs/poc-05-sandboxed/tests/test_poc05_kind_remote_controls.py::test_h17_listener_answers_only_the_remote_token_inside_a_run, ::test_h30_remote_reaches_no_other_pod` (kind, `remote-lane.yml`; LiteLLM and its MCP gateway share port 4000).
- `055 CH-6`, box "The admission check refuses an `untrusted` workload, and a third-party image": `· Delivered in PoC-5: pocs/poc-05-sandboxed/tests/test_poc05_admission_static.py::test_policy_has_every_rule_and_the_params_check; pocs/poc-05-sandboxed/tests/test_poc05_kind_admission.py::test_rejected_fixture_is_refused_with_its_rule_message` (kind, in `REMOTE_TESTS`); `pocs/poc-05-sandboxed/notes/2026-10-09-close-runs.md` section 6.
- `055 CH-6`, box "Every pipeline stage runs on remote calls": `· Delivered in PoC-5, PoC-8: pocs/poc-08-cross-cutting-build/tests/test_poc08_c7_stages_redteam.py::test_every_stage_runs_in_every_lane; pocs/poc-05-sandboxed/notes/2026-10-02-blind-spots.md` section 1; `docs/contracts/contract-v5.md#b8-what-the-chassis-cannot-see-or-control-with-a-plain-remote`.
- `055 CH-6`, box "gVisor's latency and memory overhead are measured": `· Delivered in PoC-5: pocs/poc-05-sandboxed/notes/2026-10-09-gvisor-overhead.md` "Result".

Ticks after the rewrite:

- `055 CH-6a`, box 2, once T10 runs (what is left, section 4) and the two hand-run kind files join `REMOTE_TESTS` (W5-03): `test_poc05_kind_remote_controls.py::test_remote_holds_one_secret_its_own_token, ::test_h19_remote_reaches_no_outside_address, ::test_h21_root_is_read_only_and_tmp_is_writable` already pass in `remote-lane.yml` for the secret, egress, and write halves.

Fold:

- `055 CH-6`, "Status after PoC-5": facts are ticks (boxes 1, 3, 4, 6, 7). Bearer token, ValidatingAdmissionPolicy, and agent-sandbox move into `## What` as "Decided in ADR-005" (W5-13). The dispatcher's token and `ipBlock`, the claim-delete denial of service, and the `/dev/shm` cap move to `## Out of scope` as accepted, citing ADR-005 Consequences. T10 is box 2, owned by the user. The managed-runtime adapter goes to 055b.
- `055 CH-6`, "Status after PoC-6": plain-A2A mode and the four gVisor remotes move into `## What`, citing contract v5 part B. "The kind results wait on the first `poc06-kind.yml` run" is stale: what is left section 4 says that job passes its contract and hostile checks. The two unapplied PoC-6b findings become open boxes (W5-15).

Edits:

- C1: Re-scope, not split. Keep boxes 1 to 4, 6, and 7 in phase A. Box 2 adds "`test_poc05_kind_tool_gateway.py` runs in `remote-lane.yml`".
- C1: Box 5 (the managed runtime) moves to `## Out of scope`: "Decided in ADR-006: managed runtimes (AgentCore, Vertex) are out of scope since 2026-10-09. The first cloud, 038 X-1a, reopens it." The managed-runtime blind-spot rows and the "one relaxation" bullet go with it.
- C1: Size S (ROWS). Deps lose X-1a and gain CH-3 (ROWS).
- **W5-13** [MEDIUM, rule 5, 6]: replace each with "Decided in ADR-005" and drop the `suggested:` markers. Keep mTLS only as ADR-005's Revisit trigger. Apply the same to 054's code-execution bullet.
- **W5-15** [MEDIUM, rule 10]: add an open box to 055 CH-6a: "a remote's output over `spec.limits` ends the run with a fixed error". Send the `except` item to `proposals.md`. Drop the stale clause.

#### 056 CH-7: Chassis release in rings, with a per-service pin and a minimum-version admission rule

Phase later. Size M to L. Boxes met after the ticks: 0/7.

Edits:

- C1: Box 4 (minimum chassis version) moves to 026 CH-4 (phase A). Add the box "the chassis version in `/manifest` equals the image tag" (from the ROWS resize).
- **W5-14** [MEDIUM, rule 5]: in both issues, replace "Kyverno" with "ValidatingAdmissionPolicy, in the `agent-trust-rule` set (ADR-005 decision 4)". Tell W3, the 038 X-1a owner, that 038 still installs Kyverno.
- With the `ROWS` change (W5-22): resize M to L. In `ROWS`, add `H-13` (`/manifest`) and `X-1a`. Add an open box: "the chassis version in `/manifest` equals the image tag". The phase is `later`.
- P-16 (platform-security): 056 CH-7 box 4 moves to 026 CH-4; 077 C-1's box on the rule stays open.

#### 057 H-19: Stateless check in CI: fail the build on local disk writes or kept data

Phase F. Size S to M. Boxes met after the ticks: 1/6.

Ticks:

- `057 H-19`, box "Both containers of a pod deployed with the Helm chart run with a read-only root": `· Delivered in PoC-9: pocs/poc-09-agent-mvp-template/tests/test_poc09_chart.py::test_sidecar_lane_containers_are_hardened, ::test_remote_lane_renders_the_chassis_pod_and_a_gvisor_workload_pod` (render tests; `poc09-helm.yml`).

Fold:

- `057 H-19`, "Status after PoC-4": the detector facts move into `## What`. The counter of failed tool listings is out of scope (no request data). The template CI step, the lint rule, and the admission policy stay open boxes 1, 2, and 6.

Edits:

- With the `ROWS` change (W5-18): rewrite box 3 to "the template's workload for each supported framework passes". Resize S to M. Point the dependency at 025 H-10a, and add 015 H-8 (the kit's suite). Set phase F.

#### 058 CH-8: Framework event mappings and one non-Python workload in the service template

Phase C. Size M to L. Boxes met after the ticks: 4/5.

Ticks:

- `058 CH-8`, box "The workload for each supported framework passes the contract suite over A2A": `· Delivered in PoC-6: pocs/poc-06a-bake-off-sidecar-lane/tests/test_poc06a_engine_contract.py::TestEngineInMemory, ::TestEngineOverA2A; pocs/poc-06b-bake-off-remote-lane/tests/test_poc06b_remote_engine_contract.py::TestRemoteEngineOverA2A` ("localhost" is a Unix socket).
- `058 CH-8`, box "The non-Python workload passes the same contract suite": `· Delivered in PoC-6: pocs/poc-06a-bake-off-sidecar-lane/tests/test_poc06a_criteria.py::test_criterion_2_the_typescript_agent_passes_through_the_generic_sidecar_connector; docs/contracts/contract-v5.md#c4-the-evidence-and-the-one-list-of-changes`.
- `058 CH-8`, box "The chassis imports no framework package": `· Delivered in PoC-1: pocs/poc-01-walking-skeleton/tests/test_day0.py::test_import_rules_hold` (the "no agent framework anywhere in the chassis" contract in `pyproject.toml`).
- `058 CH-8`, box "Token deltas and tool-call events stream for every mapped framework, or the gap is listed": `· Delivered in PoC-6: pocs/poc-06a-bake-off-sidecar-lane/notes/2026-10-09-scorecard.md` "Criterion 2" and "Criterion 3" (gaps: smolagents sends one delta; `kagent-adk` shows no `tool_call`).

Ticks after the rewrite:

- `058 CH-8a`, new box "`agentctl new` defaults to LangGraph and scaffolds a `sidecar` agent that passes the lane contract" (after W5-05): `packages/agentctl/tests/test_agentctl_cli.py::test_the_defaults_scaffold_a_langgraph_sidecar_agent_with_a_first_commit, ::test_the_template_default_framework_matches_the_rules`. "and the evals" stays open.

Fold:

- `058 CH-8`, "Status after PoC-6": the mapping sizes and "no engine needed an event change" move into `## What`. "Proposed default: PydanticAI" and ADR-006 "proposed" are stale (W5-21). The MCP stand-in goes to 058 CH-8a. The hosted numbers go on the owner list (what is left, section 1).
- `058 CH-8`, "Status after PoC-9": a fact for box 1 and for `## What`. "The TypeScript agent is not in the template" moves to `## Out of scope`, citing ADR-006 D5.

Edits:

- C1: Re-scope, not split. Keep box 2 and the guide. Add four boxes: "the LangGraph template uses `langchain-mcp-adapters` on `mcp` 2, or a test fails when that pairing is ready"; "a trace-join test shows LangGraph's graph steps under the run span (ADR-006 decision 1)"; "`agentctl new` defaults to LangGraph and passes the lane contract and the evals" (its first half ticks once commit `ed1e834` merges); "a `langchain` `create_agent` variant ships in the template".
- C1: Under `## Out of scope`: "The checkpointer and interrupt ADRs: CH-9 (added in PR-R)." Phase C. Size L (ROWS).
- **W5-21** [MEDIUM, rule 5, 10]: replace both with "Decided in ADR-006 (amended 2026-11-06)", citing decision 2's set. Drop the `suggested:` marker. Send the stale ADR-006 sentence to the `adr` skill owner as a note.

#### 059 H-15: Agent factory CLI: scaffold an agent by class and kind, modules by config

Phase F. Size M to L. Boxes met after the ticks: 0/10.

Ticks after the rewrite:

- `059 H-15a`, box 7 rewritten to "an untrusted framework with `--connector sidecar` fails with a clear error", if W5-02 lands as a rewrite: `packages/agentctl/tests/test_agentctl_cli.py::test_an_untrusted_framework_is_refused_in_the_sidecar_lane; packages/agentctl/tests/test_agentctl_config.py::test_agentctl_refuses_what_the_chassis_refuses`.

Fold:

- `059 H-15`, "Status after PoC-9": the built flags move into `## What`. "Trust follows the framework; there is no `--trust` flag" is W5-02. The `orchestrator` and `data` kinds, `spec.modules`, and `governance` go to 059b. The chart in the generated repo goes to 059a, with 025 H-10a.

Edits:

- C1: Re-scope, not split. Box 1 moves to 008 H-14; boxes 2 to 4 merge into 008 H-14 boxes 5 to 7. Boxes 6 and 7 follow P-15.
- C1: Box 10 reads: "The generated `governance` block holds the fields the chassis schema has (`risk_class`, `ai_generated_marking`), with a placeholder for each; 077 C-1 adds the rest of E.4."
- C1: Add two boxes: "`agentctl` refuses a generated route that the service's key does not list" (the route map in 012 H-3) and "a person new to the code goes from `agentctl new` to a demo in under a day, recorded in a dated note" (product plan, phase F).
- C1: Under `## Out of scope`: "The chart in the generated repo and the published platform pin: 025 H-10." Phase F. Size L (ROWS).
- **W5-24** [MEDIUM, rule 14]: keep box 5 open in 059a. The proof is a `demo.sh --live` rerun on the LangGraph default that calls `/v1/messages`, recorded as a dated note.
- P-15 (platform-security): 059 H-15 boxes 6 and 7: rewrite per the pick; box 6 stays open until the generated repo has a chart.

#### 060 H-23: More broker adapters behind the event port: Kafka, AWS, GCP

Phase later. Size S. Boxes met after the ticks: 0/5.

Fold:

- `060 H-23`, "Status after PoC-5": a fact. It moves into `## What` as the bar for box 1.

Edits:

- With the `ROWS` change (W5-19): replace the Dapr line with "Dapr: parked on 2026-11-06 (product plan)". In `ROWS`, add `H-22` to H-23's deps. The broker pick goes to the DEC-1 pack (ADR-004 item 5 keeps `events: none` in every profile until then).

#### 061 H-5: A2A adapter: JSON-RPC endpoint, task states, signed agent card

Phase later. Size S. Boxes met after the ticks: 0/6.

Edits:

- **W5-27** [LOW, rule 7]: write `spec.interfaces.a2a`. The phase stays `later` until DEC-1 decides the public endpoint (the product plan, gap P3).

#### 062 H-11: Chassis reuse in other projects: the stock image in front of any A2A workload

Phase later. Size S. Boxes met after the ticks: 0/4.

Ticks after the rewrite:

- `062 H-11`, box 4 rewritten to agent-testkit (after W5-10): `packages/agent-testkit/tests/test_agent_testkit_isolation.py::test_the_kit_imports_with_the_chassis_unimportable`.

Edits:

- P-19 (chassis-architect): 062 H-11 boxes 3 and 4: rewrite per the pick.

#### 063 S-8: Optional: fine-tune an SLM on tool-call examples for tool agents

Phase parked. Size L. Boxes met after the ticks: 0/5.

Fold:

- `063 S-8`, "Status after PoC-6": replaced by a "Parked on 2026-11-06" note that carries the PoC-6c baseline (W5-20).

Edits:

- With the `ROWS` change (W5-20): phase `parked`. Replace the PoC-6 bullet with "Parked on 2026-11-06 (product plan). Baseline from PoC-6c: 135 of 135 on CPU (`pocs/poc-06c-pretrained-slm/notes/backlog-changes.md`)". No live issue depends on 063.

### PR W6W7: issues 064 to 093 (`docs/backlog-ticks-W6W7`)

Inputs never applied, as the findings read them (context for this PR):

- None of 064–075 has a "Status after PoC-N" bullet (`grep` finds none). The inputs never applied do name some of them (W6-14):
- The PoC-6c list and the PoC-9 W2–W5 report name none of 064–075.
- None of the 18 issues has a "Status after PoC-N" bullet (checked with `grep` over 076–093). Inputs never applied, read for this wave:

#### 064 D-1: Golden set agent: reads records, scores them with a judge, sends uncertain ones to review

Phase later. Size M. Boxes met after the ticks: 0/7.

Fold:

- `064 D-1`, PoC-7 draft (`pocs/poc-07-cross-cutting-decisions/notes/backlog-changes.md` lines 15 and 68): "may consume `agents.feedback.received.v1`; 064's Langfuse decision reopens ADR-009". Open item, now answered by ADR-010 item 2 (Langfuse datasets, LangSmith a candidate). Goes into "Status after PoC-7" on 064.
- `064 D-1`, PoC-8 backlog changes (line 50): "About 50 cases per agent holds for one agent; every other agent needs its own set; that is 064 D-1's work." Out of scope for the golden set agent today; goes into "Status after PoC-8" as a fact (50 cases, `packages/eval-kit/src/eval_kit/golden/`).

Edits:

- **W6-09** [MEDIUM, rule 14]: phase `later`, size M stays (7 open boxes; no box is built, so nothing moves the estimate). Name in `## Out of scope`: "datasets in Langfuse and LangSmith for the gate set are phase D (see the new issue in W6-01)". The ADR-001 follow-up (a) decision (W6-22) must come first when this unparks.
- **W6-10** [MEDIUM, rule 5]: add "Decided in ADR-010 items 2 and 11"; rewrite the scoring bullet to "scores through `EvaluatorPort` on the judge route (`spec.model.judge_route`)"; delete the Decision bullet and keep "LangSmith datasets are a second store once the phase D trial passes".
- **W6-14** [MEDIUM, rule 10]: add "Status after PoC-7" bullets to 064 D-1, 072 C-3, and the parked 073–075, and a "Status after PoC-8" bullet to 064 D-1, as listed above.
- **W6-22** [MEDIUM, rule 5]: no change to phases A–F. Keep (a) open in `000-plan.md`; add to 064 and 072 `## What`: "blocked on follow-up (a): decide before the first box". Add (a) to the owner list in `011-what-is-left.md` section 3 only when one of 040, 064, or 072 leaves `later`.
- With the `ROWS` change (W6-11): add `S-3` to the dependencies of D-1 in `ROWS` (028 is lower than 064); drop nothing. 042 A-2 depends on parked 041 and 028 (W4-03); the W4 session owns that.

#### 065 D-2: Review UI: approve, reject, edit, with access control and an audit log

Phase later. Size M. Boxes met after the ticks: 0/6.

Edits:

- **W6-12** [MEDIUM, rule 14]: phase `later`, size M stays (6 open boxes). Add the `fake-langfuse` queue route to the body as the offline test seam. Blocks 110 C-6 (W9) and 073 (parked) by regeneration.

#### 066 D-6: Tags and splits (train, test, holdout) per record

Phase later. Size S. Boxes met after the ticks: 0/6.

Edits:

- **W6-13** [MEDIUM, rule 14]: phase `later` for both, sizes S stay. Add one line to each `## Reuse`: "Decide with the dataset store (ADR-010 item 2): splits and tags may be dataset fields, not tables". No box change now.

#### 068 D-3: Import from file upload, API call, or bucket watch

Phase later. Size S. Boxes met after the ticks: 0/7.

Edits:

- With the `ROWS` change (W6-02): confirm W3-03: in `ROWS`, replace `S-4` with `M-2` (037, the gate set and `evals/gate/baseline.json`). Rewrite box 4 to "The 50-case gate set (`cases.yaml` plus `cases-gate.yaml`) imports in one step, marked as already reviewed, with split `holdout`", and the `## Why` sentence the same way. 030's box 4 (reviewer, date, reason per label) lands on 064 D-1 or 065 D-2 as W3-03 says; neither is live, so it waits with them.

#### 069 D-7: Versioned datasets with lakeFS; each export is an immutable version with an ID

Phase later. Size M. Boxes met after the ticks: 0/6.

Edits:

- **W6-15** [MEDIUM, rule 6]: phase `later`, size M stays (6 boxes, box 6 parked). Keep the choice out of the DEC-1 pack: it blocks no W1 to W4 issue. Add a "Decided in ADR-010 item 2" sentence to the Decision bullet: "default Langfuse dataset versions; lakeFS only if exports must be byte-identical objects".
- **W6-16** [MEDIUM, rule 13]: mark 069 box 6, the MLflow bullet in 069, and the MLflow bullet in 071 as "parked with the SLM track" under `## Out of scope`; reword 070's `## Why` to "feeds the MCP golden set tools (096 P-2a)"; the `blocks` on 073 regenerates.

#### 070 D-4: Export in JSONL, CSV, Parquet, and Hugging Face datasets format

Phase later. Size S. Boxes met after the ticks: 0/6.

Edits:

- **W6-17** [MEDIUM, rule 4]: phase `later`, size S stays. Reword box 1 and the `## What` bullet from "lakeFS version" to "dataset version id (the store decided with 069)". Dependencies keep 066, 068, 069 as is.

#### 071 D-8: Data rules: PII removal before storage, retention period, deletion on request

Phase later. Size M. Boxes met after the ticks: 0/7.

Edits:

- **W6-18** [MEDIUM, rule 1]: phase `later`, size M stays (7 open boxes). Verdict `tick-blocked` on box 1: the proving test would be `test_a_pii_fixture_is_redacted_in_every_store_write`, in the first data agent that exists. Cite the redaction tests in `## Reuse` so the rule is not rebuilt: `packages/chassis/tests/test_redaction_and_chunks.py::test_redact_masks_emails_phones_and_ids`, `test_collector_log_redaction.py::test_the_log_pipeline_masks_each_entity`. Names stay a gap until the Presidio question (011 section 3, Q6) is answered.

#### 072 C-3: Audit log data agent: append-only, object lock, retention per risk class, export API

Phase later. Size L. Boxes met after the ticks: 0/8.

Fold:

- `072 C-3`, PoC-7 draft (line 15): `agents.feedback.received.v1` lists the audit log as a consumer. Fact for box 1; the event is published and valid, nothing consumes it.

Edits:

- **W6-19** [MEDIUM, rule 5]: add "Decided in ADR-004: a broker client in the chassis; the `dapr` adapter stays a tested alternative", and replace both Dapr mentions with "the chassis consumes the events through its broker client and hands them to the audit workload". Depends on 021 H-20, which is `later` and refused at load (`spec.events.consume`).
- **W6-21** [MEDIUM, rule 1]: `tick-blocked` for boxes 1, 5, 6, and 7. The proving tests would be `test_each_audit_event_type_lands_in_the_store`, `test_a_duplicate_event_is_stored_once`, `test_a_stored_entry_holds_no_pii`, and a kind test that reads the audit workload's environment and mounts (suggested names). The dead-letter retry exists in `EventPortContract` (`packages/contract-suites/src/chassis_contracts/events.py`, `test_after_max_attempts_the_event_goes_to_the_dead_letter_topic`) and is a dependency (053), not a box here.

#### 073 E-1: First evaluator SLM (ModernBERT or DeBERTa) trained on approved records

Phase parked. Size L. Boxes met after the ticks: 0/6.

Fold:

- `073 E-1`, `074 E-2`, `075 E-3`, PoC-7 draft (line 71): "the encoder adapter and the gate cost formula (ADR-010 item 9) are where their numbers plug in; waits on encoder agreement from the Mac run". Goes into the parked bodies as a note; owner list (011 section 1).

Edits:

- With the `ROWS` change (W6-04): `parked`. Keep the body. Add a note: "unpark 034 C-8 first" (W3-18) and "the NLI cross-encoder behind TEI (`EncoderEvaluator`) is the code that exists; the Hugging Face model was never run (403 in PoC-7)". Its `## Why` open concern (the gate's cost) is answered by the judge at rate 0.1, about $5 per million requests (ADR-010 item 9, `docs/specs/models.md`).

#### 074 E-2: Evaluator agreement test against human labels

Phase parked. Size S. Boxes met after the ticks: 0/7.

Edits:

- With the `ROWS` change (W6-05): `parked`. Keep the design (kappa, fail-class recall, a bar fixed before the first run, per-slice results) for a future encoder. Cross-link 028 S-3 box 5 in the parked note: "the judge's agreement is measured there".

#### 075 E-3: Evaluator SLM replaces the big-model judge in the harness

Phase parked. Size S. Boxes met after the ticks: 1/8.

Ticks after the rewrite:

- `075 E-3`, box 8, rewritten to "The `EncoderEvaluator` adapter passes the same `EvaluatorPort` contract suite as the judge and the fake" (W6-07): `· Delivered in PoC-7: packages/contract-suites/src/chassis_contracts/evaluator.py (EvaluatorPortContract, four cases); packages/chassis/tests/test_evaluators.py::TestEncoderEvaluator and ::TestJudgeEvaluator; packages/chassis/tests/test_contracts_cross_cutting_ports.py::TestScriptedEvaluator; pocs/poc-07-cross-cutting-decisions/tests/test_poc07_evals_adapters.py::TestPoc07EvalsEncoderEvaluator; docs/planning/adr/010-evals-offline-tool-online-gate.md item 8`

Edits:

- **W6-07** [MEDIUM, rule 1]: reword to "The `EncoderEvaluator` adapter passes ..." and tick with the evidence in Ticks. The four contract cases run against three bindings (the fake, `TestJudgeEvaluator`, `TestEncoderEvaluator`) and are rebound in PoC-7 (ran, 34 passed). The adapter is the off-the-shelf NLI stub over TEI, not a trained SLM; say so in the box.
- With the `ROWS` change (W6-06): `parked`; keep box 8 ticked (W6-07). Add to the ADR-010 hand-off in `proposals.md`: item 10's encoder default is conditional on an unparked 074; the plan's default is the judge at 0.1 on `decision-small`.
- P-20 (eval-expert): 075 E-3 box 6: stays open, verdict `conflict`.

#### 076 R-1: Registry data model and API

Phase later. Size L. Boxes met after the ticks: 0/8.

Edits:

- **W7-08** [MEDIUM, rule 4]: write the mapping in 076 (`versions.chassis` to `chassis_version`, `lane` to `engine_connector`, `agent.trust` to `runtime_trust`); add an open box to 051 H-13 for the two image digests, class, kind, governance, and the public address (the W5 session owns 051).
- **W7-14** [LOW, rule 5]: name the registry field `trust_level` in 076, 080, 085, 091, and 093, keep `runtime_trust` for `spec.trust`, and add the manifest's `agent.trust` to the note in `000-plan.md`.
- With the `ROWS` change (W7-09): add `059 H-15` to the dependencies of 076 in `ROWS`; name (a) in 076's `## What` as an open decision; list it in the DEC-1 pack as "needed before W7, not before phases A to F".

#### 077 C-1: Governance block enforced by the registry on activation

Phase later. Size S to M. Boxes met after the ticks: 0/9.

Fold:

- `077 C-1`, PoC-7 draft line 55 (`spec.governance`, the load-time refusal): a fact for the box about `limited` with marking off, which stays open as a registry-side check (W7-07). The draft puts it under 023 C-5; W2b owns that.

Edits:

- C1: Receives the E.4 schema box from the W7-02 text (home: 077, not 010 H-12, as W2a-21 also moves `human_oversight` here).
- **W7-02** [MEDIUM, rule 4]: rewrite the `## Why` sentence to say the loader holds two fields; add one box to 077 or to 010 H-12 (W2a-21 already holds the `risk_class` and `vault://` boxes): "the chassis config schema carries the nine E.4 governance fields, required when `risk_class` is `limited` or `high`, with allowed values". This is the part that could land in phase F, when `agentctl` renders a `governance` block (059 H-15).
- **W7-07** [MEDIUM, rule 8]: rewrite both to the case the registry alone guards: "a manually registered agent entry that declares ... is not activated", and add "the registry validates the entry's config with the chassis config model, so the loader's refusals hold". Keep both open (tick-blocked).
- P-16 (platform-security): 056 CH-7 box 4 moves to 026 CH-4; 077 C-1's box on the rule stays open.

#### 079 C-2: Prohibited-practice checklist at registration

Phase later. Size S. Boxes met after the ticks: 0/6.

Edits:

- With the `ROWS` change (W7-18): add `R-16` to the dependencies of 079 in `ROWS`.

#### 080 R-7: Trust levels: internal signed entries go live, external and manual need approval

Phase later. Size M. Boxes met after the ticks: 0/9.

Fold:

- `080 R-7` and `025 H-10`: the PoC-9 template ops note (`pocs/poc-09-agent-mvp-template/notes/2026-10-29-template-ops.md`) records that nothing verifies a signature at admission or deploy; only the CI self-check does. Open item for 080 box 1 and 2.

Edits:

- **W7-04** [MEDIUM, rule 6]: rewrite the boxes to "signed by the platform's CI workflow identity (certificate identity and issuer from a registry setting)"; drop "key" in 080 and 083; add the choice (keyless identity or a key) to the DEC-1 pack as a decision that no phase A to F blocks. The template's own `suggested:` marker on keyless signing stays until 025 H-10 closes.
- With the `ROWS` change (W7-05): add "Decided in ADR-005 items 4 and 5: admission already refuses an image outside the registry prefix and a `trusted` image outside the platform list; this check reads the same list, or the signature once it lands, and landing it reopens ADR-005 decisions 4 and 5" to 080 and 077; add `025 H-10` to the dependencies of 080 in `ROWS` (it is a lower number).

#### 081 R-8: Description scanning for hidden instructions on registration

Phase later. Size M. Boxes met after the ticks: 0/6.

Fold:

- `081 R-8`, "Proposed in PoC-7": decided. ADR-008 was accepted on 2026-10-30. The Snyk job and its 13 offline checks (`pocs/poc-07-cross-cutting-decisions/tests/test_poc07_agent_scan_static.py`) are built. The first real scan waits on a `SNYK_TOKEN` repo secret: owner list (011 section 2), not a box. The PoC-7 draft's open item ("the `scan` run's egress is not limited to `api.snyk.io`") goes to `## Out of scope`. Replace the bullet with "Decided in ADR-008" (W7-12).

Edits:

- **W7-12** [MEDIUM, rule 10]: replace the bullet with "Decided in ADR-008. Built in PoC-7: `poc07-agent-scan` and `deploy/agent-scan/`, with 13 offline checks; no real scan has run (needs `SNYK_TOKEN`). It reports on our own servers and does not replace this scan." The Snyk egress item goes to `## Out of scope`.

#### 083 R-2: Kubernetes controller (kopf): watches labeled deployments and reads /manifest

Phase later. Size M. Boxes met after the ticks: 0/9.

Edits:

- With the `ROWS` change (W7-15): rewrite box 9 to "a new agent from the template carries the registration label and registers through the controller only"; drop the "Removes the registration stub" bullet; reword the `ROWS` note of 025 H-10 to "registration is by label, through the controller (R-2)".

#### 086 R-10: Health checks: dead entries marked inactive, never deleted

Phase later. Size S to M. Boxes met after the ticks: 1/9.

Ticks after the rewrite:

- `086 R-10`, box 3, split. The ticked half is rewritten to "A hung workload behind a live chassis fails `/ready` after 3 failed probes, and `/health` stays 200" (W7-10); the other half stays open as "…and the entry becomes `inactive` after 3 failed checks": `· Delivered in PoC-4: packages/chassis/tests/test_readiness.py::test_ready_goes_false_after_failed_probes_and_health_stays_ok and ::test_the_monitor_turns_unhealthy_after_n_failures_and_back_after_one_success; pocs/poc-04-stateless-scalable/tests/test_liveness.py::test_ready_goes_false_when_the_workload_hangs_and_health_stays_ok; pocs/poc-04-stateless-scalable/notes/2026-10-01-drills.md (Compose pause drill: 503 after 9.7 s and 8.2 s, 200 again after 2.0 s and 1.4 s)`. The `suggested:` marker on "`/ready` checks that the workload answers through the connector" in `## What` is dropped: `ReadinessMonitor.probe()` in `packages/chassis/src/chassis/server/readiness.py` does it.

Fold:

- `086 R-10`: PoC-4 drills (`pocs/poc-04-stateless-scalable/notes/2026-10-01-drills.md`) are the evidence for the ticked half. `backlog-changes.md` of PoC-4 names no W7 issue.

Edits:

- **W7-11** [MEDIUM, rule 1]: rewrite box 8 to "the refreshed `p95_latency_ms`, `error_rate`, and cost on an entry equal the result of the named PromQL queries over the chassis series, within one interval", and list the queries in `## What`, reusing the dashboard expressions.
- With the `ROWS` change (W7-10): resize 077 C-1 and 086 R-10 from S to M in `ROWS`. W7 goes from 36.5 to 39.5 days. For 086, split box 3 as in Ticks and tick the first half. Box 4 (scale to zero) is testable with `kubectl scale --replicas=0`; the chart has an HPA with `minReplicas` of at least 1 and no KEDA, so the box needs no higher-numbered issue (104 O-3).

#### 087 R-4: MCP import: call tools/list and register each tool

Phase later. Size S. Boxes met after the ticks: 0/6.

Edits:

- **W7-20** [LOW, rule 6]: reword to "a secret reference (a name resolved by the chassis's secret mechanism)" and drop the Vault name until X-1a closes.

#### 088 R-5: OpenAPI import: register each operation of a REST API

Phase later. Size M. Boxes met after the ticks: 0/6.

Edits:

- **W7-16** [LOW, rule 1]: name the validator in 051 H-13 (the W5 session) and say in 088 that it reuses it; add the OpenAPI 3.0 note ("suggested: also 3.0") to the validator choice.

#### 089 R-15: Event schemas (AsyncAPI) registered next to OpenAPI specs

Phase later. Size S. Boxes met after the ticks: 0/6.

Edits:

- **W7-21** [LOW, rule 1]: keep open; add one sentence to `## What`: the first schema set comes from `packages/chassis/schemas/`, and the pattern check lives in the registry, not the chassis. Tick-blocked.

#### 091 R-11: Hybrid search: SQL filters, keyword, vector, reciprocal rank fusion, reranker

Phase later. Size L. Boxes met after the ticks: 0/7.

Edits:

- **W7-03** [MEDIUM, rule 13]: drop `S-7` in `ROWS`; keep `G-1b`. Rewrite the vLLM bullet: embedding and reranker models are called through named routes (`registry-embed`, `registry-rerank`) on whatever server the route map names, hosted or self-hosted; the Appendix I comparison (box 3) decides. Size L stays.

#### 092 R-12: Search results return the main pick plus ranked fallbacks with difference metadata

Phase later. Size M. Boxes met after the ticks: 0/7.

Edits:

- **W7-22** [LOW, rule 13]: say the example is a fixture pair, and name two generic entries (a main and a fallback) in the fixture; the F.3 names stay in the epic.
- With the `ROWS` change (W7-17): add `R-10` to the dependencies of 092 R-12 and `R-7` to those of 093 R-14 in `ROWS` (both are lower numbers).

### PR W8toW12: issues 094 to 127 (`docs/backlog-ticks-W8toW12`)

Inputs never applied, as the findings read them (context for this PR):

- No issue in 094–115 has a "Status after PoC-N" bullet. The inputs never applied name none of these issues. Two PoC facts belong here and are folded as new "Status after PoC-N" bullets (W89-22, W89-20):
- Inputs never applied: the PoC-7 draft (`pocs/poc-07-cross-cutting-decisions/notes/backlog-changes.md`) names none of 116–127. The PoC-6c "to apply" list names none. The PoC-9 W2–W5 report names none. The PoC-4 backlog-changes file names `122 X-5` twice (criteria 4 and 11); both are the bullets above. PoC-8's file names none of these issues; its open questions Q1 (`/metrics` open by default) and Q4 (the feedback panel) touch 117's dashboard and go to DEC-1.

#### 094 P-1: Platform MCP server built from the harness, with registry and agent tools

Phase later. Size S to M. Boxes met after the ticks: 0/7.

Edits:

- **W89-07** [MEDIUM, rule 12]: 094 builds the outbound agent call (a chassis feature, with a fake and a contract case) and 099's agent port reuses it; resize M. The caller-identity "token exchange" bullet needs an ADR-008 note: ADR-008 says the token is never forwarded to the workload; this is chassis to agent.
- **W89-08** [MEDIUM, rule 5]: add "Decided in ADR-003 item 6" and cut the streaming half of box 3 (and the "Suggested: streaming" sentence) until FastMCP can stream an OpenAPI tool.

#### 095 P-4: MCP read and write scopes, confirmation on destructive tools, audit log on every call

Phase later. Size M. Boxes met after the ticks: 0/6.

Edits:

- **W89-09** [MEDIUM, rule 5]: amend ADR-008 or rewrite the scope names; say which layer filters tools; drop the `input-required` suggestion until W89-05's interrupt ADR exists. Size M stays.
- P-21 (platform-security): 095 P-4 box 6 stays open; the row gains `conflict`.

#### 096 P-2a: MCP golden set and metrics tools

Phase later. Size M. Boxes met after the ticks: 0/6.

Edits:

- **W89-26** [LOW, rule 13]: reword to "the container dashboard's token and cost panels for the same agent and period" (046 A-5, phase E); keep the dependency.

#### 097 P-3: MCP resources and prompts

Phase later. Size S. Boxes met after the ticks: 0/6.

Edits:

- **W89-23** [LOW, rule 5]: say "from the log backend chosen in 014 H-7"; the dataset resource waits on lakeFS (069 D-7).

#### 098 O-10: Orchestrator config and workflow schemas (JSON Schema) in the config store

Phase later. Size M. Boxes met after the ticks: 0/6.

Edits:

- **W89-17** [MEDIUM, rule 4]: add a box to 098 for the dispatch and the reloadable set (W2a owns 010's verdict); reword to the real field names or keep `kind` as new. Size M holds.

#### 099 O-1: Orchestrator agent built from the harness, using registry search

Phase later. Size M. Boxes met after the ticks: 0/7.

Edits:

- **W89-02** [HIGH, rule 5]: add an ADR (`chassis-architect`) before 099: where the worker runs, the Temporal credential, the task-queue adapter. Then add "Decided in ADR-0NN" to 099 and 100 and drop the `suggested:` marks.
- **W89-06** [MEDIUM, rule 13]: use the echo agent (`packages/workloads/echo-*`) and a fake evaluator as the reference agents; rename the example workflows to `echo-long-doc` and `compare-routes`; 106 box 1 compares two routes from the route map (`docs/specs/models.md`), not "SLM vs big model". The epic's example files stay in the epic.

#### 100 O-2: Temporal workflows with a checkpoint per step

Phase later. Size M to L. Boxes met after the ticks: 0/8.

Edits:

- C1: Receives 113 O-9 box 1: "The run status shows plan, steps, step status, and input and output references, for a running and a finished run", read from a Temporal query through the run API. 100 is at the top of L after this; re-check its size when W9 leaves `later`.
- With the `ROWS` change (W89-03): resize L in `ROWS`. Keep the dependencies. The 050 CH-5 dependency is real (no `operations` key exists); W5 owns its verdict.

#### 101 O-13: Version pinning per run

Phase later. Size S to M. Boxes met after the ticks: 0/5.

Edits:

- **W89-16** [LOW, rule 1]: leave open. The proving tests are `test_an_mcp_call_replays_on_the_same_idempotency_key`, `test_a_request_keeps_the_config_it_opened_with_across_a_reload`, and for 107 a per-run test that needs step accounting.

#### 102 O-7: Idempotent steps, so a resume never repeats a side effect

Phase later. Size M. Boxes met after the ticks: 0/5.

Edits:

- **W89-24** [LOW, rule 5]: add the three facts to `## What`; no new dependency.

#### 104 O-3: Agent pools on task queues with KEDA autoscaling

Phase later. Size M to L. Boxes met after the ticks: 0/7.

Fold:

- `104 O-3`, PoC-4 drain, kill, and rolling-restart results (`pocs/poc-04-stateless-scalable/demo/2026-10-01-demo-scale.md`, `notes/2026-10-01-container-roles.md`) and the PoC-9 HPA drill (`pocs/poc-09-agent-mvp-template/notes/2026-11-05-kind-hpa-drill.md`, 2 to 4 replicas at 203% CPU, scale-down not waited for): facts, as a bullet. They do not tick a box.

Edits:

- C1: Receives 120 X-7's per-pool boxes as one box: "per-pool hosting cost, scale-to-zero, and replica-hours on the cost dashboard, with rates from config".
- **W89-22** [MEDIUM, rule 10]: add the "Status after PoC-4 and PoC-9" bullet from the folded list; mark the `remote` and KEDA parts `later`; 025 H-10 can go from deps because the chart values live in `agent-lib` (W2b confirms). Size L.
- With the `ROWS` change (W89-01): drop `O-3` from the `ROWS` dependencies of 122 X-5 and 120 X-7; the "per pool" half of each moves to a box that waits on 104. W1012 decides; also 122 depends on 091 R-11 (registry, W7).

#### 105 O-4: Fan-out and fan-in with overlap and a consistency pass

Phase later. Size M. Boxes met after the ticks: 0/6.

Edits:

- **W89-12** [MEDIUM, rule 5]: replace "`evaluator-facts` agent" with "the `EvaluatorPort` judge"; the 0.95 stays a workflow field.

#### 107 O-6: Budgets per run: max tokens, cost, steps, and time

Phase later. Size S to M. Boxes met after the ticks: 0/7.

Fold:

- `107 O-6`, the per-request budget (contract section 7, "Budgets per call"; `docs/contracts/contract-v1.md` per-run budget): a fact, as a bullet.

Edits:

- **W89-20** [MEDIUM, rule 10]: add a "Status after PoC-4" bullet; box 3 and 7 need a contract change (`chassis-architect`); resize M.

#### 108 O-12: Routing rules and planner modes (fixed, planned, hybrid)

Phase later. Size L. Boxes met after the ticks: 0/6.

Edits:

- **W89-13** [MEDIUM, rule 5]: the planner validates the model's JSON against `workflow.schema.json` and retries once; the route is `planner` in the route map. 108 does not depend on a parked issue; it only blocks the parked 127 L-3. Size L holds.

#### 110 C-6: Human oversight controls: review queue, approval steps, stop control, all audited

Phase later. Size M. Boxes met after the ticks: 0/7.

Edits:

- With the `ROWS` change (W89-14): add `C-1` (077) to 110's `ROWS`; put the reviewer role and write scope into DEC-1 and the owner list.

#### 111 O-14: Workflow validation and dry run before activation, and rollback

Phase later. Size M to L. Boxes met after the ticks: 0/5.

Edits:

- **W89-21** [MEDIUM, rule 5]: resize L; add the port change to `## What`; a dry run uses a key whose allow-list holds no write tools.

#### 112 O-16: Event triggers: workflows started by events, run and step events at each stage

Phase later. Size M. Boxes met after the ticks: 0/7.

Edits:

- **W89-19** [MEDIUM, rule 5]: use `subject` for the request id; state the `partitionkey` rule; add `H-21` or drop the AsyncAPI box; Q3 goes to the owner list. The dep on 021 stays.

#### 113 O-9: Short-term memory (execution state) and long-term memory (settings, gotchas, feedback)

Phase later. Size L. Boxes met after the ticks: 0/7.

Edits:

- C1: Re-scope, not split. Box 1 (run status) moves to 100 O-2. Box 6 (feedback) moves to `## Out of scope` with the W89-11 text. The issue is long-term memory plus the Phase 7 end-to-end test (box 8). Size L stays.
- **W89-11** [MEDIUM, rule 5]: remove feedback from the memory store; `feedback_add` wraps `/v1/feedback`; the golden set path follows ADR-009 item 6.

#### 116 X-1b: Terraform module for the second cloud, with the same inputs and outputs

Phase later. Size L. Boxes met after the ticks: 0/8.

Edits:

- **W1012-07** [MEDIUM, rule 5]: add "Decided in ADR-005 (items 1, 4) and ADR-006"; replace Kyverno with the ValidatingAdmissionPolicy from `deploy/kind/poc05/admission/`; delete the managed-runtime auth bullet and box 6 (moot); keep the gVisor and CNI bullets. Same fix as W3-12 for 038.
- **W1012-08** [MEDIUM, rule 13]: remove the GPU pools, the weights bucket, and the GPU Operator from `## What`, and keep them as "Out of scope: parked with the SLM track". Phase `later`; size L stays (cluster, buckets, networking, IAM, add-ons, environments, and the contract check are still 4 to 5 days).
- **W1012-22** [LOW, rule 7]: when W1012-07, -08, and -11 land, keep the epic story links and add "the product plan needs no cloud before phase F" to the Why of 116 and 121.

#### 117 X-3: SLOs and alerts for every agent

Phase E. Size M. Boxes met after the ticks: 0/7.

Tick-blocked:

- `117 X-3`, box 4 (fallback above 10% fires an alert): the counter and the panel exist; no rule. Proving test: a Compose drill (suggested: `test_a_fallback_rate_above_ten_percent_fires_the_alert`).
- `117 X-3`, box 6 (dashboard shows SLOs and error budget): `test_every_panel_names_a_metric_the_chassis_exports` proves the seven panels, none is an SLO or budget panel. Proving test: the same generator test over an SLO panel set.

Edits:

- C1: Under `## Out of scope`: "Alerts for parts that do not exist yet (broker lag, dead letters, Temporal runs, release rings, the minimum chassis version, the `remote` endpoint) join these rules when 019 H-17, 053 H-22, 100 O-2, and 056 CH-7 ship." No issue below 117 may depend on its alerts.
- C1: The box total is 6 today and 7 after the rewrite (the weekly note is new).
- **W1012-02** [HIGH, rule 14]: rewrite the open boxes to run on Compose and kind: (1) "A newly deployed agent gets SLO rules (availability, p95, error rate, fallback rate) generated from an `slo` block with chart defaults, no manual step"; (2) "A fault test on the Compose stack (`deploy/compose/poc08`, a fake agent returning errors) fires the burn-rate error alert and it reaches the Alertmanager receiver"; (3) the p95 latency fault test; (4) "a gate fallback rate above 10% fires an alert" (the counter and panel exist); (5) "an alert carries the chassis version from a `chassis_build_info` metric" (no such label exists today); (6) "each agent dashboard and the container dashboard show each SLO target and the remaining error budget"; (7) new: "one week of real SLO numbers is recorded in a weekly note (`record-measurement`) next to the north star". Size M stays: five bullets leave (see W1012-03) and two boxes join. Phase E.
- With the `ROWS` change (W1012-03): in `ROWS` set 117's dependencies to `["014 H-7"]` (the metrics and dashboards it builds on). Move the per-ring, minimum-version, and `remote`-endpoint alert bullets into 056 CH-7 (the W5 session decides), and the broker-lag, dead-letter, and Temporal-run alerts into the issues that add those components (019 H-17, 100 O-2). Change "staging" to "the Compose stack" in boxes 1–6. Confirms W3-09.

#### 118 C-7: Incident flow: incident events, alert routing, runbook for serious incidents

Phase later. Size M. Boxes met after the ticks: 0/5.

Edits:

- **W1012-21** [LOW, rule 5]: add "needs a `chassis-architect` pass and a schema version note" to the box; keep 118 `later`; its dependencies (072 C-3 and 117) stay.

#### 119 X-6: Runbooks for common failures

Phase later. Size M. Boxes met after the ticks: 0/4.

Edits:

- **W1012-18** [LOW, rule 10]: keep 119 `later`; rewrite the list as "the failures of components that exist" first (router outage and fallback rate, a bad chassis release, a compromised workload, a `remote` auth failure, bad config), and cite `poc-05-runbooks.md` as the base, adding rollback and escalation. Have 117's alerts link `runbook_url` to it as it stands.

#### 120 X-7: Cost dashboard per agent and per pool

Phase E. Size S. Boxes met after the ticks: 0/6.

Tick-blocked:

- `120 X-7`, box 1 (cost per agent): the per-hour panel exists; no day, week, or month windows and no per-pool cost. After the split, X-7a box 1 can tick once the windows are added.

Edits:

- C1: Re-scope, not split. Keep cost per agent and per good task for a day, a week, and a month on the container dashboard (046 A-5); API cost matches the model proxy's counts; rates are config, not 031 G-5. Box 4 (the baseline) reads "next to the first week's number".
- C1: Per-pool hosting cost, scale-to-zero, and replica-hours move to 104 O-3. The sidecar's cost line moves to 122 X-5's report: 120 must not depend on 122.
- C1: Box 1 is tick-blocked (the per-hour panel exists; no day, week, or month window).

#### 121 X-4: Backups and a tested restore for Postgres, object storage, and golden sets

Phase later. Size M. Boxes met after the ticks: 0/6.

Edits:

- **W1012-20** [LOW, rule 6]: keep the markers on 117, 121, and 122b. In X-5a, state the 2× rule as the PoC-4 note's reading, cited, and keep `suggested:` until the owner accepts it.
- With the `ROWS` change (W1012-11): reword box 6 to "on the first cloud" and drop "both clouds" from the Why, or add `116 X-1b`; drop `069 D-7` and the golden-set and lakeFS bullets (the golden set restores from git). Keep 038. Phase `later`. Size M stays: Postgres PITR, bucket copy, schedule, and the restore test are about 2 to 3 days.

#### 122 X-5: Load tests: throughput and sidecar overhead on a Linux host

Phase A. Size M. Boxes met after the ticks: 0/8.

Tick-blocked:

- `122 X-5`, box 7 (sidecar p95 CPU and memory, hop p50 and p95, next to ADR-001): the note `pocs/poc-04-stateless-scalable/notes/2026-10-01-load-results.md` gives CPU and memory as a mean and a max, and the hop at p50 1.0 ms and p95 0.0 ms below the method's resolution (`pocs/poc-04-stateless-scalable/tests/test_load_results.py::test_the_sidecar_hop_is_recorded` checks that the numbers are recorded, not that they are p95 or isolated). The proving test would assert a recorded p95 for CPU and memory from a Linux run (suggested: `test_the_sidecar_p95_cpu_and_memory_are_recorded`) and a chassis-side hop timer.

Fold:

- `122 X-5`, "Status after PoC-4": facts into the open boxes of X-5a. The CPU figures (0.105 to 0.129 vCPU at 10 RPS, at or above the top of 0.05–0.1; memory 150 to 182 MiB, inside 128–256) become the "before" numbers in the sidecar box. The Revisit-rule input ("at or above the top of the range") goes to the owner list (011 section 3, PoC-4 items), not a box. "This issue still sets the chart's requests from a cluster p95 and isolates the hop" stays as open boxes. The throughput rerun becomes X-5a box 1.
- `122 X-5`, "Status after PoC-9": out of scope, a fact. `LoadSmoke` (`packages/agent-testkit/src/agent_testkit/suites.py::LoadSmoke.test_concurrent_runs_all_complete`, 24 calls) is a smoke test and does not close any box; move it to `## Out of scope` as "not a substitute".

Edits:

- C1: Split: the number stays phase A. Move boxes 1 to 5 (pool scale to zero, pool p95, registry p95 at 5,000 entries, registry top 3, event burst) to `## Out of scope` as "moved to X-5b (added in PR-R)". Box 6 (a report per run) stays and is copied to X-5b.
- C1: Add the W1012-01 boxes: (1) "Throughput grows with replicas for every engine on a Linux host with pinned CPUs, numbers recorded; the four strict xfails in `pocs/poc-04-stateless-scalable/tests/test_load_results.py::test_throughput_grows_with_pairs` (`RECORDED_EXCEPTIONS`) pass and the list is emptied; PoC-4 criterion 4 is ticked in its README". Box 7 becomes (2): "the report gives the sidecar's p95 CPU and memory and the local hop at p50 and p95, isolated by a chassis-side timer, next to ADR-001's 0.05 to 0.1 vCPU, 128 to 256 MiB, and 1 to 3 ms, and the sidecar's cost at the configured rates". Box 8 becomes (3): "the library chart's requests are set from that p95, and the report says whether the ADR-001 Revisit trigger is met (suggested: more than twice the upper estimate)".
- C1: Retitle in ROWS: "Load tests: throughput and sidecar overhead on a Linux host". Deps: none (ROWS).
- **W1012-12** [MEDIUM, rule 1]: leave it open as `tick-blocked` (see Ticks). Name the Linux host as an owner-list item (011 section 3): the rerun needs a host with enough cores for 8 chassis and workload containers at 1 vCPU each plus Traefik, Valkey, MinIO, and the model server; `make load-test` and `python -m load_tools.run_matrix --engine X` run it.
- **W1012-19** [LOW, rule 10]: add one X-5a box: "the 502 rate at 4 pairs is recorded and the source (chassis or Traefik) is named".
- With the `ROWS` change (W7-01): drop `R-11` and `O-3` from 122 X-5 in `ROWS`; the 4-pair throughput box stands alone. Move the registry load box to a new `122b` or into 091 R-11, as a `later` box. The W1012 session confirms the box split.

#### 123 C-4: Documentation pack generator: technical docs, instructions for use, model card per agent version

Phase later. Size M. Boxes met after the ticks: 0/6.

Edits:

- **W1012-09** [MEDIUM, rule 13]: drop `032 M-1` from `ROWS`; rewrite the model card to "the route map entry (`spec.models`), the provider, the golden-set scores from `evals/gate/baseline.json`, and the chassis version from `Versions`" (suggested); drop "training compute" and the ML-BOM item; keep 051 H-13 (the manifest serves the OpenAPI sha256: `packages/chassis/tests/test_manifest.py::test_manifest_matches_the_config_and_the_openapi_spec`). Keep 052 H-21: no AsyncAPI file exists. Box 3 ("no `governance` block fails the build") needs more than `GovernanceSpec` has: today it holds `risk_class` and `ai_generated_marking` only (`packages/chassis/src/chassis/server/config.py:383`), so the intended purpose, owner, and impact-assessment fields in the box have no source until 078 R-16 or the config schema adds them. Phase `later`; size M stays.
- With the `ROWS` change (W3-14): drop `M-1` in `ROWS`; the model card's source becomes the manifest and the response `Versions` (chassis, config, prompt, model_route; `packages/chassis/src/chassis/core/envelope.py`); the W1012 session rewrites the box.

#### 124 C-9: Control mapping report: ISO 42001 Annex A and AI Act articles to platform evidence

Phase later. Size S. Boxes met after the ticks: 0/5.

Edits:

- **W1012-10** [MEDIUM, rule 1]: cite for 124 (Art. 50(2) row): contract anchor `docs/specs/contract.md`, "AI-generated marking (`server/marking.py`, `adapters/ai_marking`)" in section 8 (also `docs/contracts/contract-v7.md`, "E. AI-generated marking"), and the tests `packages/chassis/tests/test_marking.py::test_limited_marks_every_interface_streaming_and_complete`, `::test_marking_false_is_refused_for_limited_and_high`, `packages/chassis/tests/test_marking_body.py::test_the_openai_body_carries_the_flags_complete_and_on_every_chunk`, `::test_result_events_carry_the_aigenerated_extension` (ran, passed). Mark A2A and the native-envelope body as the listed gap. 023 C-5's own open boxes (the client guide, the contract-suite case, the overwrite shown for headers only) belong to the W4 session; the 2 December date makes them time-bound, so I raise it for the coordinator. 124 stays `later`; the marking itself is 023's work, not this report's.

#### 125 L-1: Shared memory at scale: session context, long-term memory, context_ref

Phase later. Size L to M. Boxes met after the ticks: 0/5.

Edits:

- **W1012-15** [MEDIUM, rule 14]: add to `## References` and `## What`: "starts from the phase C checkpointer ADR; layer 1 is that ADR's choice until the orchestrator exists". No dependency edge (ADRs are not issues). `context_ref` already exists (`packages/chassis/src/chassis/core/envelope.py:61,77`), so box 3 can only be met by the design note. Phase `later`, size M (see W1012-17).
- With the `ROWS` change (W1012-17): resize both to M (2 to 3 days). Suggested, not measured; the W9 and W6 sessions' results can move it. 127 stays L (parked, so it counts for nothing).

#### 126 L-2: System prompt improver

Phase later. Size L to M. Boxes met after the ticks: 0/5.

Edits:

- **W1012-14** [MEDIUM, rule 13]: tagger = the cheap judge route (`decision-small`, behind `EvaluatorPort`) or a reviewer edit, not an SLM; keep rule = "beats the current prompt on the golden set and never falls below the baseline floor, through the eval gate as W3 rewrites it", with the absolute floor left to the DEC-1 pack; replace `prompts.system_ref` with "a prompt file in the config store, pinned per run" as the design note's choice; drop `075 E-3`. Phase `later`.
- With the `ROWS` change (W6-03): in `ROWS`, replace `E-3` with `A-2` (042, the gate and the judge) and keep `D-4`; the W1012 session rewrites the text from "the evaluator SLM" to "the judge behind `EvaluatorPort`". Its `blocks` entry on 075 goes away by regeneration.

#### 127 L-3: Router SLM: classifier that replaces part of the orchestrator's routing

Phase parked. Size L. Boxes met after the ticks: 0/5.

Edits:

- With the `ROWS` change (W1012-16): verdict `parked`, phase `parked`, size L unchanged; add one line "Parked 2026-11-06 with the SLM track (product plan)". Nothing depends on it, so no dependency edit. Unpark as `later` if a trained router returns.

## 4. Totals

`DAYS = {S: 1, M: 2.5, L: 5}` from `sync.py`. "Before" is `ROWS` today, with each row given the phase this review gives it; parked rows count as `later` there, since `WP` does not exist yet. "After" applies section 1 and the six new rows. Open totals leave out `WD` and `WP`; the W0 to W11 line also leaves out W12, as `sync.py` does.

The change set, as data (`changes.py`):

```python
"""The C1 change set for docs/planning/tools/backlog.py ROWS, as data. Read-only use."""

WD = ["H-2"]
WP = [
    "G-3",
    "S-1",
    "S-2",
    "S-4",
    "G-5",
    "M-1",
    "S-5",
    "C-8",
    "S-6",
    "S-7",
    "X-2",
    "A-1",
    "A-3",
    "G-4",
    "A-4",
    "M-3",
    "M-4",
    "M-5",
    "S-8",
    "E-1",
    "E-2",
    "E-3",
    "L-3",
]

SIZE = {
    "G-1b": "M",
    "G-6": "S",
    "H-1": "S",
    "H-14": "L",
    "CH-1": "S",
    "H-3": "L",
    "H-7": "M",
    "H-8": "M",
    "H-4": "M",
    "X-8": "L",
    "H-20": "M",
    "CH-3": "L",
    "H-10": "M",
    "CH-4": "L",
    "S-3": "S",
    "M-2": "S",
    "D-0": "L",
    "A-2": "M",
    "A-5": "M",
    "H-13": "M",
    "H-22": "M",
    "H-16": "L",
    "CH-6": "S",
    "CH-7": "L",
    "H-19": "M",
    "CH-8": "L",
    "H-15": "L",
    "C-1": "M",
    "R-10": "M",
    "P-1": "M",
    "O-2": "L",
    "O-13": "M",
    "O-3": "L",
    "O-6": "M",
    "O-8": "M",
    "O-14": "L",
    "L-1": "M",
    "L-2": "M",
}

DEPS = {
    "H-7": ["H-1", "CH-1"],
    "H-9": ["H-2", "CH-1"],
    "CH-4": ["G-1", "CH-3"],
    "S-3": [],
    "M-2": ["S-3"],
    "A-2": ["H-4", "S-3"],
    "A-5": ["DEC-1", "H-7", "A-2"],
    "H-22": ["H-18", "H-20", "D-0"],
    "H-16": ["H-18", "H-4", "H-6"],
    "CH-6": ["CH-1", "CH-4", "H-12", "CH-3"],
    "CH-7": ["CH-3", "CH-6", "H-7", "H-13", "X-1a"],
    "H-19": ["H-10", "H-8"],
    "CH-8": ["H-8"],
    "H-15": ["H-10", "H-19", "CH-8", "CH-6", "CH-3"],
    "H-23": ["H-20", "H-22"],
    "H-11": ["H-2", "CH-2", "CH-8", "H-10"],
    "D-1": ["H-4", "D-0", "A-2", "S-3"],
    "D-3": ["D-5", "M-2"],
    "R-1": ["H-14", "H-13", "H-17", "H-6", "H-15"],
    "C-2": ["C-1", "R-16"],
    "R-7": ["R-1", "H-10"],
    "R-11": ["R-1", "R-13", "G-1b"],
    "R-12": ["R-11", "R-10"],
    "R-14": ["H-13", "R-12", "R-7"],
    "O-1": ["H-14", "R-12", "O-10", "H-15"],
    "C-6": ["D-2", "C-3", "O-8", "C-1"],
    "O-9": ["O-2"],
    "X-3": ["H-7"],
    "X-7": ["G-2"],
    "X-4": ["X-1a"],
    "X-5": [],
    "C-4": ["H-13", "H-21", "R-16"],
    "L-2": ["D-4", "A-2"],
}

TITLE = {
    "G-6": "Token and cost dashboard with live spend",
    "H-3": "Model port to the LLM router, with a route map per task",
    "S-3": "`facts_kept` and banned-word scoring for the eval gate and the judge",
    "M-2": "Eval gate in CI: a change ships only if it passes the golden set against the baseline",
    "A-5": "Container dashboard and per-agent gate panels",
    "X-5": "Load tests: throughput and sidecar overhead on a Linux host",
}

# Appended after 127, so every existing number stays. Same tuple shape as ROWS.
NEW = [
    (
        "CH-9",
        "LangGraph checkpointer and interrupts: one ADR each",
        "langgraph-checkpointer-interrupts",
        "W5",
        "0",
        "harness",
        "P2",
        "S",
        ["CH-8"],
        ["F.1", "B.5"],
        "story",
        "From the product plan, phase C (gap P3). Owner: chassis-architect.",
    ),
    (
        "CH-10",
        "LangSmith as a trace backend: Collector exporter, fake-langsmith, stack tests",
        "langsmith-trace-backend",
        "W5",
        "0",
        "harness",
        "P1",
        "M",
        ["H-7"],
        ["G.2"],
        "story",
        "From the product plan, phase D (gap P1). ADR-007 item 10.",
    ),
    (
        "CH-11",
        "LangSmith feedback adapter behind FeedbackPort",
        "langsmith-feedback-adapter",
        "W5",
        "0",
        "harness",
        "P1",
        "S",
        ["CH-10"],
        ["G.2"],
        "story",
        "From the product plan, phase D (gap P1). ADR-009 item 3a.",
    ),
    (
        "CH-12",
        "Golden cases in Langfuse and LangSmith datasets",
        "golden-cases-datasets",
        "W6",
        "2",
        "lifecycle",
        "P1",
        "S",
        ["M-2", "CH-10"],
        ["E.5", "#principles"],
        "story",
        "From the product plan, phase D. ADR-010 trial rule.",
    ),
    (
        "CH-13",
        "OWASP agentic threat list (ASI01 to ASI10) mapped to the controls",
        "owasp-agentic-map",
        "W11",
        "9",
        "governance",
        "P1",
        "S",
        ["CH-4", "H-6"],
        ["E.5", "R12"],
        "story",
        "From the product plan, phase E (gap P2). Owner: platform-security.",
    ),
    (
        "X-5b",
        "Load tests for agent pools, the registry, and event bursts",
        "load-tests-pools-registry",
        "W10",
        "8",
        "infra",
        "P1",
        "M",
        ["R-11", "O-3", "X-3"],
        ["G.4"],
        "story",
        "Split from X-5 on 2026-11-06: 122 X-5 keeps the phase A rerun on Linux.",
    ),
]

PHASE = {}
for p, ids in {
    "A": ["H-1", "CH-1", "H-12", "H-18", "CH-3", "CH-4", "CH-6", "X-5"],
    "B": ["G-1", "G-1b", "H-3", "CH-2", "H-4", "S-3", "M-2", "A-2"],
    "C": ["CH-8", "CH-9"],
    "D": ["CH-10", "CH-11", "CH-12"],
    "E": ["DEC-1", "G-2", "G-6", "H-7", "H-6", "A-5", "H-16", "X-3", "X-7", "CH-13"],
    "F": ["H-8", "H-9", "H-10", "H-19", "H-15"],
}.items():
    for i in ids:
        PHASE[i] = p
```

The snippet (`totals.py`; it imports `backlog.py` and writes nothing):

```python
"""Read-only: apply the C1 change set to a copy of ROWS, check the rules, print the totals."""

import collections
import sys

sys.path.insert(0, sys.argv[1])  # docs/planning/tools
sys.path.insert(0, sys.argv[2])  # the folder that holds changes.py
import backlog  # noqa: E402
import changes as c  # noqa: E402

DAYS = {"S": 1, "M": 2.5, "L": 5}
OUT = {"WD", "WP"}


def phase_of(eid, wave):
    if wave == "WD":
        return "done"
    if wave == "WP":
        return "parked"
    return c.PHASE.get(eid, "later")


def apply(rows):
    out = []
    for r in rows:
        eid, title, slug, wave, ph, area, prio, size, deps, refs, typ, note = r
        wave = "WD" if eid in c.WD else "WP" if eid in c.WP else wave
        out.append(
            (
                eid,
                c.TITLE.get(eid, title),
                slug,
                wave,
                ph,
                area,
                prio,
                c.SIZE.get(eid, size),
                c.DEPS.get(eid, deps),
                refs,
                typ,
                note,
            )
        )
    return out + list(c.NEW)


def check(rows):
    idx = {r[0]: n for n, r in enumerate(rows, 1)}
    wave = {r[0]: r[3] for r in rows}
    problems = []
    for n, r in enumerate(rows, 1):
        for d in r[8]:
            if idx[d] >= n:
                problems.append(f"{n:03d} {r[0]} depends on a later issue {d}")
            if r[3] not in OUT and wave[d] == "WP":
                problems.append(f"{n:03d} {r[0]} (live) depends on parked {idx[d]:03d} {d}")
            if r[3] not in OUT and wave[d] == "WD":
                problems.append(f"note: {n:03d} {r[0]} depends on done {idx[d]:03d} {d}")
    return problems


def totals(rows, label):
    by_wave, by_phase = collections.Counter(), collections.Counter()
    n_wave, n_phase = collections.Counter(), collections.Counter()
    for r in rows:
        days = DAYS[r[7]]
        by_wave[r[3]] += days
        n_wave[r[3]] += 1
        p = phase_of(r[0], r[3])
        by_phase[p] += days
        n_phase[p] += 1
    print(f"== {label}")
    order = list(backlog.WAVES) + ["WD", "WP"]
    print("wave   issues  days")
    for w in order:
        if n_wave[w]:
            print(f"{w:6} {n_wave[w]:6}  {by_wave[w]:g}")
    core = sum(v for w, v in by_wave.items() if w not in OUT | {"W12"})
    print(
        f"open W0-W11: {core:g} days; open W12: {by_wave['W12']:g}; "
        f"done: {by_wave['WD']:g} ({n_wave['WD']} issues); parked: {by_wave['WP']:g} ({n_wave['WP']} issues)"
    )
    print("phase   issues  days")
    for p in ["A", "B", "C", "D", "E", "F", "later", "done", "parked"]:
        print(f"{p:7} {n_phase[p]:6}  {by_phase[p]:g}")
    print(
        f"open A-F: {sum(by_phase[p] for p in 'ABCDEF'):g} days; all open: "
        f"{sum(v for p, v in by_phase.items() if p not in ('done', 'parked')):g} days"
    )


before = list(backlog.ROWS)
after = apply(before)
totals(before, "before (ROWS today, phases from the review)")
totals(after, "after (ROWS with the C1 change set)")
print("== checks")
probs = check(after)
print(
    "\n".join(probs)
    if probs
    else "no dependency on a later issue; no live issue depends on a WP row"
)
print(f"rows: {len(before)} before, {len(after)} after")
```

```
$ python3 -I totals.py docs/planning/tools <scratch dir>
== before (ROWS today, phases from the review)
wave   issues  days
W0          1  1
W1          5  11
W2         20  56.5
W3         13  30
W4         10  17.5
W5         14  29.5
W6         12  26
W7         18  36.5
W8          4  7
W9         18  46.5
W10         7  18.5
W11         2  3.5
W12         3  15
open W0-W11: 283.5 days; open W12: 15; done: 0 (0 issues); parked: 0 (0 issues)
phase   issues  days
A            8  25
B            8  19.5
C            1  2.5
D            0  0
E            9  16.5
F            5  14.5
later       96  220.5
done         0  0
parked       0  0
open A-F: 78 days; all open: 298.5 days
== after (ROWS with the C1 change set)
wave   issues  days
W0          1  1
W1          4  8.5
W2         19  54
W3          3  7
W4          3  10
W5         16  39.5
W6         10  20
W7         18  39.5
W8          4  8.5
W9         18  58.5
W10         8  21
W11         3  4.5
W12         2  5
WD          1  5
WP         23  52.5
open W0-W11: 272 days; open W12: 5; done: 5 (1 issues); parked: 52.5 (23 issues)
phase   issues  days
A            8  20.5
B            8  19.5
C            2  6
D            3  4.5
E           10  21.5
F            5  13.5
later       73  191.5
done         1  5
parked      23  52.5
open A-F: 85.5 days; all open: 277 days
== checks
note: 015 H-8 depends on done 011 H-2
note: 016 H-9 depends on done 011 H-2
note: 021 H-20 depends on done 011 H-2
note: 022 H-6 depends on done 011 H-2
note: 023 C-5 depends on done 011 H-2
note: 050 CH-5 depends on done 011 H-2
note: 051 H-13 depends on done 011 H-2
note: 061 H-5 depends on done 011 H-2
note: 062 H-11 depends on done 011 H-2
rows: 127 before, 133 after
```

Read-out:

- Open work, W0 to W11: 283.5 days before, 272 after (the baseline's number matches). Retiring takes out 5 days and parking 47.5 (at today's sizes); the six new rows add 9; the resizes add 32 net.
- Done: 1 issue, 5 days (011 H-2). Parked: 23 issues, 52.5 days.
- Phases A to F: 78 days before, 85.5 after, about 17 engineer-weeks. The product plan's suggested sizes add to 10 to 12 weeks, so the gap is about 5 to 7 weeks; most of it is phase A (the chart and egress boxes) and phase E (the container dashboard).
- Phase D is the only phase that had no issue. It now has three, 4.5 days.
- The "9 notes" under checks are edits that depend on a done row (011 H-2). They are allowed: a done row counts as met.

## 5. The PR sequence

From the plan's "Apply phase", with what this review found.

| PR | Branch | Content | Gate |
| -- | ------ | ------- | ---- |
| PR-1 | `docs/backlog-review-findings` | the ten findings files, `story.md`, `fix-plan.md`, `proposals.md`, `phase-map.md`, `decision-pack-DEC-1.md`, and the one docs fix at the end of this file. Nothing under `docs/planning` changes | the user reads and merges |
| — | — | the user decides the proposals (`proposals.md`), at least P-1, P-2, and P-6, and whether the 023 C-5 client guide joins phase A before the AI Act Art. 50(2) date, 2026-12-02 | — |
| PR-2 | `docs/planning-tools-retire` | the tool changes in the plan, plus section 1's "Tool changes"; the existing ticked lines in 002, 008, 009, 010, 012, 013, and 054 in the evidence form (002's is in section 3) | `make planning-sync planning-check`, `make check`, `make harness-lint` |
| PR-3 to PR-9 | `docs/backlog-ticks-<group>`, one worktree each | section 3, one PR per group. Order inside the batch does not matter; moved boxes land on both sides before PR-R | `make planning-sync planning-check` (sync prints `no changes` or only `updated NNN-...`), `make harness-lint` |
| PR-R | `docs/planning-retire`, alone, after PR-3 to PR-9 | section 1: `wave='WD'` for 011, `wave='WP'` for the 23 parked rows, the resizes, deps, titles, notes; the six new rows and their bodies (section 2); `--strict` on | `make planning-sync planning-check`, `make check`, `make harness-lint` |
| PR-P | `docs/planning-plan-prose` | template prose; `poc/000-plan.md` "After the MVP"; `001 DEC-1` body from the decision pack; the waves table points at `phase-map.md`; the critical-path paragraph uses the new anchor | same as PR-2 |
| Close | — | plan file `Status: done`; `poc/011-what-is-left.md` section 5 ticked; `verification.md` | — |

Changes from the plan:

- PR-R also writes six new bodies, because `check.py` fails on a skeleton.
- PR-2 changes the critical-path anchor: 043 A-3 is parked.
- 011 H-2 retires only if P-6 is decided as suggested. If not, PR-R leaves it in W2.
- A proposal's pick that changes code is its own PR, owned by the agent named in `proposals.md`, after PR-R. No planning PR changes code.
- Never run `make lint-fix` in a planning PR: `codespell -w docs` can rewrite the epic.

## No change

- **W2b-07**: no change: 012 H-3 stays one live issue (re-scoped, not split), so 017 H-4 keeps its H-3 dependency.
- **W2b-29**: no change: `sync.py` regenerates the `blocks` lists from the new `ROWS` deps.
- **W3-10**: no change: 038 X-1a stays size L and phase `later`; C2 records the phase.
- **W3-19**: no change: the `suggested:` markers stay in the parked bodies; none goes to the DEC-1 pack.
- **W4-11**: no change: every edge it lists is cleared by the ROWS changes; the snippet in section 4 shows no live issue on a WP row.
- **W4-15**: no change: the WP wave already shows 044 and 045 as parked in the index; titles stay.
- **W6-20**: no change: 072 C-3 stays size L and phase `later`; no split.
- **W6-23**: no change: no `ROWS` change; every dependent is `later` or parked.
- **W6-25**: no change: the markers in 064 to 075 stay; none goes to the DEC-1 pack.
- **W7-13**: no change: C2 records W7 as `later` in `phase-map.md`; the governance exception is handled in 059 H-15 and 077 C-1.
- **W89-25**: no change: the markers in 094 to 115 stay; the three that need an ADR or an owner are in the 099, 095, and 110 edits.
- **W1012-13**: no change: the cross-file list is cleared by the ROWS changes on 123, 126, 120, and the 116 body edit.

## Docs fix outside docs/planning

- **W2b-28**: `pocs/poc-09-agent-mvp-template/notes/2026-11-05-kind-hpa-drill.md` line 38 says kind's default CNI does not enforce NetworkPolicy and that PoC-5 runs Calico. `deploy/kind/poc05/cluster.yaml` sets no `disableDefaultCNI` and names no Calico, so PoC-5 runs kind's default CNI, kindnet, which ADR-005 decision 7 says enforces NetworkPolicy. Rewrite the line to that. It rides in PR-1 (no file under `docs/planning` changes).
