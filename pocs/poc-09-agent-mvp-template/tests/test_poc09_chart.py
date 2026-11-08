"""PoC-9 exit criterion 5: "The library chart renders a pod with both containers, secrets in the
chassis container only, and the Service on the chassis port."
(docs/planning/poc/009-PoC-9-agent-mvp-template.md; backlog 024 CH-3)

Renders `deploy/helm/example-agent`, which includes the library chart `deploy/helm/agent-lib`,
with `helm template` (`chart_render`, the `chart` fixture in `conftest.py`), parses the YAML, and
checks:

- Default values (the sidecar lane): one pod with exactly two containers, `chassis` (the native
  sidecar, an init container with `restartPolicy: Always`, as PoC-5) and `workload`. Every Secret
  reference (`secretKeyRef`, `secretRef`, a `secret` volume) is on the chassis; the workload has
  none, and no service account token. The Service's `targetPort` is the chassis port, never the
  workload port. Both containers run non-root with the uid table's uids, a read-only root, all
  capabilities dropped, no privilege escalation, and seccomp `RuntimeDefault`. The paired control:
  the example values do give the chassis a Secret, so "none on the workload" is not vacuous.
- `connector: remote` with `trust: untrusted` (ADR-005): the remote lane is two pods, as on kind
  in PoC-5. The chassis pod holds every internal credential; the workload pod runs under the
  gVisor RuntimeClass with one Secret, its own bearer token (ADR-005 decision 1), named after it
  (admission rule 7c), and may reach only its chassis's remote proxy port.
- Values that try to give the workload a Secret (or break the trust rule) fail the render with
  the chart's own `agent-lib:` message.

The admission and egress rules the chart carries are in `test_poc09_chart_boundary.py`.
`helm lint` runs on both charts. Skipped, with the reason, when `helm` is not on PATH;
`.github/workflows/poc09-helm.yml` installs a pinned helm and runs this file.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml
from chart_render import (
    CHASSIS_PORT,
    CHASSIS_UID,
    HELM_DIR,
    NEEDS_HELM,
    RELEASE,
    REMOTE_NAMESPACE,
    REMOTE_PROXY_PORT,
    REMOTE_VALUES,
    WORKLOAD_PORT,
    WORKLOAD_UID,
    Charts,
    Doc,
    containers,
    one,
    secret_names,
    values_file,
    walk,
)

pytestmark = NEEDS_HELM


def _assert_hardened(container: Doc, uid: int) -> None:
    sc = container["securityContext"]
    assert sc["runAsNonRoot"] is True
    assert sc["runAsUser"] == uid and sc["runAsGroup"] == uid
    assert sc["readOnlyRootFilesystem"] is True
    assert sc["allowPrivilegeEscalation"] is False
    assert sc["capabilities"] == {"drop": ["ALL"]}
    assert sc["seccompProfile"] == {"type": "RuntimeDefault"}
    assert "command" not in container, "the chassis image's ENTRYPOINT runs; admission rule 6b"
    assert container["resources"]["limits"] and container["resources"]["requests"]


def _assert_pod_closed(pod: Doc) -> None:
    assert pod["automountServiceAccountToken"] is False
    assert pod["enableServiceLinks"] is False
    assert pod["securityContext"]["runAsNonRoot"] is True
    assert pod["securityContext"]["seccompProfile"] == {"type": "RuntimeDefault"}
    assert "hostNetwork" not in pod and "hostPID" not in pod


# --- lint -------------------------------------------------------------------------------------


def test_helm_lint_passes_on_the_library_and_the_example(chart: Charts) -> None:
    lib = chart.helm("lint", str(chart.example.parent / "agent-lib"))
    app = chart.helm("lint", str(chart.example))
    assert "0 chart(s) failed" in lib.stdout + lib.stderr
    assert "0 chart(s) failed" in app.stdout + app.stderr


# --- the sidecar lane (default values) --------------------------------------------------------


def test_sidecar_lane_renders_one_pod_with_the_chassis_and_the_workload(chart: Charts) -> None:
    docs = chart.template()
    pod = one(docs, "Deployment", RELEASE)["spec"]["template"]["spec"]
    by_name = containers(pod)
    assert sorted(by_name) == ["chassis", "workload"]
    # The chassis is the native sidecar (PoC-5 bind order, contract v4 H14).
    assert [c["name"] for c in pod["initContainers"]] == ["chassis"]
    assert pod["initContainers"][0]["restartPolicy"] == "Always"
    assert [c["name"] for c in pod["containers"]] == ["workload"]
    assert "runtimeClassName" not in pod
    image = by_name["chassis"]["image"]
    assert image.startswith("kind.local/agent-platform/chassis:") and not image.endswith(":latest")
    assert not [d for d in docs if d["kind"] == "HorizontalPodAutoscaler"], "HPA is off by default"


def test_sidecar_lane_secrets_are_on_the_chassis_container_only(chart: Charts) -> None:
    docs = chart.template()
    pod = one(docs, "Deployment", RELEASE)["spec"]["template"]["spec"]
    by_name = containers(pod)
    # Paired control: the example values give the chassis secrets, so the check below has teeth.
    assert secret_names(by_name["chassis"], pod) >= {"example-agent-litellm"}
    assert secret_names(by_name["workload"], pod) == set()
    assert not [k for k, _ in walk(by_name["workload"]) if k in {"valueFrom", "envFrom"}]
    assert {e["name"] for e in by_name["workload"]["env"]} >= {"HOST", "PORT"}
    sa = one(docs, "ServiceAccount", RELEASE)
    assert sa["automountServiceAccountToken"] is False
    _assert_pod_closed(pod)


def test_sidecar_lane_service_targets_the_chassis_port_only(chart: Charts) -> None:
    docs = chart.template()
    pod = one(docs, "Deployment", RELEASE)["spec"]["template"]["spec"]
    chassis = containers(pod)["chassis"]
    assert [p["containerPort"] for p in chassis["ports"]] == [CHASSIS_PORT]
    assert "ports" not in containers(pod)["workload"], "the workload listens on 127.0.0.1"
    services = [d for d in docs if d["kind"] == "Service"]
    assert [s["metadata"]["name"] for s in services] == [RELEASE]
    ports = services[0]["spec"]["ports"]
    assert [p["targetPort"] for p in ports] == [CHASSIS_PORT]
    assert WORKLOAD_PORT not in {p["port"] for p in ports}
    workload_env = {e["name"]: e["value"] for e in containers(pod)["workload"]["env"]}
    assert workload_env["HOST"] == "127.0.0.1"
    assert workload_env["PORT"] == str(WORKLOAD_PORT)
    assert "$(POD_IP)" in chassis["args"], "the public port binds the pod IP (H13)"


def test_sidecar_lane_containers_are_hardened(chart: Charts) -> None:
    pod = one(chart.template(), "Deployment", RELEASE)["spec"]["template"]["spec"]
    by_name = containers(pod)
    _assert_hardened(by_name["chassis"], CHASSIS_UID)
    _assert_hardened(by_name["workload"], WORKLOAD_UID)
    # The workload's liveness restarts the pod on a hang without waiting for the pod's grace.
    assert by_name["workload"]["livenessProbe"]["terminationGracePeriodSeconds"] == 10


def test_sidecar_lane_network_policy_admits_the_chassis_port_only(chart: Charts) -> None:
    docs = chart.template()
    policy = one(docs, "NetworkPolicy", RELEASE)
    assert policy["spec"]["policyTypes"] == ["Ingress", "Egress"]
    ingress_ports = {p["port"] for rule in policy["spec"]["ingress"] for p in rule["ports"]}
    assert ingress_ports == {CHASSIS_PORT}
    assert not [k for k, _ in walk(policy) if k == "ipBlock"]
    config = yaml.safe_load(one(docs, "ConfigMap", f"{RELEASE}-chassis")["data"]["config.yaml"])
    assert config["spec"]["engine"] == {
        "connector": "sidecar",
        "url": f"http://127.0.0.1:{WORKLOAD_PORT}",
    }
    assert config["spec"]["trust"] == "trusted"


def test_hpa_renders_when_enabled(chart: Charts, tmp_path: Path) -> None:
    values = values_file(tmp_path, {"hpa": {"enabled": True, "minReplicas": 2, "maxReplicas": 4}})
    hpa = one(chart.template(values), "HorizontalPodAutoscaler", RELEASE)
    assert hpa["spec"]["scaleTargetRef"] == {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "name": RELEASE,
    }
    assert (hpa["spec"]["minReplicas"], hpa["spec"]["maxReplicas"]) == (2, 4)


# --- the remote lane --------------------------------------------------------------------------


def test_remote_lane_renders_the_chassis_pod_and_a_gvisor_workload_pod(
    chart: Charts, tmp_path: Path
) -> None:
    docs = chart.template(values_file(tmp_path, REMOTE_VALUES))
    chassis_pod = one(docs, "Deployment", RELEASE)["spec"]["template"]["spec"]
    workload_dep = one(docs, "Deployment", f"{RELEASE}-workload")
    workload_pod = workload_dep["spec"]["template"]["spec"]
    assert sorted(containers(chassis_pod)) == ["chassis"]
    assert sorted(containers(workload_pod)) == ["workload"]
    assert "runtimeClassName" not in chassis_pod, "the chassis stays outside the sandbox"
    assert workload_pod["runtimeClassName"] == "gvisor"
    assert workload_dep["metadata"]["namespace"] == REMOTE_NAMESPACE
    assert workload_dep["spec"]["replicas"] == 1
    labels = workload_dep["spec"]["template"]["metadata"]["labels"]
    assert labels["agents.platform/lane"] == "remote"
    assert labels["agents.platform/trust"] == "untrusted"
    for pod in (chassis_pod, workload_pod):
        _assert_pod_closed(pod)
    _assert_hardened(containers(chassis_pod)["chassis"], CHASSIS_UID)
    _assert_hardened(containers(workload_pod)["workload"], WORKLOAD_UID)


def test_remote_lane_the_workload_holds_only_its_own_token(chart: Charts, tmp_path: Path) -> None:
    docs = chart.template(values_file(tmp_path, REMOTE_VALUES))
    chassis_pod = one(docs, "Deployment", RELEASE)["spec"]["template"]["spec"]
    workload_pod = one(docs, "Deployment", f"{RELEASE}-workload")["spec"]["template"]["spec"]
    chassis = containers(chassis_pod)["chassis"]
    workload = containers(workload_pod)["workload"]
    token = f"{RELEASE}-workload-token"
    assert secret_names(chassis, chassis_pod) >= {"example-agent-litellm", token}
    assert secret_names(workload, workload_pod) == {token}
    by_name = {e["name"]: e for e in workload["env"]}
    assert by_name["CHASSIS_API_TOKEN"]["valueFrom"]["secretKeyRef"]["name"] == token
    secret_env = [e["name"] for e in workload["env"] if "secretKeyRef" in e.get("valueFrom", {})]
    assert secret_env == ["CHASSIS_API_TOKEN"]
    assert "--remote-proxy-host" in chassis["args"]
    config = yaml.safe_load(one(docs, "ConfigMap", f"{RELEASE}-chassis")["data"]["config.yaml"])
    assert config["spec"]["engine"]["url"] == (
        f"http://{RELEASE}-workload.{REMOTE_NAMESPACE}.svc.cluster.local:{WORKLOAD_PORT}"
    )
    assert config["spec"]["engine"]["auth"] == {"scheme": "bearer", "token_env": "REMOTE_TOKEN"}


def test_remote_lane_services_and_policies(chart: Charts, tmp_path: Path) -> None:
    docs = chart.template(values_file(tmp_path, REMOTE_VALUES))
    public = one(docs, "Service", RELEASE)
    assert [p["targetPort"] for p in public["spec"]["ports"]] == [CHASSIS_PORT]
    proxy = one(docs, "Service", f"{RELEASE}-remote-proxy")
    assert proxy["spec"]["clusterIP"] == "10.96.0.92"
    assert [p["targetPort"] for p in proxy["spec"]["ports"]] == [REMOTE_PROXY_PORT]
    policy = one(docs, "NetworkPolicy", f"{RELEASE}-workload")
    assert policy["metadata"]["namespace"] == REMOTE_NAMESPACE
    egress = policy["spec"]["egress"]
    assert len(egress) == 1, "no DNS, no other peer: only the chassis's remote proxy"
    assert [p["port"] for p in egress[0]["ports"]] == [REMOTE_PROXY_PORT]
    ingress = policy["spec"]["ingress"]
    assert len(ingress) == 1 and [p["port"] for p in ingress[0]["ports"]] == [WORKLOAD_PORT]
    for doc in docs:
        assert not [k for k, _ in walk(doc) if k == "ipBlock"], doc["metadata"]["name"]


# --- refusals ---------------------------------------------------------------------------------

REFUSED: dict[str, tuple[dict[str, Any], str]] = {
    "chassis arg moves the listener": (
        {"chassis": {"extraArgs": ["--host", "0.0.0.0"]}},
        "is not allowed",
    ),
    "chassis arg abbreviated": ({"chassis": {"extraArgs": ["--ho=0.0.0.0"]}}, "is not allowed"),
    "workload arg disables remote auth": (
        {"workload": {"args": ["--require-token-env", ""]}},
        "is not allowed",
    ),
    "workload secretEnv": (
        {"workload": {"secretEnv": [{"name": "X", "secretName": "s", "key": "k"}]}},
        "workload.secretEnv",
    ),
    "workload envFrom": (
        {"workload": {"envFrom": [{"secretRef": {"name": "s"}}]}},
        "workload.envFrom",
    ),
    "workload secret volume": (
        {"workload": {"volumes": [{"name": "v", "secret": {"secretName": "s"}}]}},
        "workload.volumes",
    ),
    "workload env valueFrom": (
        {
            "workload": {
                "env": [{"name": "X", "valueFrom": {"secretKeyRef": {"name": "s", "key": "k"}}}]
            }
        },
        "valueFrom",
    ),
    "workload env that looks like a credential": (
        {"workload": {"env": [{"name": "OPENAI_API_KEY", "value": "x"}]}},
        "OPENAI_API_KEY",
    ),
    "workload env the chart owns": (
        {"workload": {"env": [{"name": "CHASSIS_MODEL_URL", "value": "http://evil"}]}},
        "CHASSIS_MODEL_URL",
    ),
    "chassis env valueFrom outside secretEnv": (
        {
            "chassis": {
                "env": [{"name": "X", "valueFrom": {"secretKeyRef": {"name": "s", "key": "k"}}}]
            }
        },
        "chassis.secretEnv",
    ),
    "chassis command": ({"chassis": {"command": ["sh"]}}, "chassis.command"),
    "untrusted in the sidecar lane": (
        {"chassis": {"config": {"spec": {"trust": "untrusted"}}}},
        "untrusted",
    ),
    "chassis tag latest": ({"chassis": {"image": {"tag": "latest"}}}, "latest"),
    "egress ipBlock": (
        {"networkPolicy": {"chassisEgress": [{"to": [{"ipBlock": {"cidr": "0.0.0.0/0"}}]}]}},
        "ipBlock",
    ),
    "remote lane with two replicas": (REMOTE_VALUES | {"replicas": 2}, "one replica"),
    "remote lane without a proxy ClusterIP": (
        REMOTE_VALUES | {"remote": {"namespace": REMOTE_NAMESPACE}},
        "remote.proxyClusterIP",
    ),
    "remote token Secret named for another remote": (
        REMOTE_VALUES
        | {
            "remote": {
                "namespace": REMOTE_NAMESPACE,
                "proxyClusterIP": "10.96.0.92",
                "tokenSecret": {"name": "other-agent-workload-token"},
            }
        },
        "remote.tokenSecret.name",
    ),
}


@pytest.mark.parametrize("case", sorted(REFUSED))
def test_the_render_fails_on_values_that_break_the_boundary(
    chart: Charts, tmp_path: Path, case: str
) -> None:
    values, needle = REFUSED[case]
    path = values_file(tmp_path, values)
    proc = chart.helm("template", RELEASE, str(chart.example), "-f", str(path), check=False)
    assert proc.returncode != 0, f"{case}: rendered, want a refusal:\n{proc.stdout}"
    assert "agent-lib:" in proc.stderr and needle in proc.stderr, proc.stderr


def test_the_kind_values_pull_nothing_and_hold_no_secret(chart: Charts) -> None:
    """`values-kind.yaml`, the kind drill's values: PoC-5 admission rule 8, and no key at all."""
    docs = chart.template(HELM_DIR / "example-agent/values-kind.yaml")
    pod = one(docs, "Deployment", RELEASE)["spec"]["template"]["spec"]
    every = [*pod.get("initContainers", []), *pod["containers"]]
    assert {c["imagePullPolicy"] for c in every} == {"Never"}
    assert "secretKeyRef" not in yaml.safe_dump(docs)
    assert one(docs, "HorizontalPodAutoscaler", RELEASE)["spec"]["minReplicas"] == 2
