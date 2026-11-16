"""publish_chassis.sh: the build, push, and pin of the default chassis image (139 CH-19).

The image is `ghcr.io/odere-pro/agent-chassis`, tagged `vX.Y.Z` and pinned by digest in the
library chart (decided 2026-11-15). The tag comes from packages/chassis/pyproject.toml. These
tests run the script's dry run and its `pin` verb on a copy of the chart; neither calls Docker,
logs in, or touches the network. A `docker` on PATH that fails proves the dry run calls none.
"""

from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/publish_chassis.sh"
VALUES_TPL = ROOT / "deploy/helm/agent-lib/templates/_values.tpl"
PYPROJECT = ROOT / "packages/chassis/pyproject.toml"
IMAGE = "ghcr.io/odere-pro/agent-chassis"
PLACEHOLDER = "sha256:" + "0" * 64
NEW_DIGEST = "sha256:" + "ab" * 32
DIGEST_LINE = re.compile(r"^    digest: (\S+)$", re.M)
TAG_LINE = re.compile(r"^    tag: (\S+)$", re.M)


def _version() -> str:
    with PYPROJECT.open("rb") as f:
        version: str = tomllib.load(f)["project"]["version"]
    return f"v{version}"


@pytest.fixture
def no_docker(tmp_path: Path) -> dict[str, str]:
    """An env whose `docker`, `gh`, and `git push` stand-ins fail loudly if called."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for tool in ("docker", "gh", "cosign"):
        stub = bin_dir / tool
        stub.write_text(f"#!/bin/sh\necho '{tool} was called' >&2\nexit 97\n")
        stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
    keep = {k: v for k, v in os.environ.items() if k in {"HOME", "TMPDIR", "LANG"}}
    return {**keep, "PATH": f"{bin_dir}:/usr/bin:/bin"}


def _run(env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(SCRIPT), *args], capture_output=True, text=True, timeout=30, env=env
    )


def _digest_line(line: str) -> bool:
    return line.startswith(("    digest: ", "    # PLACEHOLDER"))


def _chart_copy(tmp_path: Path) -> Path:
    copy = tmp_path / "_values.tpl"
    shutil.copy(VALUES_TPL, copy)
    return copy


def test_the_chart_tag_is_the_chassis_package_version() -> None:
    """One version: the package, `chassis.__version__`, and the library chart's tag agree."""
    from chassis import CHASSIS_VERSION

    (tag,) = TAG_LINE.findall(VALUES_TPL.read_text())
    assert tag == _version() == f"v{CHASSIS_VERSION}"


def test_the_digest_is_the_marked_placeholder_until_the_first_push() -> None:
    """Box 4 stays open while this holds: the zero digest says no image is published yet."""
    text = VALUES_TPL.read_text()
    (digest,) = DIGEST_LINE.findall(text)
    if digest == PLACEHOLDER:
        assert "PLACEHOLDER" in text and "scripts/publish_chassis.sh" in text


def test_the_dry_run_prints_build_push_and_pin_and_calls_nothing(
    no_docker: dict[str, str],
) -> None:
    proc = _run({**no_docker, "PUBLISH_DRY_RUN": "1"}, "publish")
    assert proc.returncode == 0, proc.stderr
    assert "was called" not in proc.stderr
    steps = [line for line in proc.stdout.splitlines() if line.startswith("+ ")]
    (build,) = [s for s in steps if "buildx build" in s]
    assert f"--tag {IMAGE}:{_version()}" in build
    assert "--push" in build and "--metadata-file" in build
    assert "--platform linux/amd64,linux/arm64" in build
    assert "-f packages/chassis/Dockerfile" in build
    assert "org.opencontainers.image.source=https://github.com/odere-pro/agent-playground" in build
    assert f"org.opencontainers.image.version={_version()}" in build
    assert "--sbom=true" in build and "--provenance=mode=max" in build
    assert "SOURCE_DATE_EPOCH=" in build, "the build time is the HEAD commit's"
    assert any(s.startswith("+ pin ") for s in steps), "the digest is written into the chart"
    assert not [s for s in steps if "login" in s], "the script never logs in; the user does"


def test_the_script_holds_no_credential() -> None:
    text = SCRIPT.read_text()
    assert "docker login" not in text.replace("# ", "").split("set -euo pipefail")[1]
    assert not re.search(r"(?i)(password|token)=\S", text)
    assert "GITHUB_TOKEN" not in text and "GHCR_TOKEN" not in text


def test_pin_writes_the_digest_into_the_library_chart(
    no_docker: dict[str, str], tmp_path: Path
) -> None:
    copy = _chart_copy(tmp_path)
    proc = _run({**no_docker, "CHASSIS_VALUES_TPL": str(copy)}, "pin", NEW_DIGEST)
    assert proc.returncode == 0, proc.stderr
    text = copy.read_text()
    assert DIGEST_LINE.findall(text) == [NEW_DIGEST]
    assert "PLACEHOLDER" not in text, "a real digest drops the placeholder note"
    # Every other line is unchanged.
    before = [ln for ln in VALUES_TPL.read_text().splitlines() if not _digest_line(ln)]
    after = [ln for ln in text.splitlines() if not _digest_line(ln)]
    assert before == after


@pytest.mark.parametrize(
    "digest", ["sha256:abc", "latest", "sha256:" + "G" * 64, PLACEHOLDER], ids=str
)
def test_pin_refuses_a_digest_that_is_not_a_real_sha256(
    no_docker: dict[str, str], tmp_path: Path, digest: str
) -> None:
    copy = _chart_copy(tmp_path)
    proc = _run({**no_docker, "CHASSIS_VALUES_TPL": str(copy)}, "pin", digest)
    assert proc.returncode != 0
    assert copy.read_text() == VALUES_TPL.read_text(), "a refused pin writes nothing"


def test_publish_refuses_when_the_chart_tag_is_not_the_package_version(
    clean_repo: Path, no_docker: dict[str, str]
) -> None:
    chart = clean_repo / "deploy/helm/agent-lib/templates/_values.tpl"
    chart.write_text(TAG_LINE.sub("    tag: v9.9.9", chart.read_text(), count=1))
    proc = subprocess.run(
        ["bash", str(clean_repo / "scripts/publish_chassis.sh"), "publish"],
        capture_output=True,
        text=True,
        timeout=30,
        env={**no_docker, "PUBLISH_DRY_RUN": "1"},
    )
    assert proc.returncode != 0
    assert "v9.9.9" in proc.stderr and _version() in proc.stderr


def _dry_steps(env: dict[str, str]) -> list[str]:
    proc = _run({**env, "PUBLISH_DRY_RUN": "1"}, "publish")
    assert proc.returncode == 0, proc.stderr
    return [line for line in proc.stdout.splitlines() if line.startswith("+ ")]


def test_the_push_is_signed_by_digest_never_by_tag(no_docker: dict[str, str]) -> None:
    steps = _dry_steps(no_docker)
    (sign,) = [s for s in steps if s.startswith("+ cosign sign")]
    assert "--yes" in sign and f"{IMAGE}@" in sign and f"{IMAGE}:" not in sign
    build = next(i for i, s in enumerate(steps) if "buildx build" in s)
    assert build < steps.index(sign), "the image is signed after the push"
    assert 'cosign sign --yes "$IMAGE@$digest"' in SCRIPT.read_text()


def test_a_published_tag_is_checked_before_the_build(no_docker: dict[str, str]) -> None:
    steps = _dry_steps(no_docker)
    inspect = next(i for i, s in enumerate(steps) if "imagetools inspect" in s)
    build = next(i for i, s in enumerate(steps) if "buildx build" in s)
    assert f"{IMAGE}:{_version()}" in steps[inspect] and inspect < build


def _git(repo: Path, *args: str) -> None:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env |= {"GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1"}
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, env=env)


@pytest.fixture
def clean_repo(tmp_path: Path) -> Path:
    """A clean scratch repo, HEAD on origin/main, with the script and the two files it reads."""
    repo = tmp_path / "repo"
    for rel in (
        "scripts/publish_chassis.sh",
        "packages/chassis/pyproject.toml",
        "deploy/helm/agent-lib/templates/_values.tpl",
    ):
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(ROOT / rel, repo / rel)
    _git(repo, "init", "-q")
    _git(repo, "add", ".")
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init")
    _git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    return repo


def _docker_stub(tmp_path: Path, found: bool) -> tuple[dict[str, str], Path]:
    """A `docker` that logs its argv; `imagetools inspect` finds the tag when `found`."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    calls = tmp_path / "calls"
    calls.write_text("")
    hit = "0" if found else "1"
    stub = bin_dir / "docker"
    stub.write_text(
        f'#!/bin/sh\necho "$*" >> {calls}\n[ "$2" = imagetools ] && exit {hit}\nexit 97\n'
    )
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
    env = {"PATH": f"{bin_dir}:/usr/bin:/bin", "HOME": str(tmp_path)}
    return env, calls


def test_publish_refuses_a_tag_that_is_already_on_the_registry(
    clean_repo: Path, tmp_path: Path
) -> None:
    env, calls = _docker_stub(tmp_path, found=True)
    proc = subprocess.run(
        ["bash", str(clean_repo / "scripts/publish_chassis.sh"), "publish"],
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
    )
    assert proc.returncode != 0
    assert "already on the registry" in proc.stderr, proc.stderr
    assert "buildx build" not in calls.read_text(), "no build after the refusal"


def test_publish_refuses_a_head_that_is_not_on_origin_main(
    clean_repo: Path, tmp_path: Path
) -> None:
    _git(
        clean_repo,
        "-c",
        "user.name=t",
        "-c",
        "user.email=t@t",
        "commit",
        "-qm",
        "x",
        "--allow-empty",
    )
    env, calls = _docker_stub(tmp_path, found=False)
    proc = subprocess.run(
        ["bash", str(clean_repo / "scripts/publish_chassis.sh"), "publish"],
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
    )
    assert proc.returncode != 0
    assert "not on origin/main" in proc.stderr, proc.stderr
    assert calls.read_text() == "", "no registry call after the refusal"


def _publish(repo: Path, env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(repo / "scripts/publish_chassis.sh"), *args],
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
    )


def _registry_stub(tmp_path: Path, inspect_exit: int, err: str) -> tuple[dict[str, str], Path]:
    """A `docker` whose `imagetools inspect` exits `inspect_exit` with `err` on stderr."""
    env, calls = _docker_stub(tmp_path, found=False)
    (tmp_path / "bin/docker").write_text(
        f'#!/bin/sh\necho "$*" >> {calls}\n'
        f'if [ "$2" = imagetools ]; then echo "{err}" >&2; exit {inspect_exit}; fi\n'
        "exit 97\n"
    )
    return env, calls


def test_publish_stops_on_a_registry_error_that_is_not_not_found(
    clean_repo: Path, tmp_path: Path
) -> None:
    """platform-security: the tag check fails closed. Only "not found" means the tag is absent;
    any other inspect error (here a login problem) stops the run before the build."""
    env, calls = _registry_stub(tmp_path, 1, "ERROR: unauthorized: authentication required")
    proc = _publish(clean_repo, env, "publish")
    assert proc.returncode != 0
    assert "unauthorized" in proc.stderr, proc.stderr
    assert "buildx build" not in calls.read_text(), "no build after a registry error"


@pytest.mark.parametrize(
    "err", ["ERROR: ghcr.io/odere-pro/agent-chassis:v0.1.0: not found", "MANIFEST_UNKNOWN"]
)
def test_publish_builds_when_the_registry_says_the_tag_is_not_found(
    clean_repo: Path, tmp_path: Path, err: str
) -> None:
    env, calls = _registry_stub(tmp_path, 1, err)
    _publish(clean_repo, env, "publish")
    assert "buildx build" in calls.read_text(), "an absent tag goes on to the build"


def test_publish_refuses_a_tree_with_changes(clean_repo: Path, tmp_path: Path) -> None:
    (clean_repo / "stray.txt").write_text("not committed\n")
    env, calls = _docker_stub(tmp_path, found=False)
    proc = _publish(clean_repo, env, "publish")
    assert proc.returncode != 0
    assert "the tree has changes" in proc.stderr, proc.stderr
    assert calls.read_text() == "", "no docker call after the refusal"


def _signing_stubs(tmp_path: Path, cosign_exit: int) -> tuple[dict[str, str], Path]:
    """`docker`: inspect says not found; build writes NEW_DIGEST to its --metadata-file.
    `cosign`: logs its argv and exits `cosign_exit`."""
    env, calls = _registry_stub(tmp_path, 1, "not found")
    (tmp_path / "bin/docker").write_text(
        f'#!/bin/sh\necho "docker $*" >> {calls}\n'
        'if [ "$2" = imagetools ]; then echo "not found" >&2; exit 1; fi\n'
        "while [ $# -gt 0 ]; do\n"
        '  if [ "$1" = --metadata-file ]; then\n'
        f'    printf \'{{"containerimage.digest": "{NEW_DIGEST}"}}\' > "$2"\n'
        "  fi\n  shift\ndone\nexit 0\n"
    )
    cosign = tmp_path / "bin/cosign"
    cosign.write_text(f'#!/bin/sh\necho "cosign $*" >> {calls}\nexit {cosign_exit}\n')
    cosign.chmod(cosign.stat().st_mode | stat.S_IEXEC)
    return env, calls


def test_a_failed_sign_after_the_push_prints_the_recovery_command(
    clean_repo: Path, tmp_path: Path
) -> None:
    """The push happened, the signature did not: the log names the digest and the one command
    that signs it and pins it. The chart is not pinned to an unsigned image."""
    env, calls = _signing_stubs(tmp_path, cosign_exit=1)
    chart = clean_repo / "deploy/helm/agent-lib/templates/_values.tpl"
    before = chart.read_text()
    proc = _publish(clean_repo, env, "publish")
    assert proc.returncode != 0
    assert f"pushed {IMAGE}:{_version()}@{NEW_DIGEST}" in proc.stderr, proc.stderr
    assert f"scripts/publish_chassis.sh sign {NEW_DIGEST}" in proc.stderr, proc.stderr
    assert f"cosign sign --yes {IMAGE}@{NEW_DIGEST}" in calls.read_text()
    assert chart.read_text() == before, "an unsigned push is not pinned"


def test_sign_signs_by_digest_then_pins(no_docker: dict[str, str], tmp_path: Path) -> None:
    env, calls = _signing_stubs(tmp_path, cosign_exit=0)
    copy = _chart_copy(tmp_path)
    proc = _run({**no_docker, **env, "CHASSIS_VALUES_TPL": str(copy)}, "sign", NEW_DIGEST)
    assert proc.returncode == 0, proc.stderr
    assert calls.read_text().splitlines() == [f"cosign sign --yes {IMAGE}@{NEW_DIGEST}"]
    assert DIGEST_LINE.findall(copy.read_text()) == [NEW_DIGEST]


@pytest.mark.parametrize("digest", ["", "sha256:abc", "v0.1.0", PLACEHOLDER], ids=repr)
def test_sign_refuses_a_digest_that_is_not_a_real_sha256_before_cosign(
    no_docker: dict[str, str], tmp_path: Path, digest: str
) -> None:
    copy = _chart_copy(tmp_path)
    proc = _run({**no_docker, "CHASSIS_VALUES_TPL": str(copy)}, "sign", digest)
    assert proc.returncode != 0
    assert "was called" not in proc.stderr, "cosign runs on a real digest only"
    assert copy.read_text() == VALUES_TPL.read_text()


def test_publish_ignores_a_values_path_from_the_environment(
    clean_repo: Path, tmp_path: Path
) -> None:
    """CHASSIS_VALUES_TPL is for `pin` and `sign` only: publish checks the repo's own chart."""
    decoy = _chart_copy(tmp_path)
    decoy.write_text(TAG_LINE.sub("    tag: v9.9.9", decoy.read_text(), count=1))
    env, _ = _docker_stub(tmp_path, found=False)
    env |= {"CHASSIS_VALUES_TPL": str(decoy), "PUBLISH_DRY_RUN": "1"}
    proc = _publish(clean_repo, env, "publish")
    assert proc.returncode == 0, proc.stderr
    assert "v9.9.9" not in proc.stderr
