"""PoC-5 H11 offline: the kind Kafka refuses a client without the chassis's credential (plan
`docs/plans/2026-10-02-poc-05-sandboxed.md`, sections 2.10, 2.12; task T25;
`notes/2026-10-02-h11-queue-exception.md`, "How it closes", step 3).

Exit criterion 3 (hard requirement 1 of ADR-001), offline part. The manifests say:

- The broker has no PLAINTEXT listener for clients: every listener is SASL_PLAINTEXT with SCRAM,
  except the KRaft controller listener, which binds loopback. The Service exposes the SASL port
  only. Topics are not auto-created.
- The SCRAM users come from the Secret `kafka-sasl`, which seed.sh makes and no manifest holds.
- In `poc05-agents`, `kafka-sasl` reaches one container: the chassis of `agent-echo-events`.
- The broker's NetworkPolicy admits that one pod on 9092, and no egress; the namespace default
  denies the rest.
- The image is pinned by digest, the one the PoC-4 Compose stack runs, and the pod has the
  section 2.11 hardening.

The kind half (the workload refused, the chassis's publish landing) is the H11 case in
`test_poc05_kind_hardreq1.py`.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml
from chassis.core.results import TASK_COMPLETED, TASK_FAILED
from chassis.server.config import load_config
from test_poc05_admission_static import _Params, broken_rules

ROOT = Path(__file__).resolve().parents[3]
POC05 = ROOT / "deploy/kind/poc05"
KAFKA = POC05 / "platform/kafka.yaml"
AGENT = POC05 / "agents/agent-echo-events.yaml"
CONFIG = POC05 / "agents/chassis/echo-events.yaml"
SEED = POC05 / "platform/seed.sh"
DEFAULT_DENY = POC05 / "base/default-deny.yaml"
PARAMS = POC05 / "admission/params.yaml"
COMPOSE = ROOT / "deploy/compose/docker-compose.scale.yaml"

SECRET = "kafka-sasl"
CHASSIS_IMAGE = "kind.local/agent-platform/chassis:"
DIGEST = re.compile(r"@sha256:[0-9a-f]{64}$")
MEMORY_LIMIT_MAX_MI = 768
"""suggested: the broker must fit a laptop kind node."""

Doc = dict[str, Any]


def _docs(path: Path) -> list[Doc]:
    return [d for d in yaml.safe_load_all(path.read_text()) if isinstance(d, dict)]


def _one(path: Path, kind: str, name: str) -> Doc:
    (doc,) = [d for d in _docs(path) if d["kind"] == kind and d["metadata"]["name"] == name]
    return doc


def _properties(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            key, _, value = line.partition("=")
            out[key.strip()] = value.strip()
    return out


def _broker() -> dict[str, str]:
    return _properties(_one(KAFKA, "ConfigMap", "kafka-config")["data"]["server.properties"])


def _start_sh() -> str:
    return str(_one(KAFKA, "ConfigMap", "kafka-config")["data"]["start.sh"])


def _pod_spec(doc: Doc) -> Doc:
    return dict(doc["spec"]["template"]["spec"])


def _containers(spec: Doc) -> list[Doc]:
    return [*spec.get("initContainers", []), *spec["containers"]]


def _listeners(props: dict[str, str]) -> dict[str, tuple[str, str]]:
    """Listener name -> (host, security protocol)."""
    protocols = dict(p.split(":", 1) for p in props["listener.security.protocol.map"].split(","))
    out: dict[str, tuple[str, str]] = {}
    for entry in props["listeners"].split(","):
        name, _, rest = entry.partition("://")
        host = rest.rpartition(":")[0]
        out[name] = (host, protocols[name])
    return out


def _secret_names(container: Doc) -> set[str]:
    names = {
        e["valueFrom"]["secretKeyRef"]["name"]
        for e in container.get("env", [])
        if "secretKeyRef" in e.get("valueFrom", {})
    }
    names |= {f["secretRef"]["name"] for f in container.get("envFrom", []) if "secretRef" in f}
    return names


def _secret_volumes(spec: Doc) -> dict[str, str]:
    """Volume name -> Secret name."""
    return {v["name"]: v["secret"]["secretName"] for v in spec.get("volumes", []) if "secret" in v}


# --- listeners and topics -----------------------------------------------------------------------


def test_the_broker_has_no_plaintext_listener_for_clients() -> None:
    """H11 offline: every listener but the controller is SASL_PLAINTEXT; the controller listener
    is PLAINTEXT on loopback only. The control: at least one SASL listener exists, and it is the
    inter-broker one.
    """
    props = _broker()
    listeners = _listeners(props)
    controllers = set(props["controller.listener.names"].split(","))
    sasl = {n for n, (_, proto) in listeners.items() if proto == "SASL_PLAINTEXT"}
    assert sasl, listeners
    assert props["inter.broker.listener.name"] in sasl
    for name, (host, proto) in listeners.items():
        if name in sasl:
            continue
        assert name in controllers, f"{name}: {proto} and not a controller listener"
        assert host == "127.0.0.1", f"controller listener {name} binds {host!r}, not loopback"
    assert not re.search(r"PLAINTEXT://", props.get("advertised.listeners", ""))


def test_only_scram_is_enabled() -> None:
    props = _broker()
    mechanisms = set(props["sasl.enabled.mechanisms"].split(","))
    assert mechanisms <= {"SCRAM-SHA-512", "SCRAM-SHA-256"} and mechanisms, mechanisms
    assert props["sasl.mechanism.inter.broker.protocol"] in mechanisms
    start = _start_sh()
    assert "advertised.listeners=SASL://" in start, "the advertised listener is the SASL one"


def test_the_service_and_the_pod_expose_the_sasl_port_only() -> None:
    sasl_ports = {
        int(host_port.rpartition(":")[2])
        for host_port in _broker()["listeners"].split(",")
        if host_port.startswith("SASL://")
    }
    service = _one(KAFKA, "Service", "kafka")
    assert {p["targetPort"] for p in service["spec"]["ports"]} == sasl_ports
    (container,) = _pod_spec(_one(KAFKA, "Deployment", "kafka"))["containers"]
    assert {p["containerPort"] for p in container["ports"]} == sasl_ports


def test_topics_are_not_auto_created_and_seed_makes_the_result_topics() -> None:
    assert _broker()["auto.create.topics.enable"] == "false"
    seed = SEED.read_text()
    topics = re.search(r"(?m)^KAFKA_TOPICS=\((.*)\)$", seed)
    assert topics is not None
    assert set(topics.group(1).split()) == {TASK_COMPLETED, TASK_FAILED}
    assert re.search(r"(?m)^\s*kafka-topics\) preflight; seed_kafka_topics ;;$", seed)


# --- credentials --------------------------------------------------------------------------------


def test_scram_users_come_from_the_secret_only() -> None:
    """No credential in the manifest: the properties hold no JAAS line or password, start.sh
    reads both passwords from the mounted `kafka-sasl`, and seed.sh makes that Secret in both
    namespaces from `openssl rand`.
    """
    text = _one(KAFKA, "ConfigMap", "kafka-config")["data"]["server.properties"]
    assert "jaas" not in text and "password" not in text.lower()
    spec = _pod_spec(_one(KAFKA, "Deployment", "kafka"))
    assert set(_secret_volumes(spec).values()) == {SECRET}
    (container,) = spec["containers"]
    assert _secret_names(container) == set()
    mounts = {m["name"]: m["mountPath"] for m in container["volumeMounts"]}
    (volume,) = _secret_volumes(spec)
    start = _start_sh()
    for key in ("ADMIN_PASSWORD", "CHASSIS_PASSWORD"):
        assert f'$(<"$auth/{key}")' in start, key
    assert f"auth={mounts[volume]}" in start
    assert "set -x" not in start and not re.search(r"\becho\b", start)
    assert "--add-scram" in start
    seed = SEED.read_text()
    assert re.search(r'put_env_secret "\$PLATFORM" kafka-sasl', seed)
    assert re.search(r'put_secret "\$AGENTS" kafka-sasl KAFKA_SASL_PASSWORD', seed)
    assert re.search(r"(?m)^\s*kafka\) preflight; seed_kafka ;;$", seed)


def test_no_secret_object_under_kind_poc05_holds_kafka_sasl() -> None:
    for path in sorted(POC05.rglob("*.yaml")):
        for doc in _docs(path):
            if doc.get("kind") == "Secret":
                assert doc["metadata"]["name"] != SECRET, path


def test_the_secret_reaches_only_the_chassis_container_of_the_events_agent() -> None:
    """H11's point: the workload shares the chassis's network, not its credential. In the agent
    pod, only the chassis container names `kafka-sasl`, and there is no Secret volume at all.
    Across every pod under deploy/kind/poc05, only the broker and that chassis name it.
    """
    spec = _pod_spec(_one(AGENT, "Deployment", "agent-echo-events"))
    assert _secret_volumes(spec) == {}
    users = {c["name"]: _secret_names(c) for c in _containers(spec)}
    assert users == {"chassis": {SECRET}, "workload": set()}, users
    (chassis,) = [c for c in _containers(spec) if c["name"] == "chassis"]
    assert chassis["image"].startswith(CHASSIS_IMAGE)
    env = {e["name"]: e for e in chassis["env"]}
    assert env["KAFKA_SECURITY_PROTOCOL"]["value"] == "SASL_PLAINTEXT"
    assert env["KAFKA_SASL_PASSWORD"]["valueFrom"]["secretKeyRef"]["name"] == SECRET
    assert "value" not in env["KAFKA_SASL_PASSWORD"]

    named: set[str] = set()
    for path in sorted(POC05.rglob("*.yaml")):
        if "agent-sandbox" in path.parts or "fixtures" in path.parts:
            continue
        for doc in _docs(path):
            template = doc.get("spec", {}).get("template", {}) if isinstance(doc, dict) else {}
            if not isinstance(template, dict) or "spec" not in template:
                continue
            pod = template["spec"]
            if SECRET in set(_secret_volumes(pod).values()) or any(
                SECRET in _secret_names(c) for c in _containers(pod)
            ):
                named.add(doc["metadata"]["name"])
    assert named == {"kafka", "agent-echo-events"}, named


def test_the_events_config_publishes_through_kafka() -> None:
    config = load_config(CONFIG)
    assert config.spec.adapters is not None
    assert config.spec.adapters.events == "kafka"
    assert config.spec.events.result_events is True
    pod = _one(AGENT, "Deployment", "agent-echo-events")
    names = {v["configMap"]["name"] for v in _pod_spec(pod)["volumes"] if "configMap" in v}
    assert names == {"chassis-echo-events-config"}


def test_the_events_agent_passes_the_admission_rules() -> None:
    """The Python model of `admission/policy.yaml`: the only Secret user is the chassis image."""
    params = _Params.of(_docs(PARAMS)[0])
    doc = _one(AGENT, "Deployment", "agent-echo-events")
    assert broken_rules(doc, params, "poc05-agents") == set()


# --- network ------------------------------------------------------------------------------------


def test_the_network_policy_admits_the_events_agent_on_the_sasl_port_only() -> None:
    policy = _one(KAFKA, "NetworkPolicy", "kafka")
    spec = policy["spec"]
    assert spec["podSelector"] == {"matchLabels": {"app.kubernetes.io/name": "kafka"}}
    assert sorted(spec["policyTypes"]) == ["Egress", "Ingress"]
    assert not spec.get("egress"), "the broker calls nothing"
    (rule,) = spec["ingress"]
    assert rule["ports"] == [{"port": 9092, "protocol": "TCP"}]
    assert rule["from"] == [
        {
            "namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": "poc05-agents"}},
            "podSelector": {"matchLabels": {"app.kubernetes.io/name": "agent-echo-events"}},
        }
    ]
    agent = _one(AGENT, "Deployment", "agent-echo-events")
    labels = agent["spec"]["template"]["metadata"]["labels"]
    assert labels["app.kubernetes.io/name"] == "agent-echo-events"


def test_the_platform_namespace_denies_by_default() -> None:
    deny = [
        d
        for d in _docs(DEFAULT_DENY)
        if d["kind"] == "NetworkPolicy" and d["metadata"].get("namespace") == "poc05-platform"
    ]
    assert len(deny) == 1
    assert deny[0]["spec"] == {"podSelector": {}, "policyTypes": ["Ingress", "Egress"]}
    assert _one(KAFKA, "Deployment", "kafka")["metadata"]["namespace"] == "poc05-platform"


def test_the_events_agent_egress_is_dns_and_kafka_only() -> None:
    spec = _one(AGENT, "NetworkPolicy", "agent-echo-events")["spec"]
    peers = [
        (peer["podSelector"]["matchLabels"], [p["port"] for p in rule["ports"]])
        for rule in spec["egress"]
        for peer in rule["to"]
    ]
    assert peers == [
        ({"k8s-app": "kube-dns"}, [53, 53]),
        ({"app.kubernetes.io/name": "kafka"}, [9092]),
    ]


# --- image and hardening ------------------------------------------------------------------------


def test_the_image_is_pinned_and_is_the_compose_one() -> None:
    (container,) = _pod_spec(_one(KAFKA, "Deployment", "kafka"))["containers"]
    assert DIGEST.search(container["image"]), container["image"]
    compose = yaml.safe_load(COMPOSE.read_text())
    assert container["image"] == compose["services"]["kafka"]["image"]


def test_the_broker_pod_is_hardened_and_small() -> None:
    doc = _one(KAFKA, "Deployment", "kafka")
    spec = _pod_spec(doc)
    assert spec["automountServiceAccountToken"] is False
    assert spec["serviceAccountName"] == "kafka"
    sa = _one(KAFKA, "ServiceAccount", "kafka")
    assert sa["automountServiceAccountToken"] is False
    assert spec["securityContext"]["runAsNonRoot"] is True
    assert spec["securityContext"]["seccompProfile"] == {"type": "RuntimeDefault"}
    for key in ("hostNetwork", "hostPID", "hostIPC", "shareProcessNamespace"):
        assert not spec.get(key), key
    (c,) = spec["containers"]
    sc = c["securityContext"]
    assert sc["readOnlyRootFilesystem"] is True
    assert sc["allowPrivilegeEscalation"] is False
    assert sc["capabilities"] == {"drop": ["ALL"]}
    assert sc["runAsUser"] > 0
    memory = c["resources"]["limits"]["memory"]
    assert memory.endswith("Mi") and int(memory[:-2]) <= MEMORY_LIMIT_MAX_MI, memory
    heap = re.search(r'KAFKA_HEAP_OPTS="-Xms(\d+)m -Xmx(\d+)m"', _start_sh())
    assert heap is not None and int(heap.group(2)) <= int(memory[:-2]) // 2, heap
    for v in spec["volumes"]:
        if "emptyDir" in v:
            assert v["emptyDir"].get("sizeLimit"), v["name"]
