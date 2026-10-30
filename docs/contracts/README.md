# Contracts

This folder holds the written chassis contract, one file per version. It exists because each PoC's exit criteria ask for the contract in writing, checked against the code. To find a rule, start at the current version and walk back: each version names only what changed, and an earlier file still holds for what later files do not name.

A version here is a version of the document, not of the wire. The event schema stays `schema_version: "0"`. Where a file and the code differ, the code wins.

| File | Introduced by | Written | Status |
| ---- | ------------- | ------- | ------ |
| [contract-v0.md](contract-v0.md) | PoC-1 | 2026-10-01 | Superseded by v1 |
| [contract-v1.md](contract-v1.md) | PoC-2 | 2026-10-01 | Superseded by v2 (additive; v1 holds where later files are silent) |
| [contract-v2.md](contract-v2.md) | PoC-3 | 2026-10-01 | Superseded by v3 (additive) |
| [contract-v3.md](contract-v3.md) | PoC-4 | 2026-10-01 | Superseded by v4 (additive) |
| [contract-v4.md](contract-v4.md) | PoC-5 | 2026-10-02 | Superseded by v5 |
| [contract-v5.md](contract-v5.md) | PoC-6 | 2026-10-09 | Last non-draft version (in force after PoC-6) |
| [contract-v6.md](contract-v6.md) | PoC-7 | 2026-10-28 | Draft, additive over v5 |
| [contract-v7.md](contract-v7.md) | PoC-8 | 2026-10-29 | Draft, additive over v6; the newest version |

The status comes from each file's header, or, where a file has no banner, from the next file's header.
