# plans

Approved Claude Code plans and reviews, under version control, so a decision survives the session that made it.

Rules:

- File name `YYYY-MM-DD-<slug>.md`.
- The first lines carry `Status: approved | in progress | done | superseded`. `make harness-lint` checks it.
- A plan is copied here in the first commit of the work it describes, and its status is updated when the work lands.
- A review that changed planning docs is recorded here too, with what it changed.
- The issues themselves are files in `docs/planning/issues/`, versioned by the same commits.
