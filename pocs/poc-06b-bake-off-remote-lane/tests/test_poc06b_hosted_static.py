"""Static checks of the PoC-6 hosted kind run (T-MAC part 2). Offline: no Docker, no kind, no key.

What they pin:

- the LiteLLM config for the hosted run is PoC-5's `config.yaml` plus exactly one `model_list`
  entry, `big-default`, with no literal key;
- the egress NetworkPolicy selects only LiteLLM, allows only TCP 443, and keeps every `except`
  range (private, link-local, loopback, shared address space, and the kind pod and service CIDRs);
- `hosted.sh` pins the kind context, never puts a key in argv, never exports one, never traces;
- the chassis, Secrets, ConfigMaps, and engines `hosted.sh` names exist in the manifests, and the
  facts its route switch depends on still hold (one `route:` line per chassis config; kagent-adk
  names its own model);
- `scripts/poc06_mac.sh` has a `kind` step: it runs last, `--dry-run` prints its commands with no
  Docker, and the hosted and slm steps still refuse an untrusted engine;
- the relay (`hosted/relay.py`) pins the context and carries bytes both ways (with a fake kubectl).
"""

from __future__ import annotations

import ipaddress
import json
import os
import re
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Any

import pytest
import yaml
from bakeoff.registry import ENGINES

ROOT = Path(__file__).resolve().parents[3]
POC05 = ROOT / "deploy/kind/poc05"
POC06 = ROOT / "deploy/kind/poc06"
HOSTED = POC06 / "hosted"
HOSTED_SH = POC06 / "hosted.sh"
RUN_SH = POC06 / "run.sh"
SEED_SH = POC06 / "seed.sh"
MAC_SH = ROOT / "scripts/poc06_mac.sh"
CONFIG_P5 = POC05 / "platform/litellm/config.yaml"
CONFIG_P6 = HOSTED / "litellm-config.yaml"
POLICY = HOSTED / "network-policy.yaml"
RELAY = HOSTED / "relay.py"
UNTRUSTED = sorted(e.name for e in ENGINES if not e.trusted)

NEW_ENTRY = {
    "model_name": "big-default",
    "litellm_params": {
        "model": "__POC06_HOSTED_MODEL__",
        "api_key": "os.environ/POC06_PROVIDER_KEY",  # pragma: allowlist secret
    },
}
REQUIRED_EXCEPT = {
    "10.0.0.0/8",
    "172.16.0.0/12",
    "192.168.0.0/16",  # RFC 1918
    "169.254.0.0/16",  # link-local, the metadata service
    "127.0.0.0/8",  # loopback
    "100.64.0.0/10",  # carrier-grade NAT
    "10.244.0.0/16",  # the kind pod CIDR
    "10.96.0.0/16",  # the kind service CIDR
}


def _load(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text())
    assert isinstance(data, dict)
    return data


def _code(path: Path) -> list[str]:
    return [ln for ln in path.read_text().splitlines() if not ln.lstrip().startswith("#")]


def _statements(path: Path) -> list[str]:
    """The code lines with backslash continuations and pipe-ended lines joined."""
    out: list[str] = []
    pending = ""
    for line in _code(path):
        stripped = line.strip()
        pending = f"{pending} {stripped}" if pending else stripped
        if pending.endswith(("\\", "|", "&&", "||")):
            pending = pending.rstrip("\\").rstrip()
            continue
        out.append(pending)
        pending = ""
    if pending:
        out.append(pending)
    return out


# ---- the LiteLLM config -----------------------------------------------------------------------


def test_the_hosted_config_is_poc05s_plus_exactly_the_big_default_entry() -> None:
    base, hosted = _load(CONFIG_P5), _load(CONFIG_P6)
    models = hosted["model_list"]
    assert [m["model_name"] for m in models] == ["fake-chat", "big-default"]
    assert models[-1] == NEW_ENTRY
    assert len(models) == len(base["model_list"]) + 1
    hosted["model_list"] = models[:-1]
    assert hosted == base, "everything but the one entry equals PoC-5's file"


def test_the_hosted_config_text_is_poc05s_text_with_the_header_and_entry_added() -> None:
    base = CONFIG_P5.read_text()
    # The copy marks its two `api_key` lines for detect-secrets; nothing else may differ.
    text = re.sub(r"  # pragma: allowlist secret$", "", CONFIG_P6.read_text(), flags=re.M)
    lines = text.splitlines(keepends=True)
    header = lines[:5]  # the five-line PoC-6 header comment
    assert header[0].startswith("# PoC-6 copy of") and all(ln.startswith("#") for ln in header)
    body = "".join(lines[len(header) :])
    start = body.index("\n  # PoC-6 (deploy/kind/poc06/hosted.sh)")
    end = body.index("      api_key: os.environ/POC06_PROVIDER_KEY\n") + len(
        "      api_key: os.environ/POC06_PROVIDER_KEY\n"
    )
    assert body[:start] + body[end:] == base


def test_the_hosted_config_holds_no_literal_key() -> None:
    text = CONFIG_P6.read_text()
    assert not re.search(r"\bsk-[A-Za-z0-9]", text)
    for line in text.splitlines():
        match = re.match(r"\s*api_key:\s*(\S+)", line)
        if match:
            assert match.group(1) in {"fake", "os.environ/POC06_PROVIDER_KEY"}, line
    assert _load(CONFIG_P6)["general_settings"]["master_key"] == "os.environ/LITELLM_MASTER_KEY"


# ---- the egress policy ------------------------------------------------------------------------


def test_the_egress_policy_selects_litellm_only_and_adds_egress_only() -> None:
    policy = _load(POLICY)
    assert policy["kind"] == "NetworkPolicy"
    assert policy["metadata"]["namespace"] == "poc05-platform"
    spec = policy["spec"]
    assert spec["podSelector"] == {"matchLabels": {"app.kubernetes.io/name": "litellm"}}
    assert spec["policyTypes"] == ["Egress"]
    assert "ingress" not in spec
    assert len(spec["egress"]) == 1


def test_the_egress_policy_allows_only_tcp_443_to_public_addresses() -> None:
    (rule,) = _load(POLICY)["spec"]["egress"]
    assert rule["ports"] == [{"port": 443, "protocol": "TCP"}]
    assert len(rule["to"]) == 1
    assert set(rule["to"][0]) == {"ipBlock"}, "no pod, namespace, or other selector"
    block = rule["to"][0]["ipBlock"]
    assert block["cidr"] == "0.0.0.0/0"
    assert set(block["except"]) >= REQUIRED_EXCEPT


def test_the_except_ranges_cover_every_private_address_and_the_cluster() -> None:
    block = _load(POLICY)["spec"]["egress"][0]["to"][0]["ipBlock"]
    excluded = [ipaddress.ip_network(c) for c in block["except"]]
    for address in (
        "10.0.0.1",
        "172.18.0.2",  # the kind node on the Docker network
        "192.168.65.254",  # Docker Desktop's host
        "169.254.169.254",  # a cloud metadata service
        "127.0.0.1",
        "100.64.0.1",
        "10.96.0.1",  # the kubernetes Service
        "10.244.1.5",  # a pod
    ):
        assert any(ipaddress.ip_address(address) in net for net in excluded), address
    for address in ("104.18.0.1", "13.107.42.14", "52.84.0.1"):
        assert not any(ipaddress.ip_address(address) in net for net in excluded), address


def test_the_except_list_keeps_the_kind_cidrs_of_the_cluster_file() -> None:
    cluster = _load(POC05 / "cluster.yaml")
    networking = cluster["networking"]
    block = _load(POLICY)["spec"]["egress"][0]["to"][0]["ipBlock"]
    assert networking["podSubnet"] in block["except"]
    assert networking["serviceSubnet"] in block["except"]


def test_poc05s_litellm_policy_already_gives_dns_so_this_file_adds_none() -> None:
    policies = list(yaml.safe_load_all((POC05 / "platform/network-policy.yaml").read_text()))
    litellm = next(p for p in policies if p and p["metadata"]["name"] == "litellm")
    dns = [
        r
        for r in litellm["spec"]["egress"]
        if r["to"][0].get("podSelector", {}).get("matchLabels") == {"k8s-app": "kube-dns"}
    ]
    assert dns and {(p["port"], p["protocol"]) for p in dns[0]["ports"]} == {
        (53, "UDP"),
        (53, "TCP"),
    }
    assert 53 not in {p["port"] for r in _load(POLICY)["spec"]["egress"] for p in r["ports"]}


def test_no_other_pod_gets_internet_egress_from_the_hosted_files() -> None:
    docs = [yaml.safe_load(p.read_text()) for p in HOSTED.glob("*.yaml")]
    policies = [d for d in docs if d and d.get("kind") == "NetworkPolicy"]
    assert [p["metadata"]["name"] for p in policies] == ["litellm-hosted-egress"]


# ---- hosted.sh --------------------------------------------------------------------------------


def test_hosted_sh_is_strict_pins_the_context_and_never_traces() -> None:
    text = HOSTED_SH.read_text()
    code = "\n".join(_code(HOSTED_SH))
    assert "set -euo pipefail" in code and "CONTEXT=kind-poc05" in code
    assert not re.search(r"\bset\s+-\w*x", code) and "xtrace" not in code
    assert "--no-verify" not in code and "--force " not in code
    kubectl = [ln for ln in _code(HOSTED_SH) if re.search(r"(?<![\w-])kubectl\s", ln)]
    allowed = ('kctl() { kubectl --context "$CONTEXT" "$@"; }', "for tool in kubectl")
    assert all(any(a in ln for a in allowed) for ln in kubectl), kubectl
    assert re.search(r"^\s*up\) up ;;", text, re.M)
    assert re.search(r"^\s*run\) run ", text, re.M)
    assert re.search(r"^\s*down\) down ;;", text, re.M)
    assert subprocess.run(["bash", "-n", str(HOSTED_SH)], check=False).returncode == 0
    assert os.access(HOSTED_SH, os.X_OK)


def test_hosted_sh_never_puts_a_key_in_argv_or_the_environment() -> None:
    code = "\n".join(_code(HOSTED_SH))
    assert not re.search(r"\b(export|declare -x)\b", code)
    assert "--from-literal" not in code
    assert not re.search(r"curl[^\n|]*(-H|--header)\s+[\"']?Authorization", code)
    assert not re.search(r"-p\s+[\"']?[^\n]*\$\{?(key|master|resp)\b", code)
    for stmt in _statements(HOSTED_SH):
        if not re.search(r"\$\{?(key|master|resp)\b", stmt):
            continue
        ok = (
            stmt.startswith(("printf", "unset", "[[", "die", "if !", "local"))
            or "| curl" in stmt
            or "| jq" in stmt
            or "| put_secret" in stmt
        )
        assert ok, f"a credential variable outside a pipe: {stmt}"
        if stmt.startswith("printf"):
            assert "|" in stmt, f"printf of a credential not into a pipe: {stmt}"
        assert not re.search(r"\becho\b", stmt), stmt


def test_hosted_sh_reads_the_provider_key_from_stdin_only() -> None:
    code = "\n".join(_code(HOSTED_SH))
    assert "key=$(cat)" in code
    assert "[[ ! -t 0 ]] || die" in code, "a terminal on stdin is refused, not read"
    assert code.count("=/dev/stdin") == 2, "the Secret and the ConfigMap, both from a pipe"
    assert 'printf \'%s\' "$key" | put_secret "$PLATFORM" "$PROVIDER_SECRET" api_key' in code
    # the chassis and master keys reach curl in a config on stdin
    assert code.count("curl -sS -K -") == 1
    assert "Bearer %s" in code and "| curl -sS -K - " in code


def test_hosted_sh_keeps_the_provider_key_in_one_secret_and_one_env() -> None:
    text = HOSTED_SH.read_text()
    assert "PROVIDER_SECRET=litellm-provider" in text and "PROVIDER_ENV=POC06_PROVIDER_KEY" in text
    assert '"secretKeyRef": {"name": "$PROVIDER_SECRET", "key": "api_key"}' in text
    # it patches the litellm container only, and nothing else gets the env
    assert text.count('"containers": [{"name": "litellm"') == 2
    assert "deployment/chassis" not in text.split("patch_litellm()")[1].split("original_config")[0]


def test_hosted_sh_scopes_the_chassis_keys_to_two_routes_and_a_budget() -> None:
    text = HOSTED_SH.read_text()
    assert "ROUTE=big-default" in text and "BASE_ROUTE=fake-chat" in text
    assert "KEY_BUDGET=1.0" in text and "suggested" in text.split("KEY_BUDGET=1.0")[0][-120:]
    assert 'set_all_keys "$BASE_ROUTE" "$ROUTE"' in text
    assert r'\\"max_budget\\":%s' in text
    assert "/key/update" in text and "/key/generate" not in "\n".join(_code(HOSTED_SH))
    # an unsafe key or master value cannot break out of the curl config
    assert "^[A-Za-z0-9_-]+$" in text


def test_hosted_sh_down_undoes_up_and_is_idempotent() -> None:
    text = HOSTED_SH.read_text()
    down = text[text.index("down() {") : text.index("usage() {")]
    assert "--ignore-not-found" in down
    for needle in (
        "delete networkpolicy litellm-hosted-egress",
        'delete configmap "$HOSTED_CM"',
        'delete secret "$PROVIDER_SECRET"',
        "unpatch_litellm",
        '"$BASE_ROUTE"',
    ):
        assert needle in down, needle
    assert "nothing to undo" in down and "return 0" in down, "a missing cluster is not an error"
    assert '"\\$patch": "delete"' in text, "the env is removed with a strategic merge delete"


def test_hosted_sh_edits_no_poc05_file_and_not_run_sh() -> None:
    code = "\n".join(_code(HOSTED_SH))
    assert not re.search(r"(>|sed -i|tee)[^\n]*poc05/", code)
    assert "run.sh" not in code.replace("deploy/kind/poc06/run.sh up", "")


def test_hosted_sh_names_what_the_manifests_name() -> None:
    text = HOSTED_SH.read_text()
    pairs = re.findall(r"^  (echo-[a-z-]+|kagent-adk):([a-z-]+)$", text, re.M)
    assert sorted(engine for engine, _ in pairs) == UNTRUSTED
    seed = SEED_SH.read_text()
    for _, svc in pairs:
        assert f"\n  {svc}\n" in seed, f"seed.sh makes chassis-{svc}-litellm"
        found = [
            p
            for p in list((POC06 / "agents").glob("chassis-*.yaml"))
            + list((POC06 / "kagent").glob("chassis-*.yaml"))
            if f"name: chassis-{svc}\n" in p.read_text()
        ]
        assert found, f"a Deployment chassis-{svc}"
        assert "name: chassis-config" in found[0].read_text()
        assert f"name: chassis-{svc}-litellm" in found[0].read_text()


@pytest.mark.parametrize("svc", ["smolagents-remote", "claude-agent-remote", "kagent-adk-remote"])
def test_each_chassis_config_has_exactly_one_route_line_at_four_spaces(svc: str) -> None:
    path = next((POC06).glob(f"*/chassis/{svc}.yaml"))
    lines = [ln for ln in path.read_text().splitlines() if ln.startswith("    route: ")]
    assert lines == ["    route: fake-chat"]
    assert _load(path)["spec"]["model"]["route"] == "fake-chat"


def test_kagent_adk_names_its_own_model_so_hosted_sh_edits_the_workload_config() -> None:
    manifest = POC06 / "kagent/remote-kagent-adk.yaml"
    docs = [d for d in yaml.safe_load_all(manifest.read_text()) if d]
    cm = next(d for d in docs if d["kind"] == "ConfigMap")
    assert cm["metadata"]["name"] == "remote-kagent-adk-config"
    config = json.loads(cm["data"]["config.json"])
    assert config["model"]["model"] == "fake-chat", "the route is the workload's pick"
    assert config["model"]["api_key_passthrough"] is True
    text = HOSTED_SH.read_text()
    assert "KAGENT_CM=remote-kagent-adk-config" in text and ".model.model = $m" in text
    proxy = (ROOT / "packages/chassis/src/chassis/server/model_proxy.py").read_text()
    assert "route=body.model" in proxy, "the chassis proxy routes by the request's model field"


def test_the_chassis_config_is_read_at_start_so_hosted_sh_restarts_the_chassis() -> None:
    cfg = (ROOT / "packages/chassis/src/chassis/server/config_loader.py").read_text()
    assert "With `spec.adapters.config: memory`" in cfg and "there is no store document" in cfg
    for svc in ("smolagents-remote", "claude-agent-remote", "kagent-adk-remote"):
        config = next((POC06).glob(f"*/chassis/{svc}.yaml"))
        assert _load(config)["spec"]["adapters"]["config"] == "memory"
    assert 'rollout restart "deployment/chassis-$svc"' in HOSTED_SH.read_text()


def test_the_hosted_run_uses_target_mode_with_the_three_tasks() -> None:
    text = HOSTED_SH.read_text()
    assert "-m bakeoff run --target" in text
    assert "--tasks smoke,simplifier,lookup" in text
    assert "REPEAT=${POC06_REPEAT:-5}" in text and '--repeat "$REPEAT"' in text
    assert "scrub_file" in text and "| scrub" in text
    cli = (ROOT / "packages/bakeoff/src/bakeoff/cli.py").read_text()
    assert "--target" in cli and "--tasks" in cli and "--repeat" in cli


# ---- the Mac script's kind step ---------------------------------------------------------------


def _clean_bin(tmp_path: Path) -> Path:
    """A PATH with the shell tools the script needs and no docker, kind, or kubectl."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    for tool in ("sed", "tr", "cat", "git", "grep", "dirname", "awk", "date", "head", "tail",
                 "uname", "mktemp", "rm", "basename", "sort", "wc", "cut", "env"):  # fmt: skip
        found = shutil.which(tool)
        if found:
            (bindir / tool).symlink_to(found)
    return bindir


def _mac(tmp_path: Path, *args: str, path: str | None = None) -> subprocess.CompletedProcess[str]:
    env = {"PATH": path or os.environ["PATH"], "HOME": str(tmp_path / "home")}
    (tmp_path / "home").mkdir(exist_ok=True)
    return subprocess.run(
        [shutil.which("bash") or "bash", str(MAC_SH), *args],
        env=env,
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def test_the_dry_run_of_the_kind_step_prints_its_commands_and_needs_no_docker(
    tmp_path: Path,
) -> None:
    bindir = _clean_bin(tmp_path)
    assert shutil.which("docker", path=str(bindir)) is None
    done = _mac(tmp_path, "--dry-run", "--only", "kind", path=str(bindir))
    assert done.returncode == 0, done.stdout + done.stderr
    out = done.stdout
    for needle in (
        "== step kind",
        "deploy/kind/poc06/run.sh up",
        "deploy/kind/poc06/hosted.sh up",
        "deploy/kind/poc06/hosted.sh run",
        "deploy/kind/poc06/hosted.sh down",
        "deploy/kind/poc06/run.sh delete",
        "echo-smolagents echo-claude-agent kagent-adk",
        "command -v kind kubectl jq",
        "no kind cluster named poc05",
        "-kind-run.md",
        "pocs/poc-06b-bake-off-remote-lane/notes/",
    ):
        assert needle in out, needle
    assert "== step hosted" not in out and "docker compose" not in out
    assert "== TypeScript build" not in out
    assert not list((tmp_path / "home").iterdir()), "a dry run writes nothing"


def test_the_default_order_puts_kind_last_and_a_dry_push_names_the_notes(tmp_path: Path) -> None:
    done = _mac(tmp_path, "--dry-run", "--push")
    assert done.returncode == 0, done.stdout + done.stderr
    assert "steps: hosted slm scale load kind |" in done.stdout
    steps = re.findall(r"^== step (\w+):", done.stdout, re.M)
    assert steps == ["hosted", "slm", "scale", "load", "kind"]
    assert "exactly the note files" in done.stdout


def test_the_dry_run_never_shows_a_key(tmp_path: Path) -> None:
    secret = "dry-run-secret-value-xyz"  # pragma: allowlist secret
    env = {"PATH": os.environ["PATH"], "HOME": str(tmp_path), "OPENAI_API_KEY": secret}
    done = subprocess.run(
        ["bash", str(MAC_SH), "--dry-run", "--only", "kind"],
        env=env,
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert done.returncode == 0
    assert secret not in done.stdout + done.stderr
    assert "not shown" in done.stdout


@pytest.mark.parametrize("engine", UNTRUSTED)
@pytest.mark.parametrize("only", ["hosted", "slm", "kind"])
def test_untrusted_engines_are_still_refused_outside_the_kind_step(
    tmp_path: Path, engine: str, only: str
) -> None:
    done = _mac(tmp_path, "--dry-run", "--only", only, "--engine", engine)
    assert done.returncode == 2, done.stdout
    assert f"refusing {engine}" in done.stderr
    assert "DRY RUN" not in done.stdout


def test_the_kind_step_is_the_only_one_that_names_the_untrusted_engines() -> None:
    text = MAC_SH.read_text()
    step = text[text.index("step_kind() {") : text.index("# ---- push")]
    assert "$UNTRUSTED_ENGINES" in step
    others = text.replace(step, "")
    body = others[others.index("step_hosted() {") : others.index("# ---- push")]
    assert "UNTRUSTED_ENGINES" not in body
    assert "bakeoff_run" not in step, "the kind step uses hosted.sh run, not the host bake-off"


def test_the_kind_step_tears_down_on_any_exit_and_refuses_an_existing_cluster() -> None:
    text = MAC_SH.read_text()
    stop = text[text.index("stop_kind() {") : text.index("cleanup() {")]
    assert stop.index('"$KIND_HOSTED" down') < stop.index('"$KIND_RUN" delete')
    cleanup = text[text.index("cleanup() {") : text.index("trap cleanup EXIT")]
    assert "stop_kind" in cleanup
    assert "trap cleanup EXIT" in text and "trap 'exit 143' TERM HUP" in text
    step = text[text.index("step_kind() {") : text.index("# ---- push")]
    assert step.index("KIND_UP=1") < step.index('"$KIND_RUN" up'), "a partial up is deleted too"
    assert "kind get clusters" in text and 'grep -qx "$KIND_CLUSTER"' in text
    assert "KIND_MIN_MIB=${POC06_KIND_MIN_MIB:-6000}" in text and "suggested" in text


def test_security_md_states_the_kind_rules() -> None:
    text = (ROOT / "deploy/compose/SECURITY.md").read_text()
    for needle in (
        "PoC-6 kind step",
        "only in the Secret `litellm-provider`",
        "only pod with internet egress, and only TCP 443",
        "two routes, `fake-chat` and `big-default`",
        "Workloads never see a provider key",
    ):
        assert needle in text, needle


# ---- the relay --------------------------------------------------------------------------------


def _load_relay() -> Any:
    import importlib.util

    spec = importlib.util.spec_from_file_location("poc06_hosted_relay", RELAY)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_relay_command_pins_the_context_and_carries_no_secret() -> None:
    relay = _load_relay()
    cmd = relay.exec_command("chassis-smolagents-remote")
    assert cmd[:3] == ["kubectl", "--context", "kind-poc05"]
    assert "deploy/chassis-smolagents-remote" in cmd and "poc05-agents" in cmd
    joined = " ".join(cmd)
    assert not re.search(r"(KEY|TOKEN|SECRET|PASSWORD|Bearer|sk-)", joined)
    compile(relay.POD_SIDE, "<pod side>", "exec")
    assert relay.main(["relay.py"]) == 2
    assert relay.main(["relay.py", "chassis; rm -rf /"]) == 2


E2E = textwrap.dedent(
    """
    import http.server, json, os, subprocess, sys, threading, urllib.request
    relay, bindir, port = sys.argv[1], sys.argv[2], sys.argv[3]

    class H(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            out = json.dumps({"echo": json.loads(body), "big": "x" * 200000}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)
        def log_message(self, *a):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", int(port)), H)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    env = {"PATH": bindir + ":" + os.environ["PATH"], "TEST_PORT": port}
    proc = subprocess.Popen([sys.executable, relay, "chassis-x"], env=env,
                            stdout=subprocess.PIPE, text=True)
    try:
        local = proc.stdout.readline().split()[1]
        for n in (1, 2):
            req = urllib.request.Request(
                "http://127.0.0.1:" + local + "/v1/run",
                json.dumps({"n": n}).encode(),
                {"Content-Type": "application/json"},
            )
            got = json.load(urllib.request.urlopen(req, timeout=20))
            assert got["echo"] == {"n": n} and len(got["big"]) == 200000
        print("relay ok")
    finally:
        proc.kill()
"""
)

FAKE_KUBECTL = """#!/bin/bash
# stands in for `kubectl --context C -n N exec -i deploy/X -c chassis -- python -c CODE`
while [ "$1" != "--" ]; do shift; done
shift 3
code=${1//8080/$TEST_PORT}
exec env POD_IP=127.0.0.1 PYTHON_BIN python3 -c "$code"
"""


def test_the_relay_carries_http_both_ways_through_a_fake_kubectl(tmp_path: Path) -> None:
    bindir = tmp_path / "bin"
    bindir.mkdir()
    fake = bindir / "kubectl"
    fake.write_text(FAKE_KUBECTL.replace("PYTHON_BIN python3", f"{sys.executable}"))
    fake.chmod(0o755)
    port = "18943"  # a loopback test port; the whole scenario runs in a child process
    done = subprocess.run(
        [sys.executable, "-I", "-c", E2E, str(RELAY), str(bindir), port],
        capture_output=True,
        text=True,
        timeout=90,
        check=False,
    )
    assert done.returncode == 0, done.stdout + done.stderr
    assert "relay ok" in done.stdout


def test_down_reverts_egress_and_keys_before_the_route_restores() -> None:
    """A teardown cut short must leave no provider key and no egress behind (security review)."""
    text = HOSTED_SH.read_text()
    body = text[text.index("down() {\n") :]
    body = body[: body.index("\n}\n")]
    order = [
        body.index("delete networkpolicy litellm-hosted-egress"),
        body.index('delete secret "$PROVIDER_SECRET"'),
        body.index('set_all_keys "$BASE_ROUTE"'),
        body.index("set_chassis_route"),
        body.index('delete configmap "$HOSTED_CM"'),
    ]
    assert order == sorted(order), order
