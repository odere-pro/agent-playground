"""The testcontainers helpers run the images the Compose scale stack runs, pinned by digest.

Every `*_IMAGE` constant in `chassis_contracts/containers/*.py`, and the kind PoC-4 Valkey, is
`name:tag@sha256:<64 hex>` and equals the `image:` that `deploy/compose/docker-compose.scale*.yaml`
gives the same repository. An intended difference is listed in `deploy/compose/SECURITY.md`,
section 7, with its exact image string. The constants are read with `ast`, so nothing here imports
testcontainers or talks to Docker.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
CONTAINERS = ROOT / "packages/contract-suites/src/chassis_contracts/containers"
COMPOSE = sorted((ROOT / "deploy/compose").glob("docker-compose.scale*.yaml"))
KIND_VALKEY = ROOT / "deploy/kind/poc04/base/valkey.yaml"
SECURITY = ROOT / "deploy/compose/SECURITY.md"
DIGEST = re.compile(r"@sha256:[0-9a-f]{64}$")
DIFFERS = {"KAFKA_IMAGE": "confluentinc/cp-kafka"}
"""Constant name to the repository it runs instead of Compose's; SECURITY.md says why."""


def _repository(image: str) -> str:
    """`valkey/valkey:8.1.4-alpine@sha256:...` -> `valkey/valkey`."""
    name = image.split("@", 1)[0]
    last = name.rsplit("/", 1)[-1]
    return name.rsplit(":", 1)[0] if ":" in last else name


def _constants() -> list[tuple[str, str]]:
    """(`module.NAME`, value) for every module-level `*_IMAGE = "..."` in containers/."""
    return [
        (f"{path.stem}.{target.id}", str(node.value.value))
        for path in sorted(CONTAINERS.glob("*.py"))
        for node in ast.parse(path.read_text()).body
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant)
        for target in node.targets
        if isinstance(target, ast.Name) and target.id.endswith("_IMAGE")
    ]


def _compose_images() -> dict[str, str]:
    """Repository to the one `image:` the scale files give it (built images excluded)."""
    images: dict[str, str] = {}
    for path in COMPOSE:
        text = yaml.safe_load(path.read_text())
        anchors = [v for k, v in text.items() if k.startswith("x-") and isinstance(v, dict)]
        for service in [*(text.get("services") or {}).values(), *anchors]:
            image = (service or {}).get("image")
            if image and "@sha256:" in image:
                images = {**images, _repository(image): image}
    return images


def _kind_valkey() -> str:
    docs = [d for d in yaml.safe_load_all(KIND_VALKEY.read_text()) if d]
    deployment = next(d for d in docs if d["kind"] == "Deployment")
    containers = deployment["spec"]["template"]["spec"]["containers"]
    return str(next(c for c in containers if c["name"] == "valkey")["image"])


PINNED = [*_constants(), ("kind.poc04.valkey", _kind_valkey())]
COMPOSE_IMAGES = _compose_images()


def test_containers_define_one_image_constant_per_service_when_modules_are_read() -> None:
    """The four helpers each name their image, so none can drift unseen."""
    # Arrange
    expected = {"dapr", "kafka", "minio", "valkey"}

    # Act
    modules = {name.split(".", 1)[0] for name, _ in _constants()}

    # Assert
    assert modules == expected


@pytest.mark.parametrize(("name", "image"), PINNED, ids=[n for n, _ in PINNED])
def test_test_image_is_pinned_by_digest_when_constant_is_read(name: str, image: str) -> None:
    """Every test image ends in `@sha256:` and 64 hex characters."""
    # Arrange
    pattern = DIGEST

    # Act
    match = pattern.search(image)

    # Assert
    assert match is not None, f"{name} = {image!r} is not pinned by digest"


@pytest.mark.parametrize(("name", "image"), PINNED, ids=[n for n, _ in PINNED])
def test_test_image_equals_compose_image_when_no_difference_is_listed(
    name: str, image: str
) -> None:
    """A test image is Compose's image for its repository, or a difference SECURITY.md lists."""
    # Arrange
    constant = name.rsplit(".", 1)[-1]
    listed = DIFFERS.get(constant)

    # Act
    compose_image = COMPOSE_IMAGES.get(_repository(image))

    # Assert
    if listed is None:
        assert image == compose_image, f"{name}: {image!r} != Compose {compose_image!r}"
    else:
        assert _repository(image) == listed
        assert f"`{image}`" in SECURITY.read_text(), f"{name}: {image!r} not in SECURITY.md"


def test_kraft_version_reads_the_tag_when_image_is_pinned_by_digest() -> None:
    """`with_kraft` checks the tag; a digest after it must not be read as the version."""
    # Arrange
    from chassis_contracts.containers.kafka import kraft_version

    image = "confluentinc/cp-kafka:7.6.0@sha256:" + "a" * 64

    # Act
    version = kraft_version(image)

    # Assert
    assert version == "7.6.0"
