"""PoC-9 exit criterion 5: "The library chart renders a pod with both containers, secrets in the
chassis container only, and the Service on the chassis port."
(docs/planning/poc/009-PoC-9-agent-mvp-template.md; backlog 024 CH-3)

Renders `deploy/helm/example-agent`, which includes the library chart `deploy/helm/agent-lib`,
with `helm template` (`chart_render`, the `chart` fixture in `conftest.py`), parses the YAML, and
checks:

- Default values (the sidecar lane): one pod with exactly two containers, `workload` (the native
  sidecar, an init container with `restartPolicy: Always`) and `chassis` (the main container), so
  the kubelet stops the chassis first and it drains before the workload stops (ADR-012). Every
  Secret reference (`secretKeyRef`, `secretRef`, a `secret` volume) is on the chassis; the
  workload has none, and no service account token. The Service's `targetPort` is the chassis port,
  never the workload port. Both containers run non-root with the uid table's uids, a read-only
  root, all capabilities dropped, no privilege escalation, and seccomp `RuntimeDefault`. The
  paired control: the example values do give the chassis a Secret, so "none on the workload" is
  not vacuous.
- `connector: remote` with `trust: untrusted` (ADR-005): the remote lane is two pods, as on kind
  in PoC-5. The chassis pod holds every internal credential; the workload pod runs under the
  gVisor RuntimeClass with one Secret, its own bearer token (ADR-005 decision 1), named after it
  (admission rule 7c), and may reach only its chassis's remote proxy port.
- Values that try to give the workload a Secret (or break the trust rule) fail the render with
  the chart's own `agent-lib:` message. So does a pod grace shorter than the chassis's worst-case
  drain, `drainDelaySeconds + drainTimeoutSeconds + 5` (ADR-012 item 3); exactly the bound renders.

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
    # The workload is the native sidecar and the chassis the main container (ADR-012 item 1).
    assert [c["name"] for c in pod["initContainers"]] == ["workload"]
    assert pod["initContainers"][0]["restartPolicy"] == "Always"
    assert [c["name"] for c in pod["containers"]] == ["chassis"]
    assert "runtimeClassName" not in pod
    image = by_name["chassis"]["image"]
    assert image.startswith("kind.local/agent-platform/chassis:") and not image.endswith(":latest")
    assert not [d for d in docs if d["kind"] == "HorizontalPodAutoscaler"], "HPA is off by default"


def test_sidecar_lane_stop_order_puts_the_chassis_first(chart: Charts) -> None:
    """ADR-012 items 1-2: the kubelet stops main containers before restartable init containers,
    so the chassis (main) gets SIGTERM first and drains; the workload (native sidecar) stops after
    the chassis has exited. The workload's startupProbe gates the chassis's start."""
    pod = one(chart.template(), "Deployment", RELEASE)["spec"]["template"]["spec"]
    (workload,) = pod["initContainers"]
    (chassis,) = pod["containers"]
    assert (workload["name"], chassis["name"]) == ("workload", "chassis")
    assert workload["restartPolicy"] == "Always"
    assert "restartPolicy" not in chassis, "a main container takes the pod's restart policy"
    # The kubelet starts the chassis only after this probe passes.
    assert "exec" in workload["startupProbe"]
    assert "readinessProbe" not in workload, "the workload is never in the Service"
    # The chassis keeps HTTP probes for readiness and liveness; its startup is the marker's.
    assert chassis["readinessProbe"]["httpGet"]["path"] == "/ready"
    assert chassis["livenessProbe"]["httpGet"]["path"] == "/health"
    assert pod["terminationGracePeriodSeconds"] == 50


@pytest.mark.parametrize("values", [None, REMOTE_VALUES], ids=["sidecar", "remote"])
def test_the_chassis_starts_on_its_ready_marker_in_a_volume_only_it_mounts(
    chart: Charts, tmp_path: Path, values: dict[str, Any] | None
) -> None:
    """ADR-012, H14: the workload starts first and could answer on the chassis's public port
    while the chassis imports. The startup probe passes only on the marker `--ready-marker`
    writes once the chassis has bound, in `chassis-tmp`, which no other container mounts."""
    path = values_file(tmp_path, values) if values is not None else None
    docs = chart.template(path)
    pods = [d["spec"]["template"]["spec"] for d in docs if d["kind"] == "Deployment"]
    pod = one(docs, "Deployment", RELEASE)["spec"]["template"]["spec"]
    chassis = containers(pod)["chassis"]
    args = chassis["args"]
    marker = args[args.index("--ready-marker") + 1]
    mounts = {m["mountPath"]: m["name"] for m in chassis["volumeMounts"]}
    assert mounts[str(Path(marker).parent)] == "chassis-tmp", (marker, mounts)
    volume = next(v for v in pod["volumes"] if v["name"] == "chassis-tmp")
    assert volume["emptyDir"]["medium"] == "Memory"
    others = [c for p in pods for c in containers(p).values() if c["name"] != "chassis"]
    assert others, "the paired control: a workload container exists to check"
    for c in others:
        assert "chassis-tmp" not in {m["name"] for m in c.get("volumeMounts", [])}, c["name"]
    probe = chassis["startupProbe"]
    assert "httpGet" not in probe, "a workload on the public port would answer an httpGet"
    # The probe checks the marker's stamp against the live process, not only that the file
    # exists: a marker a killed chassis left in the kept emptyDir fails (the #107 LOW).
    assert probe["exec"]["command"] == ["python", "-m", "chassis.ready_marker", marker], (
        "the chassis image has python, no shell assumed"
    )


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
    assert [m["type"] for m in hpa["spec"]["metrics"]] == ["Resource"], "CPU only by default"


def test_hpa_scales_on_the_in_flight_gauge_when_asked(chart: Charts, tmp_path: Path) -> None:
    """CH-15: `hpa.runsInFlight.enabled` adds a Pods metric on `chassis_runs_in_flight` next to
    CPU, with `targetAverageValue` runs per pod (suggested: 24, three quarters of
    `spec.limits.max_in_flight` 32). The chassis idles on CPU while it waits for the model, so
    the in-flight gauge is the signal that scales it."""
    values = values_file(
        tmp_path,
        {"hpa": {"enabled": True, "runsInFlight": {"enabled": True, "targetAverageValue": 24}}},
    )
    hpa = one(chart.template(values), "HorizontalPodAutoscaler", RELEASE)
    metrics = hpa["spec"]["metrics"]
    assert [m["type"] for m in metrics] == ["Resource", "Pods"]
    pods = metrics[1]["pods"]
    assert pods["metric"] == {"name": "chassis_runs_in_flight"}
    assert pods["target"] == {"type": "AverageValue", "averageValue": "24"}


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
    "pod grace below the chassis drain": (
        {"terminationGracePeriodSeconds": 44},
        "chassis.drainDelaySeconds",
    ),
    "drain timeout raised past the grace": (
        {"chassis": {"drainTimeoutSeconds": 36}},
        "terminationGracePeriodSeconds",
    ),
    "drain timeout raised past the grace by an extra arg": (
        {"chassis": {"extraArgs": ["--drain-timeout-s=36"]}},
        "terminationGracePeriodSeconds",
    ),
    "drain timeout that overflows the sum": (
        {"chassis": {"drainTimeoutSeconds": 9223372036854775800}},
        "chassis.drainTimeoutSeconds",
    ),
    "drain timeout extra arg past atoi's range": (
        {"chassis": {"extraArgs": ["--drain-timeout-s", "99999999999999999999"]}},
        "is not allowed",
    ),
    "negative drain delay": (
        {"chassis": {"drainDelaySeconds": -10}},
        "chassis.drainDelaySeconds",
    ),
    "remote lane pod grace below the chassis drain": (
        REMOTE_VALUES | {"terminationGracePeriodSeconds": 44},
        "chassis.drainTimeoutSeconds",
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


@pytest.mark.parametrize(
    "values",
    [
        {"terminationGracePeriodSeconds": 45},
        {
            "terminationGracePeriodSeconds": 25,
            "chassis": {"drainDelaySeconds": 5, "drainTimeoutSeconds": 15},
        },
        {"terminationGracePeriodSeconds": 45, "chassis": {"extraArgs": ["--drain-delay-s", "10"]}},
    ],
    ids=["the default drain at exactly the bound", "a shorter drain at its bound", "extra arg"],
)
def test_the_render_passes_with_the_grace_at_exactly_the_drain_bound(
    chart: Charts, tmp_path: Path, values: dict[str, Any]
) -> None:
    """The paired control for the grace refusals: grace == delay + timeout + 5 renders."""
    pod = one(chart.template(values_file(tmp_path, values)), "Deployment", RELEASE)
    want = values["terminationGracePeriodSeconds"]
    assert pod["spec"]["template"]["spec"]["terminationGracePeriodSeconds"] == want


def test_the_kind_values_pull_nothing_and_hold_no_secret(chart: Charts) -> None:
    """`values-kind.yaml`, the kind drill's values: PoC-5 admission rule 8, and no key at all."""
    docs = chart.template(HELM_DIR / "example-agent/values-kind.yaml")
    pod = one(docs, "Deployment", RELEASE)["spec"]["template"]["spec"]
    every = [*pod.get("initContainers", []), *pod["containers"]]
    assert {c["imagePullPolicy"] for c in every} == {"Never"}
    assert "secretKeyRef" not in yaml.safe_dump(docs)
    assert one(docs, "HorizontalPodAutoscaler", RELEASE)["spec"]["minReplicas"] == 2
