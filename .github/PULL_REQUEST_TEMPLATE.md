## What

One or two sentences. Which PoC (`pocs/poc-NN-*`) and which exit criteria this moves.

## Evidence

```
make check
<paste the tail of the output>
```

## Planning

- [ ] No planning doc changed, or: `docs/planning` issue or PoC doc updated and `make planning-check` passes
- [ ] If this work came from a plan, it is in `docs/plans/` with its `Status:` line updated

## Checklist

- [ ] Tests run offline (`make test`), no key in any file
- [ ] New dependency sits behind a port with a fake, and the fake passes the same contract suite
- [ ] Folder `CLAUDE.md` and README still true after this change
