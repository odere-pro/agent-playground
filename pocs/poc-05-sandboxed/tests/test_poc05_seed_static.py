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

import hashlib
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
RUN_SH = KIND / "run.sh"
AGENT_SANDBOX = KIND / "base/agent-sandbox"
KIND_LITELLM = PLATFORM / "litellm/config.yaml"
COMPOSE_LITELLM = ROOT / "deploy/compose/litellm"
NON_FAKE_CONFIGS = (COMPOSE_LITELLM / "config.local.yaml", KIND_LITELLM)
MASTER_KEY_REF = "os.environ/LITELLM_MASTER_KEY"
DIGEST = re.compile(r"@sha256:[0-9a-f]{64}$")
LOCAL_IMAGE = re.compile(r"^kind\.local/agent-platform/[a-z0-9-]+:poc05$")
UNPINNED_UNTIL_T19 = {"postgres:17.6-alpine"}
"""Third-party images whose digest is not in the repo yet. Each carries `TODO(T19)` in its file."""

SERVICES = (
    "litellm",
    "postgres",
    "fake-model-server",
    "fake-mcp-server",
    "valkey",
    "minio",
    "code-runner-dispatch",
)
DISPATCHER = "code-runner-dispatch"
CODE_RUNNER_URL = "http://code-runner-dispatch.poc05-platform.svc.cluster.local:8000/mcp"
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


def test_kind_litellm_reaches_the_code_runner_only_through_the_dispatcher() -> None:
    """Per-call sandbox design: the server name stays `code_runner`; its URL is the dispatcher's
    Service, never a sandbox pod or a `poc05-tools` Service."""
    servers = yaml.safe_load(KIND_LITELLM.read_text())["mcp_servers"]
    assert servers["code_runner"]["url"] == CODE_RUNNER_URL
    assert not [s["url"] for s in servers.values() if "poc05-tools" in s["url"]]
    (svc,) = [
        d
        for _, d in _manifests(PLATFORM)
        if d["kind"] == "Service" and d["metadata"]["name"] == DISPATCHER
    ]
    assert svc["spec"]["selector"] == {"app.kubernetes.io/name": DISPATCHER}
    assert [(p["port"], p["targetPort"]) for p in svc["spec"]["ports"]] == [(8000, 8000)]


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
        DISPATCHER: set(),
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
    Postgres and the fake servers admit LiteLLM only; no rule uses an `ipBlock` but the
    dispatcher's one block to the API server (test_poc05_netpol_static.py pins it down).
    """
    policies = _platform("NetworkPolicy")
    for path, doc in _manifests(PLATFORM):
        if doc["kind"] == "NetworkPolicy" and doc["metadata"]["name"] == DISPATCHER:
            continue
        assert "ipBlock" not in str(doc), f"{path.name}: {doc['metadata']['name']}"
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
    for svc in ("echo", "echo-remote"):
        assert f'"{svc}|' in text, svc


def test_seed_mints_no_probe_credentials() -> None:
    """Security review 2026-10-02, item 8: T10 (the probe pods) is dropped, so no pod mounts a
    probe Secret. The seed mints no `chassis-probe-*-litellm` key and no `remote-probe-token`.
    Control: the echo service and the echo remote are still seeded.
    """
    text = SEED.read_text()
    services = re.search(r"(?ms)^SERVICES=\((.*?)^\)", text)
    remotes = re.search(r"(?m)^REMOTES=\((.*)\)$", text)
    assert services and remotes
    names = re.findall(r'"([\w-]+)\|', services.group(1))
    assert names == ["echo", "echo-remote"], names
    assert remotes.group(1).split() == ["echo"]
    code = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
    assert "probe" not in code


def test_seed_review_fixes_hold() -> None:
    """Security review F9 and F10: the Valkey ACL drops dangerous commands, and the MinIO chassis
    pair stays in the platform namespace while no chassis reads S3."""
    text = SEED.read_text()
    assert "-@admin -@dangerous" in text
    assert not re.search(r'put_env_secret "\$AGENTS" minio-chassis', text)


# --- run.sh: the gateway key, the Job, the pool, the pinned manifest (reviews of 2026-10-09) ---


def _function(name: str) -> str:
    match = re.search(rf"(?ms)^{name}\(\) \{{\n(.*?)^\}}", RUN_SH.read_text())
    assert match, f"run.sh has no {name}()"
    return match.group(1)


def _before(body: str, first: str, then: str) -> bool:
    return 0 <= body.find(first) < body.find(then)


def test_with_gateway_reads_the_key_with_tracing_off_and_masks_it_in_ci() -> None:
    """Security review LOW: under `bash -x` the key would be printed; in CI it is not a
    registered secret. Tracing goes off before the Secret is read; under GITHUB_ACTIONS the key is
    masked before it is exported or any command runs."""
    body = _function("with_gateway")
    read = "get secret chassis-echo-litellm"
    assert _before(body, "{ set +x; } 2>/dev/null", read)
    assert re.search(r"\[\[ -z \$\{GITHUB_ACTIONS:-\} \]\] \|\| printf '::add-mask::%s\\n'", body)
    assert _before(body, read, "::add-mask::")
    assert _before(body, "::add-mask::", "export POC05_CHASSIS_VIRTUAL_KEY")
    assert _before(body, "::add-mask::", '"$@"')
    assert _before(body, "export POC05_CHASSIS_VIRTUAL_KEY", "set -x")


def test_run_sh_test_runs_through_with_gateway() -> None:
    """Code review MEDIUM: `run.sh test` skipped the code-runner and gateway tests without it."""
    body = _function("run_tests")
    assert "with_gateway env POC05_KIND=1 uv run pytest -m network" in body


def test_replace_changed_job_deletes_a_failed_job_regardless_of_the_dry_run() -> None:
    """Code review MEDIUM: a Failed minio-init with an unchanged manifest blocked `up`."""
    body = _function("replace_changed_job")
    failed = body.index("if [[ $finished == *Failed* ]]; then")
    assert _before(body[failed:], 'delete job "$name"', "return 0")
    assert failed < body.index("--dry-run=server")


def test_the_tools_step_waits_on_the_warm_pool() -> None:
    """Per-call design: no `code-runner` Sandbox to wait on; the pool's readyReplicas instead."""
    body = _function("apply_tools")
    assert "wait_pool poc05-tools code-runner" in body
    assert "wait_sandbox" not in body
    assert "{.status.readyReplicas}" in _function("wait_pool")


def test_the_platform_wait_includes_the_dispatcher() -> None:
    assert "code-runner-dispatch" in _function("apply_platform")


def test_run_sh_pins_the_with_extensions_manifest_it_installs() -> None:
    """The vendored file is the one kustomization.yaml installs, and its sha256 is run.sh's."""
    text = RUN_SH.read_text()
    (want,) = re.findall(r"(?m)^AGENT_SANDBOX_SHA256=([0-9a-f]{64})$", text)
    assert want == "b150cb058c577c59c42b060ff7f22e31b5311ca80430db98129f1280a0e85970"
    resources = yaml.safe_load((AGENT_SANDBOX / "kustomization.yaml").read_text())["resources"]
    assert resources == ["upstream-v1.0.5-with-extensions.yaml"]
    assert f"AGENT_SANDBOX_MANIFEST=$HERE/base/agent-sandbox/{resources[0]}" in text
    manifest = AGENT_SANDBOX / resources[0]
    assert hashlib.sha256(manifest.read_bytes()).hexdigest() == want
    body = manifest.read_text()
    assert "aggregate-to-" not in body, "a vendored ClusterRole would flow into admin/edit/view"
    assert "- --extensions" in body
    for crd in ("sandboxtemplates", "sandboxwarmpools", "sandboxclaims"):
        assert f"{crd}.extensions.agents.x-k8s.io" in _function("install_agent_sandbox") or (
            f"{crd}.extensions.agents.x-k8s.io" in text
        )


def test_admission_applies_the_extension_rules_in_order_with_a_canary() -> None:
    """Task 4: each policy before its binding, all three type-checked, a rule-T1 canary next to
    the rule-1 one, each with its admitted twin."""
    body = _function("admission")
    order = [
        "params.yaml",
        "rbac.yaml",
        '"$dir/policy.yaml"',
        "extension-policy.yaml",
        "type_checked",
        '"$dir/binding.yaml"',
        "extension-binding.yaml",
        "canary agent-trust-rule",
        "canary sandbox-template-rule",
    ]
    assert [body.find(s) for s in order] == sorted(body.find(s) for s in order), body
    assert all(body.find(s) >= 0 for s in order)
    assert "agent-trust-rule sandbox-template-rule sandbox-claim-rule" in body
    assert "ruleT1-network-policy-unmanaged/rejected-absent.yaml" in body
    assert "ruleT1-network-policy-unmanaged/admitted.yaml" in body
    assert '"$fx/ruleT1-network-policy-unmanaged/admitted.yaml" "template rule T1"' in body
    canary = _function("canary")
    assert '--as="$DEPLOYER"' in canary and "--dry-run=server" in canary
    assert '*"$message"*' in canary
