"""PoC-5 secrets and the seed step, offline (plan `docs/plans/2026-10-02-poc-05-sandboxed.md`,
sections 2.9, 2.11, 2.12).

Exit criterion 3 (offline part): every internal service refuses a call without the chassis's
credential. Offline this means: the LiteLLM configs need a master key from the environment in
every non-`fake` variant and grant no MCP server to every key (H05, H07); Valkey takes its password
from a mounted file, never argv (H10); MinIO and Postgres take theirs from a Secret (H12). And the
rules of section 2.9: no Secret with data in the repo, and the seed script never prints a credential
or puts one in argv. Also the offline part of criterion 1 (the seed's static checks).
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
KIND = ROOT / "deploy/kind/poc05"
PLATFORM = KIND / "platform"
SEED = PLATFORM / "seed.sh"
KIND_LITELLM = PLATFORM / "litellm/config.yaml"
COMPOSE_LITELLM = ROOT / "deploy/compose/litellm"
NON_FAKE_CONFIGS = (COMPOSE_LITELLM / "config.local.yaml", KIND_LITELLM)
MASTER_KEY_REF = "os.environ/LITELLM_MASTER_KEY"
DIGEST = re.compile(r"@sha256:[0-9a-f]{64}$")
LOCAL_IMAGE = re.compile(r"^kind\.local/agent-platform/[a-z0-9-]+:poc05$")
UNPINNED_UNTIL_T19 = {"postgres:17.6-alpine"}
"""Third-party images whose digest is not in the repo yet. Each carries `TODO(T19)` in its file."""

SERVICES = ("litellm", "postgres", "fake-model-server", "fake-mcp-server", "valkey", "minio")
CHASSIS = {"agents.platform/role": "chassis"}
SECRET_SOURCES = re.compile(r"\b(new_secret|new_id|read_secret|openssl|curl)\b")


def _docs(path: Path) -> Iterator[dict[str, Any]]:
    for doc in yaml.safe_load_all(path.read_text()):
        if isinstance(doc, dict):
            yield doc


def _manifests(root: Path = KIND) -> Iterator[tuple[Path, dict[str, Any]]]:
    for path in sorted(root.rglob("*.yaml")):
        if path.parent.name == "litellm" or path.name == "kustomization.yaml":
            continue
        for doc in _docs(path):
            if "kind" in doc and "apiVersion" in doc:
                yield path, doc


def _platform(kind: str) -> dict[str, dict[str, Any]]:
    return {doc["metadata"]["name"]: doc for _, doc in _manifests(PLATFORM) if doc["kind"] == kind}


def _pod_spec(doc: dict[str, Any]) -> dict[str, Any]:
    """A Deployment's or a Job's pod template spec."""
    return dict(doc["spec"]["template"]["spec"])


def _workloads() -> dict[str, dict[str, Any]]:
    found = {**_platform("Deployment"), **_platform("Job")}
    assert found, "no workload under deploy/kind/poc05/platform"
    return found


def _strip_comment(line: str) -> str:
    """The line without a shell comment: a `#` outside quotes that starts a word."""
    quote = ""
    for i, ch in enumerate(line):
        if quote:
            quote = "" if ch == quote else quote
        elif ch in "'\"":
            quote = ch
        elif ch == "#" and (i == 0 or line[i - 1].isspace()):
            return line[:i].rstrip()
    return line.rstrip()


def _seed_lines() -> list[str]:
    """The seed script's code lines, comments stripped, the shebang kept."""
    raw = SEED.read_text().splitlines()
    lines = [raw[0]]
    for line in raw[1:]:
        code = _strip_comment(line)
        if code.strip():
            lines.append(code)
    return lines


def _secret_vars() -> set[str]:
    """Variables that hold a credential: set from a command that makes or reads one, or from
    another such variable (to a fixed point).
    """
    assigns = []
    for line in _seed_lines():
        m = re.match(r"^\s*(?:if\s+!\s+)?(?:local\s+)?([A-Za-z_]\w*)=\$\((.*)$", line)
        if m:
            assigns.append((m.group(1), m.group(2)))
    names: set[str] = set()
    while True:
        ref = re.compile(r"\$\{?(" + "|".join(sorted(names)) + r")\b") if names else None
        found = {
            name
            for name, source in assigns
            if SECRET_SOURCES.search(source) or (ref is not None and ref.search(source))
        }
        if found <= names:
            return names
        names |= found


# --- No Secret with data anywhere -------------------------------------------------------------


def test_no_secret_with_data_under_kind_poc05() -> None:
    """Section 2.9: every credential is made by the seed at run time; no manifest holds one."""
    for path, doc in _manifests():
        if doc["kind"] == "Secret":
            assert not doc.get("data") and not doc.get("stringData"), path.relative_to(ROOT)


def test_platform_manifests_name_the_secrets_the_seed_makes() -> None:
    """Every Secret a platform manifest references is one the seed script creates."""
    text = SEED.read_text()
    names = set()
    for doc in _workloads().values():
        names |= set(re.findall(r"'(?:secretName|name)': '([\w-]+)'", _secret_refs(doc)))
    assert names, "no Secret referenced"
    for name in names:
        assert re.search(rf"\b{re.escape(name)}\b", text), f"seed.sh never makes {name}"


def _all_platform_text() -> str:
    return "\n".join(p.read_text() for p in sorted(PLATFORM.rglob("*.yaml")))


# --- LiteLLM configs ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", NON_FAKE_CONFIGS, ids=lambda p: p.relative_to(ROOT).as_posix())
def test_non_fake_litellm_configs_require_a_master_key_from_the_environment(path: Path) -> None:
    """H05 offline: auth is on wherever LiteLLM fronts a real model or holds virtual keys."""
    config = yaml.safe_load(path.read_text())
    assert config["general_settings"]["master_key"] == MASTER_KEY_REF


@pytest.mark.parametrize(
    "path",
    (COMPOSE_LITELLM / "config.yaml", *NON_FAKE_CONFIGS),
    ids=lambda p: p.relative_to(ROOT).as_posix(),
)
def test_no_litellm_config_grants_an_mcp_server_to_every_key(path: Path) -> None:
    """H07 offline: `allow_all_keys` is absent or false on every MCP server entry."""
    config = yaml.safe_load(path.read_text()) or {}
    for name, entry in (config.get("mcp_servers") or {}).items():
        assert entry.get("allow_all_keys", False) is False, f"{path.name}: {name}"
    text = path.read_text()
    assert "sk-" not in text
    for model in config.get("model_list", []):
        key = model["litellm_params"].get("api_key")
        assert key in (None, "fake") or str(key).startswith("os.environ/"), key


def test_kind_litellm_keeps_keys_in_postgres_and_reaches_only_platform_tools() -> None:
    """Virtual keys need a database (section 2.9); the URL comes from the environment."""
    config = yaml.safe_load(KIND_LITELLM.read_text())
    assert config["general_settings"]["database_url"] == "os.environ/DATABASE_URL"
    servers = config["mcp_servers"]
    assert set(servers) == {"fake_tools", "code_runner"}, sorted(servers)
    for entry in servers.values():
        assert set(entry) <= {"url", "transport", "description"}, sorted(entry)
    assert config["litellm_settings"]["turn_off_message_logging"] is True


# --- Platform workloads: credentials and hardening -------------------------------------------


def test_every_platform_service_has_a_workload() -> None:
    assert set(SERVICES) <= set(_workloads()), sorted(_workloads())


def test_platform_images_are_pinned() -> None:
    """Third-party images by digest; ours are the local `:poc05` builds, never pulled."""
    for name, doc in _workloads().items():
        spec = _pod_spec(doc)
        for c in spec.get("initContainers", []) + spec["containers"]:
            image = c["image"]
            if LOCAL_IMAGE.match(image):
                assert c.get("imagePullPolicy") == "Never", f"{name}: {image}"
            elif image in UNPINNED_UNTIL_T19:
                assert f"{image}  # TODO(T19): pin by digest" in _all_platform_text(), image
            else:
                assert DIGEST.search(image), f"{name}: {image} is not pinned by digest"


def test_platform_pods_are_hardened() -> None:
    """Section 2.11: what PSA `restricted` needs, plus no token and a read-only root."""
    for name, doc in _workloads().items():
        spec = _pod_spec(doc)
        assert spec["automountServiceAccountToken"] is False, name
        assert spec.get("serviceAccountName") == name, f"{name}: own ServiceAccount"
        assert spec["securityContext"]["runAsNonRoot"] is True, name
        assert spec["securityContext"]["seccompProfile"]["type"] == "RuntimeDefault", name
        for key in ("hostNetwork", "hostPID", "hostIPC", "shareProcessNamespace"):
            assert not spec.get(key), f"{name}: {key}"
        for c in spec.get("initContainers", []) + spec["containers"]:
            sc = c["securityContext"]
            assert sc["readOnlyRootFilesystem"] is True, f"{name}/{c['name']}"
            assert sc["allowPrivilegeEscalation"] is False, f"{name}/{c['name']}"
            assert sc["capabilities"]["drop"] == ["ALL"], f"{name}/{c['name']}"
            assert sc["runAsUser"] > 0, f"{name}/{c['name']}"
            assert "memory" in c["resources"]["limits"], f"{name}/{c['name']}"
    for name in _workloads():
        sa = _platform("ServiceAccount").get(name)
        assert sa is not None and sa.get("automountServiceAccountToken") is False, name


def test_valkey_reads_its_password_from_a_mounted_file_never_argv() -> None:
    """H10 offline, the CH-3 debt: no password on the command line or in env."""
    spec = _pod_spec(_workloads()["valkey"])
    (c,) = spec["containers"]
    argv = " ".join([*c.get("command", []), *c.get("args", [])])
    assert "requirepass" not in argv and "VALKEY_PASSWORD" not in argv, argv
    assert not any("secretKeyRef" in str(e) for e in c.get("env", [])), c.get("env")
    secrets = {v["secret"]["secretName"] for v in spec["volumes"] if "secret" in v}
    assert secrets == {"valkey-auth"}, secrets
    conf = _platform("ConfigMap")["valkey-config"]["data"]["valkey.conf"]
    assert re.search(r"(?m)^include /etc/valkey/auth/auth\.conf$", conf), conf


def test_only_the_platform_services_get_the_platform_secrets() -> None:
    """Section 2.12: each service gets its own credentials only."""
    allowed = {
        "litellm": {"litellm-master", "litellm-db"},
        "postgres": {"litellm-db"},
        "valkey": {"valkey-auth"},
        "minio": {"minio-root"},
        "minio-init": {"minio-root", "minio-chassis"},
        "fake-model-server": set(),
        "fake-mcp-server": set(),
    }
    for name, doc in _workloads().items():
        used = set(re.findall(r"'(?:secretName|name)': '([\w-]+)'", _secret_refs(doc)))
        assert used == allowed[name], f"{name}: {sorted(used)}"


def _secret_refs(doc: dict[str, Any]) -> str:
    spec = _pod_spec(doc)
    parts: list[Any] = [v["secret"] for v in spec.get("volumes", []) if "secret" in v]
    for c in spec.get("initContainers", []) + spec["containers"]:
        parts += [e["valueFrom"]["secretKeyRef"] for e in c.get("env", []) if "valueFrom" in e]
        parts += [e["secretRef"] for e in c.get("envFrom", []) if "secretRef" in e]
    return str([{k: v for k, v in p.items() if k in ("secretName", "name")} for p in parts])


def test_platform_policies_select_chassis_pods_by_role() -> None:
    """Section 2.10: LiteLLM and Valkey admit `agents.platform/role: chassis` pods from
    `poc05-agents`; MinIO admits no chassis while no chassis reads S3 (security review F10);
    Postgres and the fake servers admit LiteLLM only; no rule uses an `ipBlock`.
    """
    policies = _platform("NetworkPolicy")
    for path, doc in _manifests(PLATFORM):
        assert "ipBlock" not in str(doc), path.name
    for name in ("litellm", "valkey"):
        (rule,) = [r for r in policies[name]["spec"]["ingress"] if _from_chassis(r)]
        assert rule["ports"], name
    assert not [r for r in policies["minio"]["spec"]["ingress"] if _from_chassis(r)]
    for name in ("postgres", "fake-model-server", "fake-mcp-server"):
        (rule,) = policies[name]["spec"]["ingress"]
        (peer,) = rule["from"]
        assert peer == {"podSelector": {"matchLabels": {"app.kubernetes.io/name": "litellm"}}}
        assert "egress" not in policies[name]["spec"], f"{name}: no egress at all"


def _from_chassis(rule: dict[str, Any]) -> bool:
    return any(
        peer.get("podSelector", {}).get("matchLabels") == CHASSIS
        and peer.get("namespaceSelector", {}).get("matchLabels")
        == {"kubernetes.io/metadata.name": "poc05-agents"}
        for peer in rule.get("from", [])
    )


# --- The seed script --------------------------------------------------------------------------


def test_seed_is_strict_and_pins_the_context() -> None:
    """Section 2.9: `set -euo pipefail`, no trace, every kubectl call on `kind-poc05`."""
    text = SEED.read_text()
    lines = _seed_lines()
    assert lines[0] == "#!/usr/bin/env bash"
    assert "set -euo pipefail" in lines
    assert "CONTEXT=kind-poc05" in lines
    for line in lines:
        assert not re.search(r"\bset\s+-\w*x|\bset\s+-o\s+xtrace|\bBASH_XTRACEFD\b", line), line
        assert not re.search(r"\bcurl\b.*\s(-v|--verbose|--trace\S*)\b", line), line
        assert "--from-literal" not in line, line
        if line.strip().startswith("for tool in "):
            continue
        for m in re.finditer(r"\bkubectl\b", line):
            assert line[m.end() :].startswith(' --context "$CONTEXT"'), f"unpinned: {line}"
    assert text.count("kubectl --context") == 1, "only the kctl helper calls kubectl"


def test_seed_makes_values_with_openssl_into_stdin() -> None:
    """Section 2.9: `openssl rand -hex`, then a pipe into `--from-file=KEY=/dev/stdin` (or an env
    file on stdin), with a client-side dry run applied by the server.
    """
    lines = _seed_lines()
    assert any(re.search(r"openssl rand -hex (32\b|\"\$\{1:-32\}\")", line) for line in lines)
    creates = [line for line in lines if "create secret" in line]
    assert creates, "seed.sh creates no Secret"
    for line in creates:
        assert re.search(r"--from-(file=([\w.-]+|\"\$\w+\")|env-file)=/dev/stdin\b", line), line
        assert "--dry-run=client -o yaml" in line, line


def test_seed_never_prints_a_credential_or_puts_one_in_argv() -> None:
    """Section 2.9: a credential variable is used only as a `printf` argument piped onward (a
    builtin, so never in a process's argv), in `[[ ]]` tests, and in `unset`. No `echo` of one.
    """
    names = _secret_vars()
    assert {"value", "master", "key"} <= names, sorted(names)
    ref = re.compile(r"\$\{?(" + "|".join(sorted(names)) + r")\b")
    for line in _seed_lines():
        if not ref.search(line):
            continue
        stripped = line.strip()
        if stripped.startswith(("unset ", "[[ ", "local ")) and "$(" not in stripped:
            continue
        body = re.sub(r"^(?:if\s+!\s+)?(?:local\s+)?\w+=\$\(", "", stripped)
        for command in re.split(r";|&&|\|\|", body):
            segments = command.split("|")
            for i, seg in enumerate(segments):
                if ref.search(seg):
                    assert seg.strip().startswith("printf "), f"credential outside printf: {line}"
                    assert i < len(segments) - 1, f"printf of a credential not piped: {line}"
        assert not re.search(r"\becho\b", line), line


def test_seed_sends_the_master_key_as_a_header_on_stdin() -> None:
    """Section 2.9: `curl -H @-`, and the new key read with `jq` from the response."""
    lines = _seed_lines()
    curls = [line for line in lines if re.search(r"\bcurl\b", line)]
    assert curls
    for line in curls:
        assert "Authorization" not in line.split("curl", 1)[1], line
    assert any("-H @-" in line and "/key/generate" in line for line in curls)
    assert any("jq -er .key" in line for line in lines)


def test_seed_is_idempotent_and_rotates_on_request() -> None:
    """Section 2.9: an existing Secret is kept unless `seed.sh rotate NAME` is asked."""
    text = SEED.read_text()
    assert re.search(r"(?m)^\s*rotate\)", text)
    assert re.search(r"(?m)^secret_exists\(\)", text)
    for name in ("litellm-master", "litellm-db", "valkey-auth", "minio-root", "minio-chassis"):
        assert name in text, name
    for svc in ("echo", "probe-sidecar", "echo-remote", "probe-remote"):
        assert f'"{svc}|' in text, svc


def test_seed_review_fixes_hold() -> None:
    """Security review F9 and F10: the Valkey ACL drops dangerous commands, and the MinIO chassis
    pair stays in the platform namespace while no chassis reads S3."""
    text = SEED.read_text()
    assert "-@admin -@dangerous" in text
    assert not re.search(r'put_env_secret "\$AGENTS" minio-chassis', text)
