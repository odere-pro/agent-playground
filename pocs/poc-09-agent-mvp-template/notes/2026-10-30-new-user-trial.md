# Trial by someone new to the code (criterion 6)

Not run yet. It needs a person who did not build the chassis. Time them from the first command to the last step. The limit is one day.

1. Read `docs/guides/how-to-add-business-logic.md`.
2. `agentctl new <name> --kind transformer --dest <folder>`, then `cd` into it and run `make setup test`.
3. Change `handle` to do something of their own, and add a case to `evals/cases.yaml`.
4. Run `make lint test load-smoke hostile redteam eval`.
5. Build the chassis image (`deploy/compose/README.md`) and start the stack with the `fake` profile.
6. Call it from the OpenAI SDK and from an MCP client, and attach a debugger.
7. Break the logic and watch `make eval` fail.

Record here: who, the date, the total time, and every place they got stuck.
