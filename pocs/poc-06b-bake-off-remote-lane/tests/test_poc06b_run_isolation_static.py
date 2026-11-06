"""PoC-6b, the remote lane: one fresh remote pod per run, offline (core review R6-11).

Exit criteria 1 and 4: a remote engine no longer runs in one long-lived Sandbox that every run
shares. Each is a SandboxTemplate and SandboxWarmPool; the front door
`deploy/kind/poc06/platform/remote-run-dispatch.yaml` claims one pod per A2A run and deletes the
claim after. These tests read the manifests only; `test_poc06b_kind_run_isolation.py` runs the
spec case on kind.

Run: `make test-poc POC=06b`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml
from chassis.server.config import ChassisConfig
from fake_model_server import Script

ROOT = Path(__file__).resolve().parents[3]
POC06 = ROOT / "deploy/kind/poc06"
FRONT_DOOR_FILE = POC06 / "platform/remote-run-dispatch.yaml"
RBAC_FILE = POC06 / "admission/remote-run-rbac.yaml"
DOOR = "remote-run-dispatch"
DOOR_URL = "http://remote-run-dispatch.poc05-platform.svc.cluster.local:9000"
NAME = "app.kubernetes.io/name"
NS_LABEL = "kubernetes.io/metadata.name"
ENGINES = {
    "smolagents": ("remote/remote-smolagents.yaml", "agents/chassis/smolagents-remote.yaml"),
    "claude-agent": ("remote/remote-claude-agent.yaml", "agents/chassis/claude-agent-remote.yaml"),
    "typescript": ("remote/remote-typescript.yaml", "agents/chassis/typescript-remote.yaml"),
    "kagent-adk": ("kagent/remote-kagent-adk.yaml", "kagent/chassis/kagent-adk-remote.yaml"),
}
Doc = dict[str, Any]


def _docs(path: Path) -> list[Doc]:
    return [d for d in yaml.safe_load_all(path.read_text()) if d]


def _one(path: Path, kind: str, name: str) -> Doc:
    (found,) = [d for d in _docs(path) if d["kind"] == kind and d["metadata"]["name"] == name]
    return found


def _remote_policies() -> list[Doc]:
    docs = [
        *_docs(POC06 / "remote/network-policy.yaml"),
        *_docs(POC06 / "kagent/remote-kagent-adk.yaml"),
    ]
    return [d for d in docs if d["kind"] == "NetworkPolicy"]


def _peer_names(rules: list[Doc], key: str) -> set[tuple[str, str]]:
    return {
        (
            peer["namespaceSelector"]["matchLabels"][NS_LABEL],
            peer["podSelector"]["matchLabels"][NAME],
        )
        for rule in rules
        for peer in rule.get(key, [])
        if "podSelector" in peer
    }


@pytest.mark.parametrize("folder", ["remote", "kagent"])
def test_no_long_lived_sandbox_is_left_in_the_folder(folder: str) -> None:
    """A Sandbox is one pod for every run; none is left. No Service either: the door dials pods."""
    kinds = {d["kind"] for f in (POC06 / folder).glob("*.yaml") for d in _docs(f)}
    assert "Sandbox" not in kinds
    services = [
        d["metadata"]["name"]
        for f in (POC06 / folder).glob("*.yaml")
        for d in _docs(f)
        if d["kind"] == "Service"
        and d["metadata"].get("namespace", "poc05-remote") == "poc05-remote"
    ]
    assert services == []


@pytest.mark.parametrize("engine", sorted(ENGINES))
def test_each_engine_is_a_template_and_a_warm_pool_set_like_code_runner(engine: str) -> None:
    """Template rules T1 to T5 (deploy/kind/poc05/admission/extension-policy.yaml)."""
    path = POC06 / ENGINES[engine][0]
    name = f"remote-{engine}"
    template = _one(path, "SandboxTemplate", name)["spec"]
    pool = _one(path, "SandboxWarmPool", name)["spec"]

    assert template["networkPolicyManagement"] == "Unmanaged"
    assert template["envVarsInjectionPolicy"] == "Disallowed"
    assert template["volumeClaimTemplatesPolicy"] == "Disallowed"
    assert template["service"] is False
    assert "networkPolicy" not in template and "volumeClaimTemplates" not in template
    assert template["podTemplate"]["spec"]["runtimeClassName"] == "gvisor"
    assert pool["sandboxTemplateRef"] == {"name": name}
    assert pool["replicas"] >= 1


@pytest.mark.parametrize("engine", sorted(ENGINES))
def test_the_chassis_url_is_the_front_door(engine: str) -> None:
    config = ChassisConfig.model_validate(yaml.safe_load((POC06 / ENGINES[engine][1]).read_text()))
    assert config.spec.engine.url == f"{DOOR_URL}/{engine}"


def test_the_front_door_has_a_card_for_each_engine() -> None:
    cards = _one(FRONT_DOOR_FILE, "ConfigMap", f"{DOOR}-cards")["data"]
    assert set(cards) == {f"{e}.json" for e in ENGINES}
    for text in cards.values():
        card = yaml.safe_load(text)
        bindings = [i["protocolBinding"] for i in card["supportedInterfaces"]]
        assert bindings == ["JSONRPC"]


@pytest.mark.parametrize("policy", _remote_policies(), ids=lambda d: d["metadata"]["name"])
def test_a_remote_takes_9000_only_from_the_front_door(policy: Doc) -> None:
    spec = policy["spec"]
    assert _peer_names(spec["ingress"], "from") == {("poc05-platform", DOOR)}
    assert [p["port"] for r in spec["ingress"] for p in r["ports"]] == [9000]


def test_the_front_door_policy_edges() -> None:
    """In from the four remote chassis on 9000; out to the four remote pools on 9000 and to the
    API server sentinel on 6443, nothing else (no DNS)."""
    spec = _one(FRONT_DOOR_FILE, "NetworkPolicy", DOOR)["spec"]
    assert spec["podSelector"]["matchLabels"] == {NAME: DOOR}
    assert _peer_names(spec["ingress"], "from") == {
        ("poc05-agents", f"chassis-{e}-remote") for e in ENGINES
    }
    assert _peer_names(spec["egress"], "to") == {("poc05-remote", f"remote-{e}") for e in ENGINES}
    blocks = [peer["ipBlock"] for r in spec["egress"] for peer in r["to"] if "ipBlock" in peer]
    assert blocks == [{"cidr": "__API_SERVER_IP__/32"}]
    ports = sorted({p["port"] for r in spec["egress"] for p in r["ports"]})
    assert ports == [6443, 9000]


def test_the_front_door_pod_holds_no_key_and_one_projected_token() -> None:
    deploy = _one(FRONT_DOOR_FILE, "Deployment", DOOR)["spec"]["template"]["spec"]
    (container,) = deploy["containers"]
    assert deploy["automountServiceAccountToken"] is False
    assert deploy["serviceAccountName"] == DOOR
    assert "runtimeClassName" not in deploy  # runc
    assert "env" not in container and "envFrom" not in container
    assert not [v for v in deploy["volumes"] if "secret" in v]
    projected = [v for v in deploy["volumes"] if "projected" in v]
    assert len(projected) == 1
    assert container["args"][0] == "a2a-dispatch"
    sc = container["securityContext"]
    assert sc["readOnlyRootFilesystem"] is True and sc["allowPrivilegeEscalation"] is False
    assert sc["capabilities"]["drop"] == ["ALL"] and sc["runAsNonRoot"] is True


def test_the_front_door_role_is_create_get_delete_on_claims_in_poc05_remote_only() -> None:
    role = _one(RBAC_FILE, "Role", "remote-run-claims")
    binding = _one(RBAC_FILE, "RoleBinding", "remote-run-claims")
    assert role["metadata"]["namespace"] == "poc05-remote"
    assert role["rules"] == [
        {
            "apiGroups": ["extensions.agents.x-k8s.io"],
            "resources": ["sandboxclaims"],
            "verbs": ["create", "get", "delete"],
        }
    ]
    assert binding["subjects"] == [
        {"kind": "ServiceAccount", "name": DOOR, "namespace": "poc05-platform"}
    ]
    kinds = {d["kind"] for d in _docs(RBAC_FILE)}
    assert kinds == {"Role", "RoleBinding", "ResourceQuota"}


def test_a_quota_caps_claims_and_pods_in_poc05_remote() -> None:
    quota = _one(RBAC_FILE, "ResourceQuota", "remote-run")
    assert quota["metadata"]["namespace"] == "poc05-remote"
    hard = quota["spec"]["hard"]
    assert int(hard["count/sandboxclaims.extensions.agents.x-k8s.io"]) > 0
    assert int(hard["pods"]) > 0


def test_run_sh_applies_the_rbac_and_the_front_door_and_waits_for_pools() -> None:
    text = (POC06 / "run.sh").read_text()
    assert 'kctl apply -f "$HERE/admission/remote-run-rbac.yaml"' in text
    assert '"$P5" api-ip-fill "$ip" <"$FRONT_DOOR"' in text
    assert "wait_pool" in text and "wait_sandbox" not in text


SCRIPT = Script.from_yaml(POC06 / "platform/fake-model-script.yaml")


def _bash(rule: Any) -> str:
    assert rule.tool_call is not None and rule.tool_call.name == "Bash", rule
    return str(rule.tool_call.arguments["command"])


def test_the_fake_model_plants_then_probes_in_one_run_and_stops() -> None:
    """The kind test's script: `[plant]` writes the marker and starts `sleep 600`, the tool result
    `PLANTED` leads to the probe (the control), and the probe's result ends the run."""
    plant = _bash(SCRIPT.pick([{"role": "user", "content": "isolation [plant]"}]))
    after_plant = SCRIPT.pick(
        [
            {"role": "user", "content": "isolation [plant]"},
            {"role": "tool", "content": "PLANTED", "tool_call_id": "t1"},
        ]
    )
    end = SCRIPT.pick(
        [
            {"role": "user", "content": "isolation [plant]"},
            {"role": "tool", "content": "PROBE host=x marker=yes sleepers=1", "tool_call_id": "t2"},
        ]
    )

    assert "/tmp/marker" in plant and "sleep 600 " in plant and "&" in plant
    assert _bash(after_plant).startswith("echo PROBE host=")
    assert end.tool_call is None and end.reply == "isolation probe done"


def test_the_fake_model_probe_alone_reads_marker_and_processes() -> None:
    probe = _bash(SCRIPT.pick([{"role": "user", "content": "isolation [probe]"}]))
    assert "test -e /tmp/marker" in probe and "/proc/" in probe and "'^sleep 600'" in probe
