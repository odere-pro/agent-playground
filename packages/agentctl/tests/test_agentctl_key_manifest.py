"""059 H-15 box 7: `agentctl new --key-manifest PATH` reads the key's routes from the manifest the
deploy seed writes (`write_key_manifest` in `deploy/kind/lib.sh`), and refuses a generated route
the key does not list. The manifest names routes only, never the key."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
from agentctl import manifest
from agentctl.cli import main

REPO = Path(__file__).resolve().parents[3]
LIB = REPO / "deploy/kind/lib.sh"
SEEDS = (REPO / "deploy/kind/poc05/platform/seed.sh", REPO / "deploy/kind/poc06/seed.sh")
JQ_PATH = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"


def _new(dest: Path, platform: Path, *args: str) -> int:
    return main(["new", *args, "--dest", str(dest), "--platform-path", str(platform), "--no-lock"])


def _manifest(folder: Path, body: Any, name: str = "svc-reader.json") -> Path:
    path = folder / name
    path.write_text(body if isinstance(body, str) else json.dumps(body))
    return path


def _good(routes: list[str]) -> dict[str, Any]:
    return {
        "version": 1,
        "key_alias": "svc-reader",
        "routes": routes,
        "written_at": "2026-11-14T10:00:00Z",
    }


@pytest.fixture
def dest(tmp_path: Path) -> Path:
    out = tmp_path / "out"
    out.mkdir()
    return out


def test_a_manifest_that_lists_the_generated_route_passes(
    tmp_path: Path, dest: Path, platform: Path
) -> None:
    path = _manifest(tmp_path, _good(["local-small", "big-default"]))

    assert _new(dest, platform, "reader", "--kind", "tool", "--key-manifest", str(path)) == 0
    assert (dest / "reader/config/agent.yaml").is_file()


def test_a_generated_route_the_manifest_does_not_list_is_refused(
    tmp_path: Path, dest: Path, platform: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = _manifest(tmp_path, _good(["fake-chat"]))

    assert _new(dest, platform, "reader", "--kind", "tool", "--key-manifest", str(path)) == 2
    err = capsys.readouterr().err
    assert err.startswith("agentctl: ")
    assert "'big-default'" in err and "svc-reader" in err and str(path) in err
    assert "fake-chat" not in err, "the file's routes are never echoed"
    assert list(dest.iterdir()) == [], "nothing is written"


@pytest.mark.parametrize(
    "body",
    [
        _good(["sk-x"]),
        _good(["fake-chat", "sk-x"]),
        {**_good(["big-default"]), "key_alias": "sk-x"},
        {**_good(["big-default"]), "key_alias": "svc-sk-x\n"},
        _good(["big-default", "sk-x\u001b[0m"]),
        _good(["big-default", "a" * 257]),
        _good(["big-default", "has space"]),
        {**_good(["big-default"]), "sk-x": 1},
    ],
    ids=[
        "route-only",
        "route-among-others",
        "alias",
        "alias-newline",
        "route-escape",
        "route-too-long",
        "route-space",
        "field-name",
    ],
)
def test_a_value_from_the_manifest_is_never_echoed(
    tmp_path: Path,
    dest: Path,
    platform: Path,
    capsys: pytest.CaptureFixture[str],
    body: dict[str, Any],
) -> None:
    """Security review (2026-11-14): a key pasted into the manifest is refused or matched, and
    never printed; the alias and routes are checked against strict patterns before any use."""
    path = _manifest(tmp_path, body)

    assert _new(dest, platform, "reader", "--kind", "tool", "--key-manifest", str(path)) == 2
    out = capsys.readouterr()
    assert "sk-x" not in out.out and "sk-x" not in out.err
    assert list(dest.iterdir()) == []


@pytest.mark.parametrize(
    ("body", "said"),
    [
        ("{not json", "not JSON"),
        ("[]", "one JSON object"),
        ({**_good(["big-default"]), "version": 2}, "version"),
        ({**_good(["big-default"]), "version": True}, "version"),
        ({**_good(["big-default"]), "version": 1.0}, "version"),
        ({k: v for k, v in _good(["big-default"]).items() if k != "routes"}, "routes"),
        (_good([]), "routes"),
        (_good(["big-default", ""]), "routes"),
        (_good(["big-default", 3]), "routes"),
        ({**_good(["big-default"]), "key_alias": ""}, "key_alias"),
        ({**_good(["big-default"]), "written_at": 5}, "written_at"),
        ({**_good(["big-default"]), "key": "sk-x"}, "never the key"),  # pragma: allowlist secret
    ],
    ids=[
        "not-json",
        "not-object",
        "bad-version",
        "version-true",
        "version-float",
        "no-routes",
        "empty-routes",
        "empty-route",
        "route-not-text",
        "empty-alias",
        "time-not-text",
        "holds-a-key",
    ],
)
def test_a_malformed_manifest_is_refused_and_writes_nothing(
    tmp_path: Path,
    dest: Path,
    platform: Path,
    capsys: pytest.CaptureFixture[str],
    body: Any,
    said: str,
) -> None:
    path = _manifest(tmp_path, body)

    assert _new(dest, platform, "reader", "--kind", "tool", "--key-manifest", str(path)) == 2
    err = capsys.readouterr().err
    assert err.startswith("agentctl: --key-manifest") and str(path) in err and said in err
    assert "sk-x" not in err, "a key value is never echoed"
    assert list(dest.iterdir()) == []


def test_a_missing_manifest_is_refused_and_names_the_seed(
    tmp_path: Path, dest: Path, platform: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "svc-nobody.json"

    assert _new(dest, platform, "reader", "--kind", "tool", "--key-manifest", str(path)) == 2
    err = capsys.readouterr().err
    assert err.startswith("agentctl: --key-manifest") and "no file" in err and "seed.sh" in err
    assert list(dest.iterdir()) == []


def test_key_manifest_and_key_route_are_one_or_the_other(tmp_path: Path, platform: Path) -> None:
    path = _manifest(tmp_path, _good(["big-default"]))
    args = ("--key-manifest", str(path), "--key-route", "big-default")

    with pytest.raises(SystemExit) as both:
        _new(tmp_path, platform, "reader", "--kind", "tool", *args)
    assert both.value.code == 2


def test_the_reader_returns_the_alias_and_routes(tmp_path: Path) -> None:
    path = _manifest(tmp_path, _good(["big-default", "fake-chat"]))

    got = manifest.read_key_manifest(path)

    assert (got.key_alias, got.routes) == ("svc-reader", ("big-default", "fake-chat"))
    assert got.written_at == "2026-11-14T10:00:00Z"


def _has_jq() -> bool:
    probe = subprocess.run(["bash", "-c", f"PATH={JQ_PATH} command -v jq"], capture_output=True)
    return probe.returncode == 0


@pytest.mark.skipif(not _has_jq(), reason="no jq")
def test_the_seed_writer_and_agentctl_agree_on_the_format(
    tmp_path: Path, dest: Path, platform: Path
) -> None:
    """The deploy side: `write_key_manifest` in lib.sh, the function both seed scripts call when
    they mint a key, writes a file agentctl reads; it holds the alias and routes, nothing else."""
    folder = tmp_path / "manifests"
    code = (
        f"set -euo pipefail\nCONTEXT=c\nLOG_PREFIX=p\nsource {LIB}\n"
        f'write_key_manifest "{folder}" svc-reader fake-chat big-default\n'
    )
    done = subprocess.run(
        [shutil.which("bash") or "bash", "-c", code],
        env={"PATH": JQ_PATH},
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    path = folder / "svc-reader.json"

    data = json.loads(path.read_text())
    assert set(data) == {"version", "key_alias", "routes", "written_at"}
    assert [p.name for p in folder.iterdir()] == ["svc-reader.json"], "no temp file left"
    got = manifest.read_key_manifest(path)
    assert (got.key_alias, got.routes) == ("svc-reader", ("fake-chat", "big-default"))
    assert _new(dest, platform, "reader", "--kind", "tool", "--key-manifest", str(path)) == 0


def test_the_seed_writer_leaves_no_temp_file_when_jq_fails(tmp_path: Path) -> None:
    """Security review (2026-11-14): a failed write stops the seed and removes its temp file."""
    stubs = tmp_path / "bin"
    stubs.mkdir()
    (stubs / "jq").write_text("#!/bin/sh\nexit 3\n")
    (stubs / "jq").chmod(0o755)
    folder = tmp_path / "manifests"
    code = (
        f"set -euo pipefail\nCONTEXT=c\nLOG_PREFIX=p\nsource {LIB}\n"
        f'write_key_manifest "{folder}" svc-reader fake-chat\necho not-reached\n'
    )

    done = subprocess.run(
        [shutil.which("bash") or "bash", "-c", code],
        env={"PATH": f"{stubs}:/usr/bin:/bin"},
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert done.returncode == 1 and "not-reached" not in done.stdout
    assert "[p] could not write the key manifest for svc-reader" in done.stderr
    assert list(folder.iterdir()) == []


@pytest.mark.parametrize("seed", SEEDS, ids=["poc05", "poc06"])
def test_each_seed_writes_the_manifest_right_after_it_stores_the_key(seed: Path) -> None:
    """Both seed scripts call `write_key_manifest` with the key's alias and its `models` list:
    after `put_secret` for a new key, and for a key whose Secret is already there."""
    text = seed.read_text()

    stored = text.index('put_secret "$AGENTS" "chassis-$svc-litellm" LITELLM_API_KEY')
    written = text.index('write_key_manifest "$KEY_MANIFESTS" "svc-$svc" "$MODEL"')
    assert stored < written
    assert "KEY_MANIFESTS=${KEY_MANIFEST_DIR:-$HERE/" in text
    # A key whose Secret is already there still gets its manifest, from $MODEL, not the Secret.
    seed_keys = text[text.index("seed_keys() {") :]
    kept = seed_keys.index('if secret_exists "$AGENTS" "chassis-$svc-litellm"; then')
    assert seed_keys.index('write_key_manifest "$KEY_MANIFESTS" "svc-$svc" "$MODEL"') > kept
