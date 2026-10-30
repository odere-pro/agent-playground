# pocs

Iteration folders. Read the folder's own `CLAUDE.md` and README before working in one.

- Skill `poc-iteration` owns how a PoC opens, runs, and closes (`.claude/skills/poc-iteration/SKILL.md`). That includes the evidence a checkbox needs.
- Scenario tests live in `pocs/poc-NN-*/tests/`. Their shape, docstring included, is in `docs/guides/testing.md` ("Test shape"). A criterion without a test is not done.
- Code that outlives the iteration goes to `packages/`, never into the PoC folder. The PoC folder holds tests, demo scripts, and notes.
- Measurements are dated files in `notes/`, with the command that produced them, so they can be re-run.

## `CURRENT`

This section is the one definition of `pocs/CURRENT`.

- `pocs/CURRENT` holds one folder name: the default PoC for a new session. The session-start hook prints it.
- More than one PoC can be open. The open PoCs are the ones whose README `Status:` is `in progress`.
- Opening or closing a PoC does not require a change to `CURRENT`.
