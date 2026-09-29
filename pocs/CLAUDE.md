# pocs

Iteration folders. Read the folder's own `CLAUDE.md` and README before working in one. Skill `poc-iteration` has the procedure.

- A checkbox in a PoC README changes only with evidence: the test name or the command and its output.
- Scenario tests live in `pocs/poc-NN-*/tests/` and name the exit criterion in their docstring. A criterion without a test is not done.
- Code that outlives the iteration goes to `packages/`, never into the PoC folder. The PoC folder holds tests, demo scripts, and notes.
- Measurements are dated files in `notes/`, with the command that produced them, so they can be re-run.
- `pocs/CURRENT` names the iteration in progress. The session-start hook prints it.
