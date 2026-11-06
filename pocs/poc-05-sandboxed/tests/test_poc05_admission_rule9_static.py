"""PoC-5 admission, offline: trust rule 9, the minimum chassis version (ADR-001 item 9; backlog
056 CH-7; the product plan, phase A), on the Python model of the rules
(`poc_harness.admission_model`). The policy itself is `deploy/kind/poc05/admission/policy.yaml`,
checked against its pins in `test_poc05_admission_static.py` and run on kind by
`test_poc05_kind_admission.py` with the `rule9-chassis-version` fixtures.

Exit criterion 7 (offline part): "Admission rejects `untrusted` and a third-party image in
`sidecar`." Rule 9 reads the chassis image the way the node resolves it: with `imageSource:
pull-never` (kind) the tag must be in `supportedChassisTags` and no digest may sit beside it; with
`imageSource: digest` (a cloud) the digest must be in `supportedChassisDigests`, because the
runtime ignores a tag beside a digest (security review, 2026-11-06).
"""

from __future__ import annotations

import copy
from typing import Any

import pytest
from poc_harness.admission_manifests import ADMISSION, FIXTURES, PREFIX, one_of_kind, read_fixture
from poc_harness.admission_model import AdmissionParams, broken_rules
from poc_harness.manifests import containers_of

Doc = dict[str, Any]


@pytest.fixture(scope="module")
def params() -> Doc:
    return one_of_kind(ADMISSION / "params.yaml", "ConfigMap")


DIGEST_OK = "sha256:" + "a" * 64
DIGEST_OTHER = "sha256:" + "0" * 64


def _chassis_twin(chassis_image: str, pull: str) -> Doc:
    """Rule 9's admitted twin with its chassis image replaced, in the pull-never or digest shape."""
    twin = read_fixture(FIXTURES / "rule9-chassis-version" / "admitted.yaml").doc
    for c in containers_of(twin["spec"]):
        c["imagePullPolicy"] = pull
        if pull != "Never" and "@" not in c["image"]:
            c["image"] += "@" + DIGEST_OTHER
    (chassis,) = [c for c in containers_of(twin["spec"]) if c["name"] == "chassis"]
    chassis["image"] = chassis_image
    return twin


@pytest.mark.parametrize(
    ("chassis_image", "broken"),
    [
        (PREFIX + "chassis:poc05", set()),
        (PREFIX + "chassis:poc04", {"9"}),
        (PREFIX + "chassis:poc05@" + DIGEST_OK, {"9"}),
        (PREFIX + "chassis@" + DIGEST_OK, {"9"}),
        (PREFIX + "chassis", {"9"}),
    ],
    ids=["tag", "old tag", "tag and digest", "digest only", "no tag"],
)
def test_rule_9_with_pull_never_reads_the_chassis_tag_and_takes_no_digest(
    params: Doc, chassis_image: str, broken: set[str]
) -> None:
    """Criterion 7: "The admission check rejects an `untrusted` workload, and a third-party image,
    in the `sidecar` lane."

    Rule 9 (the minimum chassis version, ADR-001 item 9) with `imageSource: pull-never`, the
    kind mode: the store is loaded by tag, so the chassis tag must be one of
    `supportedChassisTags` and no digest may sit beside it (the rule could not check it)."""
    twin = _chassis_twin(chassis_image, "Never")

    assert broken_rules(twin, AdmissionParams.of(params)) == broken


@pytest.mark.parametrize(
    ("chassis_image", "broken"),
    [
        (PREFIX + "chassis:poc05@" + DIGEST_OK, set()),
        (PREFIX + "chassis@" + DIGEST_OK, set()),
        (PREFIX + "chassis:poc05@" + DIGEST_OTHER, {"9"}),
        (PREFIX + "chassis:poc05", {"8", "9"}),
    ],
    ids=[
        "listed digest with its tag",
        "listed digest alone",
        "other digest, right tag",
        "no digest",
    ],
)
def test_rule_9_with_digest_source_reads_the_chassis_digest_not_the_tag(
    params: Doc, chassis_image: str, broken: set[str]
) -> None:
    """Criterion 7: "The admission check rejects an `untrusted` workload, and a third-party image,
    in the `sidecar` lane."

    Rule 9 with `imageSource: digest`, the cloud mode: the runtime pulls the digest and ignores a
    tag beside it (security review, 2026-11-06), so the chassis digest must be one of
    `supportedChassisDigests`; a right tag beside a wrong digest is refused."""
    doc = copy.deepcopy(params)
    doc["data"].update(imageSource="digest", supportedChassisDigests=DIGEST_OK)
    twin = _chassis_twin(chassis_image, "IfNotPresent")

    assert broken_rules(twin, AdmissionParams.of(doc)) == broken


def test_rule_9_fails_closed_while_the_list_for_the_mode_is_empty(params: Doc) -> None:
    """With `imageSource: digest` and no `supportedChassisDigests` (the kind params as shipped),
    every chassis pod is refused; a pod with no chassis image is not rule 9's."""
    doc = copy.deepcopy(params)
    doc["data"]["imageSource"] = "digest"
    chassis_pod = _chassis_twin(PREFIX + "chassis:poc05@" + DIGEST_OK, "IfNotPresent")
    remote = read_fixture(FIXTURES / "rule5-remote-shape" / "admitted.yaml").doc

    assert "9" in broken_rules(chassis_pod, AdmissionParams.of(doc))
    assert "9" not in broken_rules(remote, AdmissionParams.of(doc))
