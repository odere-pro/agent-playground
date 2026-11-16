# ADR-005 amendment: image signatures at admission, three options, 2026-11-16

Writer: `platform-security`. Read against `main` at `9fc0f69`. A note for the user's decision, not a decision. Claims the repo cannot show are marked "not verified online"; versions stay `suggested:`.

## Question

Should the cluster check image signatures at admission, and with what? Today nothing does (132 CH-13 review, HIGH 1, ASI04). ADR-005 decision 4 says "no Kyverno"; its Revisit reopens decisions 4 and 5 when signatures land. The 056 CH-7 signature box and 080 R-7 wait on this.

Facts on `main`:

- No chassis image is signed. `scripts/publish_chassis.sh` lands with 139 CH-19. Only the template CI signs, keyless by digest, then runs `cosign verify` with a pinned identity and issuer (`ci.yml.jinja`, `sign` job).
- On kind, `imageSource: pull-never`: images sit in the node under `kind.local/`, with no registry. A signature lives in a registry, so any verifier needs one.
- On a cloud, `imageSource: digest`: rule 9 admits only the chassis digests in the platform-owned `supportedChassisDigests`.
- A ValidatingAdmissionPolicy cannot fetch a signature.

## Options

- **A.** Kyverno, `verifyImages` only. The trust rules stay ValidatingAdmissionPolicies.
- **B.** sigstore `policy-controller` (`ClusterImagePolicy`), same split.
- **C.** Baseline: no admission check. Verify in CI and the ring gate only.

## Comparison table

| Criterion | A: Kyverno | B: policy-controller | C: CI and ring gate |
| --- | --- | --- | --- |
| Ask 1: match from `registryPrefix` + `chassis` | `imageReferences` glob rendered from the param; a miss admits the image | `images[].glob`, same; a "no match" mode may deny an uncovered image (not verified online) | The gate reads the same param |
| Ask 2: `failurePolicy: Fail` | Policy field, render-asserted (not verified online) | On its webhook, asserted (not verified online) | No webhook |
| Ask 3: `cloud` refuses `required: false` | Chart check in `_admission.tpl` | Same | CI refuses an unsigned digest in the list |
| Ask 4: egress NetworkPolicy | Registry, Rekor, TUF mirror, API server; no host names in NetworkPolicy, so `ipBlock`s beside B4's one exception | Same | None in cluster |
| Keyless, pinned identity and issuer | Keyless attestor, subject and issuer (not verified online) | Keyless identities, issuer and subject (not verified online) | `cosign verify`, as the template CI does |
| Verifier down | Every pod create in bound namespaces refused, restarts too; needs 2 replicas, its namespace off its webhook | Same | Nothing blocks |
| Rekor or TUF down | A bundled signature may verify from the cached root, else refused (not verified online) | Same | Releases stop; pods run |
| Extra parts | Several controllers, CRDs, a wide ClusterRole that can mutate; suggested: admission controller only | One webhook Deployment, CRDs, read-mostly RBAC | None |
| ADR-005 decision 4 | Breaks its text; matches "Why not" C2 and `010-reuse-analysis.md` | Keeps "no Kyverno", still adds a controller | Holds; decision 5's hand-kept list stays |
| ADR-001 hard requirement 1 | No internal credential; a pull credential while GHCR is private | Same | Held by CI |
| kind offline vs cloud | Local registry and key-pair test signature; keyless needs public Sigstore, so kind proves another authority | Same | Unchanged |
| 056 CH-7 box | Closes with a kind test | Same | Rewritten: not admission |
| 080 R-7 | Can check workload images per template identity | Same | `internal-signed` set by the registry only |

## What each option changes in this repo

A and B:

- `docs/planning/adr/005-remote-lane-auth-and-trust-admission.md`: the amendment.
- `deploy/helm/agent-admission/`, `deploy/helm/agent-lib/templates/_admission.tpl`: the policy, identity, issuer, `required`, the cloud refusal.
- `deploy/kind/poc05/admission/`, `run.sh`: a pinned install, a local registry, the egress NetworkPolicy. B adds a namespace opt-in label in `deploy/kind/poc05/base/namespaces.yaml` (not verified online).
- Tests: a render test on a non-kind prefix in `pocs/poc-09-agent-mvp-template/tests/`; a fixture pair in `deploy/kind/poc05/admission/fixtures/` (unsigned chassis refused, signed twin admitted), run by `test_poc05_kind_admission.py::test_rejected_fixture_is_refused_with_its_rule_message` and `::test_admitted_twin_is_admitted`.
- Docs: the 056 and 080 boxes, the ASI04 row in `docs/specs/security.md`, 025 H-10's stale Kyverno line.

C: a `cosign verify` step in `deploy/rings/README.md` and before a digest enters `supportedChassisDigests`; the 056 box rewritten. ASI04 stays HIGH.

## Risks

- C leaves ASI04 open. The deployer, or anyone with pod create in a bound namespace, can run an unsigned image under the prefix. On a cloud, rule 9 pins the chassis by digest; workload images are the gap. Rule 4 matches a repository, so an image pushed with a stolen registry credential is admitted.
- A and B fail closed per namespace. A verifier or Rekor outage stops scale-ups and restarts, not only releases.
- A missed glob fails open, the first draft's bug. Only a render test on a cloud-shaped prefix catches it.
- No PoC-5 hostile case covers signatures. The nearest is exit criterion 7 (third-party image) in `pocs/poc-05-sandboxed/README.md`.

## Platform-security's preference

B, if its claims hold when checked online. It only verifies signatures, cannot mutate, has narrower RBAC, and is one Deployment on a 7.75 GiB VM. A is acceptable, trimmed to admission only, if the user wants one engine for later mutation rules.

## What the user decides

1. A, B, or C, and the amendment text.
2. Chassis image only, or workload images too (080 R-7).
3. The pinned identity and issuer; GHCR packages public, or a read-only pull credential for the verifier.
4. The kind trust root: a key-pair test signature, or a networked keyless run.
5. Whether a verifier outage may block pod creates in bound namespaces.
6. The egress form: `ipBlock` exceptions, or a CNI with host-name policy.

## Not verified

- Kyverno and policy-controller field names, current versions, controller counts, RBAC scope, default webhook `failurePolicy`, and the "no match" deny mode. Check them online before the amendment.
- Whether a signature with a Rekor bundle verifies from a cached trusted root when Rekor or TUF is down, for either tool.
- Whether kindnet supports a host-name NetworkPolicy. The note assumes standard NetworkPolicy.
- The parked first draft is in no branch, so its `required: false` value and failing default come from `HANDOVER.md` and the 056 box.
- `scripts/publish_chassis.sh` is not on `main`; keyless-by-digest signing of the chassis is confirmed only from `HANDOVER.md`. The template CI pins cosign `v3.1.4`.
- No memory figures for either controller. Any figure would be `suggested:`.
