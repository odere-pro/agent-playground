"""The template A2A server a Python workload ships with (ADR-002). Never imports the chassis.

`mapping` is a byte-for-byte copy of `chassis.adapters.a2a.mapping`; `server` is the workload-side
copy of the chassis's template server, validating with `jsonschema` against the vendored
`schemas/events.v0.json`; `cli` is the `workload-a2a serve` command.
"""
