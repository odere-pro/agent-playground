#!/usr/bin/env python3
"""Check that the Claude layer, the PoC folders, and docs/plans are wired correctly.

Runs in `make check` and CI. Exit code 1 on any finding. It catches an agent that names a
skill that does not exist, a hook that settings.json names but the disk lacks, a PoC folder
without its README or CLAUDE.md, and a plan without a Status line. It also keeps the root
CLAUDE.md agent and skill lists equal to the disk, caps CLAUDE.md size, checks that repo paths
cited in CLAUDE.md files exist, and rejects two test files with the same name (test folders
have no __init__.py, so pytest cannot import both).
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
CLAUDE = ROOT / ".claude"
AGENT_FIELDS = {"name", "description", "tools", "model"}
STATUSES = {"approved", "in progress", "done", "superseded"}
CLAUDE_MD_MAX_BYTES = 8_000  # suggested: keeps every brief cheap to load
SKIP_DIRS = {".git", ".venv", "node_modules", ".mypy_cache", ".pytest_cache", ".ruff_cache"}
REPO_DIRS = ("packages/", "pocs/", "deploy/", "docs/", "scripts/", ".claude/", ".github/")
# Paths a CLAUDE.md names on purpose after they were removed (history, not a pointer).
REMOVED_PATHS = {"packages/workloads/hostile"}

findings: list[str] = []
say = findings.append


def frontmatter(path: Path) -> dict[str, object]:
    text = path.read_text()
    m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    if not m:
        say(f"{path.relative_to(ROOT)}: no frontmatter")
        return {}
    data = yaml.safe_load(m.group(1)) or {}
    return data if isinstance(data, dict) else {}


def check_agents() -> None:
    skills = {p.name for p in (CLAUDE / "skills").iterdir() if p.is_dir()}
    for path in sorted((CLAUDE / "agents").glob("*.md")):
        fm = frontmatter(path)
        missing = AGENT_FIELDS - fm.keys()
        if missing:
            say(f"{path.relative_to(ROOT)}: missing frontmatter {sorted(missing)}")
        if fm.get("name") != path.stem:
            say(f"{path.relative_to(ROOT)}: name {fm.get('name')!r} does not match the file name")
        declared = fm.get("skills")
        for skill in declared if isinstance(declared, list) else []:
            if skill not in skills:
                say(f"{path.relative_to(ROOT)}: skill {skill!r} does not exist")


def check_skills() -> None:
    for folder in sorted(p for p in (CLAUDE / "skills").iterdir() if p.is_dir()):
        skill = folder / "SKILL.md"
        if not skill.exists():
            say(f"{folder.relative_to(ROOT)}: no SKILL.md")
            continue
        fm = frontmatter(skill)
        if fm.get("name") != folder.name:
            say(f"{skill.relative_to(ROOT)}: name {fm.get('name')!r} does not match the folder")
        if not fm.get("description"):
            say(f"{skill.relative_to(ROOT)}: no description")


def check_settings() -> None:
    settings = json.loads((CLAUDE / "settings.json").read_text())
    style = settings.get("outputStyle")
    if style and not (CLAUDE / "output-styles" / f"{style}.md").exists():
        say(f"settings.json: output style {style!r} has no file")
    for event, groups in settings.get("hooks", {}).items():
        for group in groups:
            for hook in group.get("hooks", []):
                cmd = hook.get("command", "").replace("${CLAUDE_PROJECT_DIR}", str(ROOT))
                path = Path(cmd.split()[0]) if cmd else None
                if path is None or not path.exists():
                    say(f"settings.json: {event} hook {cmd!r} does not exist")
                elif not os.access(path, os.X_OK):
                    say(f"settings.json: {event} hook {path.relative_to(ROOT)} is not executable")


def check_folders() -> None:
    for pkg in sorted(p for p in (ROOT / "packages").iterdir() if p.is_dir()):
        if pkg.name == "workloads":
            continue
        for name in ("CLAUDE.md", "README.md", "tests"):
            if not (pkg / name).exists():
                say(f"{pkg.relative_to(ROOT)}: missing {name}")
    pocs = ROOT / "pocs"
    current = (pocs / "CURRENT").read_text().strip()
    if not (pocs / current).is_dir():
        say(f"pocs/CURRENT names {current!r}, which does not exist")
    for poc in sorted(p for p in pocs.iterdir() if p.is_dir() and p.name.startswith("poc-")):
        for name in ("README.md", "CLAUDE.md", "tests", "demo", "notes"):
            if not (poc / name).exists():
                say(f"{poc.relative_to(ROOT)}: missing {name}")


def check_plans() -> None:
    for path in sorted((ROOT / "docs" / "plans").glob("*.md")):
        if path.name == "README.md":
            continue
        if not re.match(r"^\d{4}-\d{2}-\d{2}-[a-z0-9-]+\.md$", path.name):
            say(f"{path.relative_to(ROOT)}: name must be YYYY-MM-DD-<slug>.md")
        m = re.search(r"^Status:\s*(.+)$", path.read_text(), re.M)
        if not m:
            say(f"{path.relative_to(ROOT)}: no `Status:` line")
        elif m.group(1).strip().lower() not in STATUSES:
            say(f"{path.relative_to(ROOT)}: status {m.group(1)!r} not in {sorted(STATUSES)}")


def repo_files(pattern: str, under: Path = ROOT) -> list[Path]:
    return sorted(p for p in under.rglob(pattern) if not SKIP_DIRS & set(p.relative_to(ROOT).parts))


def check_index() -> None:
    text = (ROOT / "CLAUDE.md").read_text()
    delegation = text.split("## Delegation", 1)[-1]
    table, _, skills_line = delegation.partition("Skills:")
    listed_agents = set(re.findall(r"\| `([a-z0-9-]+)` \|", table))
    listed_skills = set(re.findall(r"`([a-z0-9-]+)`", skills_line.splitlines()[0]))
    agents = {p.stem for p in (CLAUDE / "agents").glob("*.md")}
    skills = {p.name for p in (CLAUDE / "skills").iterdir() if p.is_dir()}
    for kind, on_disk, listed in (
        ("agent", agents, listed_agents),
        ("skill", skills, listed_skills),
    ):
        for name in sorted(on_disk - listed):
            say(f"CLAUDE.md: {kind} {name!r} exists but is not listed")
        for name in sorted(listed - on_disk):
            say(f"CLAUDE.md: {kind} {name!r} is listed but does not exist")


def check_claude_md() -> None:
    for path in repo_files("CLAUDE.md"):
        rel = path.relative_to(ROOT)
        size = path.stat().st_size
        if size > CLAUDE_MD_MAX_BYTES:
            say(f"{rel}: {size} bytes, over {CLAUDE_MD_MAX_BYTES}; move detail to docs/guides")
        for token in re.findall(r"`([^`\s]+)`", path.read_text()):
            if not token.startswith(REPO_DIRS) or re.search(r"[<>*{}$]|NN|\.\.\.", token):
                continue
            cited = token.split(":")[0].rstrip(".,")
            if cited.rstrip("/") not in REMOVED_PATHS and not (ROOT / cited).exists():
                say(f"{rel}: cites {cited!r}, which does not exist")


def check_test_names() -> None:
    seen: dict[str, Path] = {}
    for top in ("packages", "pocs"):
        for path in repo_files("test_*.py", ROOT / top):
            first = seen.setdefault(path.name, path)
            if first != path:
                rel, other = path.relative_to(ROOT), first.relative_to(ROOT)
                say(f"{rel}: same file name as {other}; pytest cannot import both")


def main() -> int:
    check_agents()
    check_skills()
    check_settings()
    check_folders()
    check_plans()
    check_index()
    check_claude_md()
    check_test_names()
    for f in findings:
        print(f"harness-lint: {f}")
    print("harness-lint: ok" if not findings else f"harness-lint: {len(findings)} finding(s)")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
