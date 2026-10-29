# Rubric

Every review session checks its area against this list. A finding cites the rule it breaks.

## Simple

- [ ] Functions are under 50 lines.
- [ ] Files are under 800 lines.
- [ ] Nesting is at most 4 levels.
- [ ] No dead code and no unused option.
- [ ] No second helper that does what one in `packages/` already does.
- [ ] No abstraction with one caller and no planned second.

## Secure

Lens agents: `platform-security` for `deploy/`, CI, images, and egress. `security-reviewer` for server input handling.

- [ ] ADR-001 hard requirement 1: credentials live only in the chassis.
- [ ] Egress is default-deny. The workload holds no service account token.
- [ ] ADR-001 hard requirement 2: every lane is tested on every commit.
- [ ] Each inbound adapter validates its input.
- [ ] Errors do not leak internals.
- [ ] Images and actions are pinned by hash.
- [ ] No secret in any file, prompt, log, or cassette.

## Maintainable

- [ ] Code that outlives a PoC is in `packages/`.
- [ ] No `sys.path` insert in `pocs/*/tests`.
- [ ] Each port has a fake, and both pass one contract suite.
- [ ] The import rules hold (`make lint`).
- [ ] Names use domain words.
- [ ] No PoC number in a package name or a pytest marker.
- [ ] Config duplicated across `deploy/` is listed, not silently copied.

## Docs in simple English

- [ ] The first three sentences of a README say what it is, why it exists, and how to run it.
- [ ] Sentences are under about 20 words (suggested:).
- [ ] One idea per sentence. US spelling. No marketing words.
- [ ] Every PoC README has a `Builds on:` line and a `Hands to:` line.
- [ ] A doc says a thing once. Other docs link to it.

## Tests read like specs

- [ ] The name is `test_<subject>_<does what>_when_<condition>` and reads as a sentence. `when_` is optional.
- [ ] The docstring is one line in plain words.
- [ ] A PoC scenario test names its exit criterion word for word (`pocs/CLAUDE.md`).
- [ ] The body is arrange, act, assert, split by blank lines.
- [ ] One behavior per test. `parametrize` instead of loops.
- [ ] No `if` or `for` around asserts.
- [ ] Helpers live in a harness and have domain names.
- [ ] A renamed test updates every README line that cites it as evidence.

## Out of scope for a fix

- A change to the wire contract or the event schema.
- A change to what an exit criterion means.
- A ticked or unticked box in an open PoC (PoC-6, PoC-7).

Each of these goes to [proposals.md](proposals.md).
