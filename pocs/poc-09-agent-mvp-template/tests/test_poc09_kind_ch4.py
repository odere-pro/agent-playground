"""Backlog 026 CH-4 on kind: the LiteLLM service key has a duration, and `seed.sh rekey` mints a
new key and revokes the old one while the service keeps answering. On the PoC-5 cluster `poc05`.

The two PoC-5 services, `agent-echo` (sidecar) and `chassis-echo-remote` (remote), each hold a
LiteLLM virtual key (Secret `chassis-<svc>-litellm`, alias `svc-<svc>`). While a caller pod sends
`/v1/run` to both Services in a loop, `seed.sh rekey` runs: per service it moves the live key to
the alias `svc-<svc>-retiring`, mints a new key under `svc-<svc>`, rolls the Deployments that read
the Secret (maxUnavailable 0, so an old pod answers until its new one is Ready), then deletes the
retiring key and checks the new one answers. Each `/v1/run` calls the model through LiteLLM with
the service's key, so a dead key shows as a failed run.

Checks: every run in the loop answered 200, the loop spans the whole rekey; `seed.sh key-info`
shows one key per alias before and after, a new `created_at` after, an `expires` about
`KEY_DURATION` after creation, and nothing left under the retiring alias. `seed.sh rekey` logs
that each old key was revoked and the new key answers. Names, statuses, and times only: no key.

The caller is a pod in `agent-platform-system` with the gateway labels (`gateway_caller`): its
egress is every chassis's 8080 and nothing else.

The drill services `ch4-a` and `ch4-b` (seed.sh CHART_SERVICES) hold their own keys and Valkey
users, made by `run.sh up`. Their tests use the credentials from this host through port-forwards
(`kind_ch4`): one service's key cannot call the other's tool, one Valkey user cannot touch the
other's prefix or run FUNCTION FLUSH or DBSIZE, `seed.sh revoke-grants` leaves no tool (the
gateway refuses the call, not just the list), and `seed.sh revoke` gets the held key a 401 while
the other service keeps working. They put ch4-a back with `seed.sh keys` after.

A kind test: marked `network`, skipped unless `KIND_DRILLS=1`. Run:
`deploy/kind/poc05/run.sh test-ch4` after `run.sh up`. Every kubectl call pins `--context
kind-poc05`.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import threading
import time
from datetime import datetime, timedelta
from typing import Any

import pytest
from chassis.adapters.mcp.gateway import McpGatewayTools
from chassis.ports.tool import ToolDefinition, ToolError
from kind_ch4 import (
    CH4,
    CHART_NS,
    Cred,
    forward,
    key_info,
    leaks,
    models_status,
    script_sha,
    secret_value,
    seed,
    valkey,
)
from poc_harness.drills import KIND_SANDBOX
from poc_harness.kind import AGENTS_NS, CONTEXT, GATEWAY_NS, gateway_caller, service_ip

pytestmark = KIND_SANDBOX

SERVICES = {"echo": "agent-echo", "echo-remote": "chassis-echo-remote"}
# seed.sh KEY_DURATION (suggested: 30d).
KEY_DURATION = timedelta(days=30)
DURATION_SLACK = timedelta(minutes=10)
REKEY_TIMEOUT_S = 900
# suggested: the loop runs this long before and after the rekey, so both ends are covered.
EDGE_S = 10.0
# suggested: at least this many runs while the rekey runs (two rollouts on a busy Mac).
MIN_RUNS = 20

# One /v1/run every 0.5 s, alternating the two Services, one JSON line per run, until killed.
LOOP_PY = r"""
import json, sys, time, urllib.error, urllib.request
targets = json.loads(sys.argv[1])
body = json.dumps({"input": {"text": "hello"}}).encode()
i = 0
while True:
    name, url = targets[i % len(targets)]
    i += 1
    req = urllib.request.Request(url, body, {"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            code = r.status
    except urllib.error.HTTPError as e:
        code = e.code
    except Exception as e:
        code = type(e).__name__
    print(json.dumps({"t": time.time(), "svc": name, "status": code}), flush=True)
    time.sleep(0.5)
"""


def _when(stamp: str) -> datetime:
    return datetime.fromisoformat(stamp.replace("Z", "+00:00"))


class Loop:
    """`LOOP_PY` in the caller pod; its result lines, read as they come."""

    def __init__(self, caller: str, targets: list[tuple[str, str]]) -> None:
        exe = shutil.which("kubectl")
        assert exe is not None, "kubectl is not on PATH"
        self.results: list[dict[str, Any]] = []
        self.proc = subprocess.Popen(
            [exe, "--context", CONTEXT, "exec", "-n", GATEWAY_NS, caller, "-c", "caller", "--",
             "python", "-u", "-c", LOOP_PY, json.dumps(targets)],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
        )  # fmt: skip
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()

    def _read(self) -> None:
        assert self.proc.stdout is not None
        for line in self.proc.stdout:
            if line.startswith("{"):
                self.results.append(json.loads(line))

    def stop(self) -> list[dict[str, Any]]:
        self.proc.terminate()
        self.proc.wait(timeout=30)
        self.reader.join(timeout=10)
        return list(self.results)


def test_rekey_mints_a_new_key_with_a_duration_and_revokes_the_old_while_the_service_answers() -> (
    None
):
    """026 CH-4: "The LiteLLM service key has a `duration`, and a `seed.sh rekey` mints a new key
    and revokes the old one while the service keeps answering, on kind."
    """
    before = key_info()
    for svc in SERVICES:
        assert len(before[f"svc-{svc}"]) == 1, before
        assert before[f"svc-{svc}-retiring"] == [], before
    targets = [
        (name, f"http://{service_ip(AGENTS_NS, name)}:8080/v1/run") for name in SERVICES.values()
    ]
    with gateway_caller() as caller:
        loop = Loop(caller, targets)
        time.sleep(EDGE_S)
        start = time.time()
        rekey = seed("rekey", timeout=REKEY_TIMEOUT_S)
        end = time.time()
        time.sleep(EDGE_S)
        runs = loop.stop()
    output = rekey.stdout + rekey.stderr
    print(f"\nrekey exit {rekey.returncode} in {end - start:.0f} s")
    print("\n".join(line for line in output.splitlines() if "rekey" in line))
    assert leaks(output) == []
    assert rekey.returncode == 0, output[-800:]
    for svc in SERVICES:
        # The held old key, after the delete: LiteLLM refuses it (its cache included).
        assert f"rekey: svc-{svc} old key gets HTTP 401" in output, output[-800:]
        assert f"rekey: svc-{svc} new key gets HTTP 200" in output, output[-800:]
        assert (
            f"svc-{svc} old key revoked (svc-{svc}-retiring deleted), new key answers (HTTP 200)"
            in output
        )

    during = [r for r in runs if start <= r["t"] <= end]
    failed = [r for r in runs if r["status"] != 200]
    by_svc = {name: sum(1 for r in during if r["svc"] == name) for name in SERVICES.values()}
    print(f"runs: {len(runs)} in all, {len(during)} during the rekey {by_svc}, failed: {failed}")
    assert runs and runs[0]["t"] < start and runs[-1]["t"] > end, "the loop does not span the rekey"
    assert len(during) >= MIN_RUNS and all(by_svc.values()), by_svc
    assert failed == []

    after = key_info()
    for svc in SERVICES:
        (new,) = after[f"svc-{svc}"]
        (old,) = before[f"svc-{svc}"]
        assert after[f"svc-{svc}-retiring"] == [], after
        assert new["created_at"] != old["created_at"], "no new key was minted"
        assert new["expires"], f"svc-{svc}: the new key has no expiry"
        lifetime = _when(new["expires"]) - _when(new["created_at"])
        print(f"svc-{svc}: created {new['created_at']}, expires {new['expires']} ({lifetime})")
        assert abs(lifetime - KEY_DURATION) <= DURATION_SLACK, lifetime


# --- the drill services: one service's credentials, the other's tools and cache keys ----------

GATEWAY = "fake_tools"
# LiteLLM caches a key's object (its tool grants included) in memory: a /key/update shows within
# about 60 s (measured on poc05, 2026-11-16: 62 s). suggested: wait up to twice that.
GRANT_CACHE_S = 130.0
GRANT_POLL_S = 5.0


def _tool(name: str) -> str:
    return f"{GATEWAY}-{name}"


def _listed(name: str) -> ToolDefinition:
    """A read-only definition put in an adapter's cache, so a call really reaches the gateway
    (a write tool would stop in the adapter first, for its idempotency key)."""
    return ToolDefinition(name=name, description=name, read_only=True)


async def _gateway_tools(port: int, key: Cred) -> McpGatewayTools:
    tools = McpGatewayTools(
        f"http://127.0.0.1:{port}/mcp/", key.reveal(), auth_header="Authorization"
    )
    await tools.refresh()
    return tools


async def _listed_names(port: int, key: Cred) -> list[str]:
    tools = await _gateway_tools(port, key)
    try:
        return sorted(d.name for d in tools.list_tools())
    finally:
        await tools.aclose()


async def _call(port: int, key: Cred, name: str) -> tuple[bool, str]:
    """Call `name` through the gateway, listed or not: (is_error, "") or (True, its error code)."""
    tools = await _gateway_tools(port, key)
    if _tool(name) not in tools._tools:  # a listed tool keeps its own definition
        tools._tools = {**tools._tools, _tool(name): _listed(_tool(name))}
    try:
        result = await tools.call(_tool(name), ARGS[name], idempotency_key=f"ch4-{name}")
        return result.is_error, ""
    except ToolError as e:
        return True, e.code
    finally:
        await tools.aclose()


ARGS = {"glossary_lookup": {"term": "SLM"}, "note_write": {"text": "ch4 drill"}}


@pytest.fixture(scope="module")
def ch4_keys() -> dict[str, Cred]:
    """Both drill services have their key (`seed.sh keys` makes a missing one); each key, in
    memory only, as a `Cred` (its repr shows no value)."""
    made = seed("keys")
    assert made.returncode == 0 and leaks(made.stdout + made.stderr) == [], made.stderr[-400:]
    return {svc: secret_value(CHART_NS, f"chassis-{svc}-litellm", "LITELLM_API_KEY") for svc in CH4}


async def test_one_services_key_cannot_call_the_other_services_tool(
    ch4_keys: dict[str, Cred],
) -> None:
    """026 CH-4, the tools part of "One service's credentials cannot use another service's models,
    tools, or cache keys." Each key lists its own tool only; the other's tool, sent to the gateway
    anyway, is refused there. The control: its own tool answers."""
    with forward("litellm", 4000) as port:
        for svc, own in CH4.items():
            other = next(t for s, t in CH4.items() if s != svc)
            listed = await _listed_names(port, ch4_keys[svc])
            own_call = await _call(port, ch4_keys[svc], own)
            other_call = await _call(port, ch4_keys[svc], other)
            print(f"\n{svc}: lists {listed}; own {own}: {own_call}; {other}: {other_call}")
            assert listed == [_tool(own)]
            assert own_call == (False, "")
            assert other_call == (True, "tool_denied")


def _scripts(own: str, other: str) -> dict[str, str]:
    return {
        # The chassis's shape: compare-and-set on a key it names (the positive control).
        "cas": "if redis.call('GET', KEYS[1]) == ARGV[1] then "
        "return redis.call('SET', KEYS[1], ARGV[2]) end return 0",
        "get_named": f"return redis.call('GET', '{other}:x')",
        "flushall": "return redis.call('FLUSHALL')",
    }


def test_one_services_valkey_user_cannot_touch_the_other_prefix_or_run_admin_commands() -> None:
    """026 CH-4, the cache-keys part, and the ACL allow-list (platform-security, 2026-11-16). Each
    Valkey user writes its own prefix and runs a compare-and-set script on it (the controls). It
    gets NOPERM on: the other's prefix, FUNCTION FLUSH, DBSIZE, SCRIPT FLUSH, SCRIPT KILL, EVAL,
    its own loaded script pointed at the other's key, and loaded scripts that name the other's key
    or call FLUSHALL inside Lua. A refusal inside Lua comes back wrapped: on Valkey 8.1.4 it is
    `-ERR ACL failure in script: No permissions ...`, with no NOPERM word (seen on poc05,
    2026-11-16), so a refusal is NOPERM or that wrapped ACL failure."""
    with forward("valkey", 6379) as port:
        for svc in CH4:
            other = next(s for s in CH4 if s != svc)
            scripts = _scripts(svc, other)
            sha = {name: script_sha(text) for name, text in scripts.items()}
            password = secret_value(CHART_NS, f"chassis-{svc}-valkey", "VALKEY_PASSWORD")
            steps = [
                ("own set", ["SET", f"{svc}:x", "1"]),
                ("load cas", ["SCRIPT", "LOAD", scripts["cas"]]),
                ("cas own", ["EVALSHA", sha["cas"], "1", f"{svc}:x", "1", "2"]),
                ("own get", ["GET", f"{svc}:x"]),
                ("other get", ["GET", f"{other}:x"]),
                ("function flush", ["FUNCTION", "FLUSH"]),
                ("dbsize", ["DBSIZE"]),
                ("script flush", ["SCRIPT", "FLUSH"]),
                ("script kill", ["SCRIPT", "KILL"]),
                ("eval", ["EVAL", "return 1", "0"]),
                ("cas other", ["EVALSHA", sha["cas"], "1", f"{other}:x", "1", "2"]),
                ("load get_named", ["SCRIPT", "LOAD", scripts["get_named"]]),
                ("get_named", ["EVALSHA", sha["get_named"], "0"]),
                ("load flushall", ["SCRIPT", "LOAD", scripts["flushall"]]),
                ("flushall", ["EVALSHA", sha["flushall"], "0"]),
            ]
            replies = valkey(port, svc, password, [cmd for _, cmd in steps])
            got = dict(zip(["auth", *[name for name, _ in steps]], replies, strict=True))
            print(f"\n{svc}: " + "; ".join(f"{k}={v[:70]}" for k, v in got.items()))
            assert got["auth"] == "+OK" and got["own set"] == "+OK"
            assert got["load cas"] == sha["cas"] and got["cas own"] == "+OK"
            assert got["own get"] == "2", "compare-and-set did not write"
            assert got["load get_named"] == sha["get_named"]
            assert got["load flushall"] == sha["flushall"]
            denied = ["other get", "function flush", "dbsize", "script flush", "script kill",
                      "eval", "cas other", "get_named", "flushall"]  # fmt: skip
            for name in denied:
                refused = "NOPERM" in got[name] or got[name].startswith(
                    "-ERR ACL failure in script"
                )
                assert refused, (name, got[name])


async def _wait_for_grants(port: int, key: Cred, want: list[str]) -> float:
    """Seconds until `key` lists `want`; LiteLLM's key cache lags a /key/update."""
    start = time.monotonic()
    listed: list[str] = []
    while time.monotonic() - start < GRANT_CACHE_S:
        listed = await _listed_names(port, key)
        if listed == want:
            return time.monotonic() - start
        await asyncio.sleep(GRANT_POLL_S)
    pytest.fail(f"still lists {listed} {GRANT_CACHE_S:.0f} s after the update, want {want}")


async def test_revoking_one_services_grants_and_key_cuts_it_off_and_the_other_keeps_working(
    ch4_keys: dict[str, Cred],
) -> None:
    """026 CH-4: revoke-grants then revoke on ch4-a. After the grants go (and LiteLLM's key cache
    has caught up), ch4-a lists no tool and the gateway refuses its own tool, so an empty grant
    does not fail open; its key still answers. After the key goes, the held key gets 401. A second
    revoke exits 0. ch4-b keeps its tool and its key throughout (the control). The network label
    is not part of this test."""
    key_a, key_b = ch4_keys["ch4-a"], ch4_keys["ch4-b"]
    try:
        with forward("litellm", 4000) as port:
            assert await _listed_names(port, key_a) == [_tool("glossary_lookup")]
            grants = seed("revoke-grants", "ch4-a")
            assert grants.returncode == 0, grants.stderr[-400:]
            lag = await _wait_for_grants(port, key_a, [])
            call = await _call(port, key_a, "glossary_lookup")
            print(f"\nch4-a after revoke-grants: no tool listed after {lag:.0f} s; call: {call}")
            assert call[0] is True and call[1] in {"tool_denied", "unknown_tool"}, call
            assert models_status(port, key_a) == 200, "revoke-grants must leave the key"

            first = seed("revoke", "ch4-a")
            again = seed("revoke", "ch4-a")
            out = first.stdout + first.stderr + again.stdout + again.stderr
            print("\n".join(line for line in out.splitlines() if "revok" in line))
            assert leaks(out) == []
            assert first.returncode == 0, out[-600:]
            assert "svc-ch4-a deleted key gets HTTP 401" in out, out[-600:]
            assert again.returncode == 0 and "svc-ch4-a is already revoked" in out, out[-600:]
            assert models_status(port, key_a) == 401

            assert await _listed_names(port, key_b) == [_tool("note_write")]
            assert await _call(port, key_b, "note_write") == (False, "")
            assert models_status(port, key_b) == 200
    finally:
        restored = seed("keys")
        assert restored.returncode == 0, restored.stderr[-400:]
