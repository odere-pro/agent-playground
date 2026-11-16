"""The offline twin of `test_poc09_kind_ch4.py` (026 CH-4): what the kind run depends on, checked
with no cluster. The seed gives every PoC-5 key a duration; `rekey` keeps the old key live until
the pods roll, then revokes it and proves it dead with the held key; `revoke` does the same; the
drill's Valkey users get an allow-list and are added without touching the PoC-5 password; the two
PoC-5 Deployments roll with no gap; `run.sh test-ch4` runs the kind file.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]
SEED = ROOT / "deploy/kind/poc05/platform/seed.sh"
RUN_SH = ROOT / "deploy/kind/poc05/run.sh"
AGENTS = ROOT / "deploy/kind/poc05/agents"
KIND_TEST = Path(__file__).with_name("test_poc09_kind_ch4.py")


def _function(path: Path, name: str) -> str:
    match = re.search(rf"(?ms)^{name}\(\) \{{\n(.*?)^\}}", path.read_text())
    assert match, f"{path.name} has no {name}()"
    return match.group(1)


def _in_order(body: str, *steps: str) -> None:
    found = [body.find(step) for step in steps]
    assert -1 not in found, dict(zip(steps, found, strict=True))
    assert found == sorted(found), dict(zip(steps, found, strict=True))


def test_every_key_the_seed_mints_has_a_duration() -> None:
    text = SEED.read_text()
    assert re.search(r"(?m)^KEY_DURATION=30d$", text)
    body = _function(SEED, "key_body")
    assert '--arg duration "$KEY_DURATION"' in body
    assert "duration: $duration" in body


def test_rekey_holds_the_old_key_and_revokes_it_only_after_the_pods_rolled() -> None:
    """Read the old key, move it to the retiring alias with a short duration, mint, roll, delete,
    then the held old key must get 401 and the new key 200. Deleting before the roll would cut
    the service off; checking only the key list would miss LiteLLM's key cache."""
    body = _function(SEED, "rekey_one")
    _in_order(
        body,
        'old=$(read_secret "$ns" "chassis-$svc-litellm" LITELLM_API_KEY)',
        "key_alias: $alias, duration: $duration",
        'make_keys "$1"',
        "restart_consumers",
        'delete_alias "$retiring"',
        """printf '%s' "$old" | require_status 401 "rekey: svc-$svc old key\"""",
        'require_status 200 "rekey: svc-$svc new key"',
        'log "rekey: svc-$svc old key revoked ($retiring deleted), new key answers (HTTP 200)"',
    )
    assert 'die "a key under $retiring is left' in body, "a half-done rekey must stop the next"
    assert "could not be moved" in body, "a live key that did not move must stop the rekey"
    # The Secret is overwritten by a server-side apply, never deleted first.
    assert "drop_secret" not in body
    assert re.search(r"(?m)^RETIRE_DURATION=1h$", SEED.read_text())
    assert '--arg duration "$RETIRE_DURATION"' in body


def test_rekey_is_limited_to_the_poc05_services() -> None:
    body = _function(SEED, "rekey")
    assert '[[ ${entry%%|*} == "$AGENTS" ]] || continue' in body
    assert 'rekey_one "$entry"' in body


def test_revoke_reads_the_key_before_deleting_it_and_a_second_revoke_exits_0() -> None:
    body = _function(SEED, "revoke")
    _in_order(
        body,
        "already revoked",
        'old=$(read_secret "$ns" "chassis-$svc-litellm" LITELLM_API_KEY)',
        'delete_alias "svc-$svc"\n  unset master\n  printf',
        "require_status 401",
        'drop_secret "$ns" "chassis-$svc-litellm"',
    )


def test_a_status_check_refuses_an_empty_key() -> None:
    body = _function(SEED, "require_status")
    assert "[[ -n $key ]] || die" in body
    assert "-H @-" in body and "/v1/models" in body


def test_key_info_prints_times_only_per_alias_and_its_retiring_alias() -> None:
    body = _function(SEED, "key_info")
    assert 'for alias in "svc-$svc" "svc-$svc-retiring"; do' in body
    assert "{alias: $alias, keys: [.keys[] | {created_at, expires}]}" in body


def test_the_pods_roll_with_no_gap_and_a_failed_roll_keeps_the_old_key() -> None:
    body = _function(SEED, "restart_consumers")
    assert "rollout restart" in body and "rollout status" in body
    assert "the old key is still live" in body
    for name in ("agent-echo.yaml", "chassis-echo-remote.yaml"):
        (deployment,) = [
            d for d in yaml.safe_load_all((AGENTS / name).read_text()) if d["kind"] == "Deployment"
        ]
        assert deployment["spec"]["strategy"] == {
            "type": "RollingUpdate",
            "rollingUpdate": {"maxUnavailable": 0, "maxSurge": 1},
        }, name


def test_a_key_in_a_request_body_goes_through_stdin_never_argv() -> None:
    body = _function(SEED, "admin_update")
    assert "curl " in body and "-K -" in body
    assert re.search(r"printf '[^']*Authorization: Bearer %s[^']*' \"\$master\"", body)


def test_the_drill_valkey_users_get_an_allow_list_and_never_change_the_poc05_password() -> None:
    """platform-security, 2026-11-16: no +@all for a drill user; FUNCTION, SCRIPT FLUSH/KILL, and
    DBSIZE are not in the list. Reviewer, risk (a): an existing password is read back, never made
    again, so adding the drill users does not rotate the PoC-5 chassis password."""
    text = SEED.read_text()
    acl = re.search(r'(?m)^SERVICE_ACL="([^"]*)"$', text)
    assert acl, "no SERVICE_ACL"
    rules = acl.group(1).split()
    assert rules == ["resetchannels", "-@all", "+get", "+set", "+del", "+evalsha",
                     "+script|load", "+ping"]  # fmt: skip
    assert not [r for r in rules if r.startswith("+@")]
    body = _function(SEED, "valkey_password")
    _in_order(body, 'if ! secret_exists "$1" "$2"; then', "new_secret", 'read_secret "$1" "$2"')
    acl_body = _function(SEED, "valkey_acl")
    assert 'valkey_password "$AGENTS" valkey-auth | acl_user chassis' in acl_body
    assert "new_secret" not in acl_body


def test_valkey_is_at_least_8_1_4() -> None:
    """platform-security, 2026-11-16: the drill users may run Lua (EVALSHA), so Valkey must carry
    the 8.1.4 fixes for the Lua CVEs."""
    text = (ROOT / "deploy/kind/poc05/platform/valkey.yaml").read_text()
    found = re.search(r"image: valkey/valkey:(\d+)\.(\d+)\.(\d+)-alpine@sha256:[0-9a-f]{64}", text)
    assert found, "valkey is not pinned by version and digest"
    assert tuple(int(n) for n in found.groups()) >= (8, 1, 4), found.group(0)


def test_run_sh_test_ch4_runs_the_kind_file() -> None:
    body = _function(RUN_SH, "run_tests_ch4")
    # Short tracebacks: a long one prints every frame's arguments (iteration, 2026-11-16).
    assert "--tb=short" in body
    assert "KIND_DRILLS=1" in body
    assert "pocs/poc-09-agent-mvp-template/tests/test_poc09_kind_ch4.py" in body
    assert re.search(r"(?m)^\s+test-ch4\) run_tests_ch4 ;;$", RUN_SH.read_text())


def test_the_kind_file_is_a_drill_and_checks_its_output_for_keys() -> None:
    text = KIND_TEST.read_text()
    assert "pytestmark = KIND_SANDBOX" in text
    assert "assert leaks(output) == []" in text
    assert 'f"rekey: svc-{svc} old key gets HTTP 401"' in text
