# PoC-NN: <title>

Status: <not started | in progress | done> (YYYY-MM-DD)
Planning doc: [docs/planning/poc/00N-*.md](../../docs/planning/poc/)
Builds on: <the PoC folders this one needs, or none>
Hands to: <the PoC folders that build on this one, or none>
Time box: <from the planning doc>

<!-- `Status:` holds one value and the date it was set. Detail goes under "Waiting on". -->

## Waiting on

<what blocks the next box, one bullet each, or none>

## Question

<one or two sentences, from the planning doc>

## Scope

- [ ] <item from the planning doc>

## Exit criteria

Each one has a scenario test in `tests/` or a recorded reason it cannot have one yet.

- [ ] <criterion> · evidence: `tests/test_<name>.py::test_<case>`

## How to run

```bash
make test-poc POC=NN
```

## Demo

<the script in `demo/` and what it shows>

## Notes and decisions

Dated entries, or files in `notes/`.
