"""PoC-9 exit criterion 7: "ADR-001 and the ADRs from PoC-6 and PoC-7 are linked from the template
docs." (docs/planning/poc/009-PoC-9-agent-mvp-template.md)

Static checks, no network:

- The template's `docs/decisions.md` and its copy of the guide link ADR-001, ADR-006 (PoC-6), and
  ADR-007 to ADR-010 (PoC-7). Inside a rendered agent repo the ADRs are not on disk, so those
  links are absolute URLs into this monorepo; each must name an ADR file that exists here.
- Every relative link in the monorepo guide `docs/guides/how-to-add-business-logic.md` resolves to
  an existing file, and so does every relative link in the template docs.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
GUIDE = ROOT / "docs" / "guides" / "how-to-add-business-logic.md"
TEMPLATE_DOCS = ROOT / "packages" / "agentctl" / "src" / "agentctl" / "template" / "docs"
DECISIONS = TEMPLATE_DOCS / "decisions.md"
TEMPLATE_GUIDE = TEMPLATE_DOCS / "how-to-add-business-logic.md"
MONOREPO_URL = "https://github.com/odere-pro/agent-playground/blob/main/"
ADR_DIR = ROOT / "docs" / "planning" / "adr"
# ADR-001, the bake-off ADR (PoC-6), and the four cross-cutting ADRs (PoC-7).
REQUIRED_ADRS = ("001", "006", "007", "008", "009", "010")
LINK = re.compile(r"\[[^\]]+\]\(([^)\s]+)\)")


def links(path: Path) -> list[str]:
    return LINK.findall(path.read_text())


def is_relative(target: str) -> bool:
    return not re.match(r"[a-z][a-z0-9+.-]*:", target) and not target.startswith("#")


def linked_adrs(path: Path) -> set[str]:
    """The ADR numbers a doc links, by absolute monorepo URL or by relative path."""
    found = set()
    for target in links(path):
        m = re.search(r"docs/planning/adr/(\d{3})-[^/#]+\.md|adr/(\d{3})-[^/#]+\.md", target)
        if m:
            found.add(m.group(1) or m.group(2))
    return found


@pytest.mark.parametrize("doc", [DECISIONS, TEMPLATE_GUIDE], ids=lambda p: p.name)
def test_the_template_docs_link_adr_001_and_the_poc6_and_poc7_adrs(doc: Path) -> None:
    missing = sorted(set(REQUIRED_ADRS) - linked_adrs(doc))
    assert not missing, f"{doc.relative_to(ROOT)} does not link ADR {missing}"


@pytest.mark.parametrize("doc", [DECISIONS, TEMPLATE_GUIDE], ids=lambda p: p.name)
def test_every_monorepo_url_in_the_template_docs_names_a_file_that_exists(doc: Path) -> None:
    urls = [t for t in links(doc) if t.startswith(MONOREPO_URL)]
    assert urls, f"{doc.relative_to(ROOT)} has no link into the monorepo"
    broken = [u for u in urls if not (ROOT / u[len(MONOREPO_URL) :].split("#")[0]).is_file()]
    assert not broken, f"{doc.relative_to(ROOT)}: {broken}"


@pytest.mark.parametrize("doc", [DECISIONS, TEMPLATE_GUIDE], ids=lambda p: p.name)
def test_every_relative_link_in_the_template_docs_stays_inside_the_docs_folder(doc: Path) -> None:
    broken = [
        t for t in links(doc) if is_relative(t) and not (doc.parent / t.split("#")[0]).is_file()
    ]
    assert not broken, f"{doc.relative_to(ROOT)}: {broken}"


def test_every_relative_link_in_the_monorepo_guide_resolves() -> None:
    targets = [t for t in links(GUIDE) if is_relative(t)]
    assert targets, "the guide has no relative link"
    broken = [t for t in targets if not (GUIDE.parent / t.split("#")[0]).resolve().is_file()]
    assert not broken, f"{GUIDE.relative_to(ROOT)}: {broken}"


def test_the_monorepo_guide_links_every_adr_it_rests_on() -> None:
    required = {"001", "002", "005", *REQUIRED_ADRS}
    missing = sorted(required - linked_adrs(GUIDE))
    assert not missing, f"the guide does not link ADR {missing}"
    for number in required:
        assert len(list(ADR_DIR.glob(f"{number}-*.md"))) == 1, f"ADR-{number} is not one file"
