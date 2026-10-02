# hostile

## Status

Not built in PoC-5. The user decided this on 2026-10-02: task T10 is dropped.

No probe workload runs in the cluster. The controls are checked from outside the pod instead:

- `kubectl exec` of standard tools that are already in our images.
- The pod specs.
- The cgroup files.

Each check is written as "the control refuses X", with a paired allowed control in the same test. The offline part is `pocs/poc-05-sandboxed/tests/test_poc05_hostile_offline.py`.

Exit criterion 8 is "partly shown".

## What it was

The probe workload for the PoC-5 hostile suites: one fixed check per threat-model id, standard library only, on the `workload-a2a` template.

The package is a skeleton (PoC-5 task T01). The design is in `docs/plans/2026-10-02-poc-05-sandboxed.md`, section 5.
