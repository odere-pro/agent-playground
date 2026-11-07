"""Verify the backlog and the docs. Exit code 1 on any failure.

Usage: python3 tools/check.py [--strict] [FIRST LAST]
With FIRST LAST (issue indexes), body checks run only on that range; every other check always runs.
--strict adds the evidence rules for ticked boxes and for done (WD) issues.
"""
import argparse
import hashlib
import re
import sys
from pathlib import Path

import yaml

from backlog import WAVES

TOOLS = Path(__file__).resolve().parent
ROOT = TOOLS.parent
EPIC_NAME = "slm-agent-platform-epic-v3.md"
MARKER = "<!-- BODY: replace"
SECTIONS = ["## Why", "## What", "## Out of scope", "## Acceptance criteria", "## Dependencies", "## References"]
DONE, PARKED = WAVES["WD"], WAVES["WP"]
# A story the backlog splits into an `a` and a `b` issue, or into the story ID and a `b` issue.
SPLIT = ("X-1", "X-5", "P-2")
# Repo folders a ticked line may cite as evidence; --strict checks each cited path exists.
EVIDENCE_DIRS = ("pocs/", "packages/", "deploy/", "docs/", ".github/")
BOX = re.compile(r"^- \[([ x])\]", re.M)
TICKED = re.compile(r"^- \[x\].*$", re.M)
DELIVERED_BY = re.compile(r"^(?:- )?Delivered by:", re.M)
TOKEN = re.compile(r"`([^`]+)`")
LINK = re.compile(r"\]\([^)\s]+\)")


def slugs(text):
    out = set(re.findall(r'<a id="([^"]+)"', text))
    for h in re.findall(r"^#{1,6} (.+)$", text, re.M):
        out.add(re.sub(r"[^a-z0-9 -]", "", h.lower()).replace(" ", "-"))
    return out


def acceptance(s):
    return s.split("## Acceptance criteria", 1)[-1].split("## Dependencies", 1)[0]


def check_epic(root):
    """1. The epic is read-only."""
    want = (root / "tools" / "epic.sha256").read_text().split()[0]
    if hashlib.sha256((root / EPIC_NAME).read_bytes()).hexdigest() != want:
        return [f"EPIC CHANGED: {EPIC_NAME} must not be edited"]
    return []


def read_issues(files):
    """Each issue's file, text, and frontmatter. A file with no frontmatter is an error."""
    out, errors = [], []
    for p in files:
        s = p.read_text()
        m = re.match(r"^---\n(.*?)\n---\n", s, re.S)
        if not m:
            errors.append(f"{p.name}: no frontmatter")
            continue
        out.append((p, s, yaml.safe_load(m.group(1))))
    return out, errors


def check_frontmatter(p, fm):
    """2. Frontmatter keys, index, file name, and dependency order."""
    errors, n = [], int(p.name[:3])
    for k in ("title", "labels", "milestone", "index", "epic_id", "depends_on", "blocks"):
        if k not in fm:
            errors.append(f"{p.name}: frontmatter missing {k}")
    if fm.get("index") != n:
        errors.append(f"{p.name}: index {fm.get('index')} != {n}")
    eid = str(fm.get("epic_id"))
    if not p.name.startswith(f"{n:03d}-{eid}-"):
        errors.append(f"{p.name}: epic_id {eid} not in file name")
    for d in fm.get("depends_on", []):
        if int(d[:3]) >= n:
            errors.append(f"{p.name}: depends on a later issue {d}")
    for b in fm.get("blocks", []):
        if int(b[:3]) <= n:
            errors.append(f"{p.name}: blocks an earlier issue {b}")
    return errors


def check_parked_deps(issues):
    """A live issue (not done, not parked) must not depend on a parked one.

    Reads the milestone from each file's frontmatter, not from backlog.build(): check.py verifies
    the files as they are on disk, so a stale sync shows here too, and a test tree needs no ROWS.
    """
    milestone = {fm.get("index"): fm.get("milestone") for _, _, fm in issues}
    errors = []
    for p, _, fm in issues:
        if fm.get("milestone") in (DONE, PARKED):
            continue
        for d in fm.get("depends_on", []):
            if milestone.get(int(d[:3])) == PARKED:
                errors.append(f"{p.name}: live issue depends on parked {d}")
    return errors


def check_body(p, s):
    """2. Body shape: sections in order and at least 3 acceptance criteria, open or ticked."""
    if MARKER in s:
        return [f"{p.name}: body not written"]
    errors = []
    pos = [s.find("\n" + h + "\n") for h in SECTIONS]
    if -1 in pos or pos != sorted(pos):
        errors.append(f"{p.name}: sections missing or out of order")
    if len(BOX.findall(acceptance(s))) < 3:
        errors.append(f"{p.name}: fewer than 3 acceptance criteria")
    return errors


def check_ticked_line(p, line, repo):
    """--strict: a ticked line names where it was delivered, with a token or link that exists."""
    errors = []
    if "Delivered in PoC-" not in line and "Delivered by:" not in line:
        errors.append(f"{p.name}: ticked line has no `Delivered in PoC-` or `Delivered by:`: {line}")
    tokens = TOKEN.findall(line)
    if not tokens and not LINK.search(line):
        errors.append(f"{p.name}: ticked line has no backtick token or link: {line}")
    for token in tokens:
        errors += check_cited_path(p, token, repo)
    return errors


def check_cited_path(p, token, repo):
    """--strict: a cited `path`, `path::test`, or `path#anchor` exists, and so does its anchor."""
    path, _, anchor = token.split("::", 1)[0].partition("#")
    if not path.startswith(EVIDENCE_DIRS):
        return []
    target = repo / path
    if not target.exists():
        return [f"{p.name}: ticked line cites {path}, which does not exist"]
    if anchor and (not target.is_file() or anchor not in slugs(target.read_text())):
        return [f"{p.name}: ticked line cites {path}#{anchor}, which has no such anchor"]
    return []


def check_strict(p, s, fm, repo):
    """--strict: the evidence form, and an issue with no open box is retired."""
    ac = acceptance(s)
    errors = [e for line in TICKED.findall(ac) for e in check_ticked_line(p, line, repo)]
    has_open = " " in BOX.findall(ac)
    milestone = fm.get("milestone")
    if milestone == DONE and has_open:
        errors.append(f"{p.name}: done issue has an open box")
    if milestone == DONE and not DELIVERED_BY.search(ac):
        errors.append(f"{p.name}: done issue has no `Delivered by:` line under ## Acceptance criteria")
    if milestone not in (DONE, PARKED) and not has_open:
        errors.append(f"{p.name}: every box is ticked; retire it (wave WD)")
    return errors


def check_coverage(covered, story_ids):
    """3. Every epic story exactly once; a split story twice. DEC-, L-, CH- are not epic stories.

    X-5 is split only once X-5b exists, so it counts twice then and once before.
    """
    split = re.compile(rf"^({'|'.join(map(re.escape, SPLIT))})[ab]$")
    base = [split.sub(r"\1", c) for c in covered if not c.startswith(("DEC-", "L-", "CH-"))]
    twice = {"X-1", "P-2"} | ({"X-5"} if "X-5b" in covered else set())
    errors = []
    for sid in sorted(story_ids):
        k, want_k = base.count(sid), 2 if sid in twice else 1
        if k != want_k:
            errors.append(f"story {sid} covered {k} times, expected {want_k}")
    if set(base) - story_ids:
        errors.append(f"issue IDs not in the epic: {sorted(set(base) - story_ids)}")
    return errors


def check_links(root):
    """4. Links and anchors in issues/, poc/, adr/; frontmatter parses everywhere."""
    errors = []
    for f in sorted(p for d in ("issues", "poc", "adr") for p in (root / d).glob("*.md")):
        s = f.read_text()
        if s.startswith("---\n"):
            try:
                yaml.safe_load(re.match(r"^---\n(.*?)\n---\n", s, re.S).group(1))
            except yaml.YAMLError as e:
                errors.append(f"{f.parent.name}/{f.name}: bad frontmatter: {e}")
        for t in re.findall(r"\]\(([^)\s]+)\)", s):
            if t.startswith(("http://", "https://", "mailto:")):
                continue
            path, _, anchor = t.partition("#")
            target = (f.parent / path).resolve() if path else f.resolve()
            if not target.exists():
                errors.append(f"{f.parent.name}/{f.name}: broken link {t}")
            elif anchor and anchor not in slugs(target.read_text()):
                errors.append(f"{f.parent.name}/{f.name}: bad anchor {t}")
    return errors


def run(root=ROOT, first=1, last=10**6, strict=False):
    """Every check on the planning folder `root` (the repo is two levels up). Returns the errors
    and a one-line summary."""
    repo = root.parents[1]
    errors = check_epic(root)
    story_ids = set(re.findall(r"^\d+\. \*\*([A-Z]-\d+b?)\*\*", (root / EPIC_NAME).read_text(), re.M))
    files = sorted(p for p in (root / "issues").glob("*.md") if p.name != "000-plan.md")
    idx = [int(p.name[:3]) for p in files]
    if idx != list(range(1, len(files) + 1)):
        errors.append(f"issue indexes are not contiguous from 001: {idx}")
    issues, read_errors = read_issues(files)
    errors += read_errors
    for p, s, fm in issues:
        errors += check_frontmatter(p, fm)
        if first <= int(p.name[:3]) <= last:
            body = check_body(p, s)
            errors += body
            if strict and MARKER not in s:
                errors += check_strict(p, s, fm, repo)
    errors += check_parked_deps(issues)
    errors += check_coverage([str(fm.get("epic_id")) for _, _, fm in issues], story_ids)
    errors += check_links(root)
    return errors, f"{len(files)} issues, {len(story_ids)} epic stories"


def parse_args(argv):
    """`[--strict] [FIRST LAST]` as (first, last, strict)."""
    ap = argparse.ArgumentParser(description="Verify the backlog and the planning docs.")
    ap.add_argument("--strict", action="store_true", help="also check the evidence on ticked boxes")
    ap.add_argument("range", nargs="*", type=int, metavar="FIRST LAST")
    a = ap.parse_args(argv)
    if len(a.range) not in (0, 2):
        ap.error("give both FIRST and LAST, or neither")
    first, last = a.range or (1, 10**6)
    return first, last, a.strict


def main(argv):
    first, last, strict = parse_args(argv)
    errors, line = run(ROOT, first, last, strict)
    print(line)
    if errors:
        print("\n".join(errors))
        return 1
    print("OK")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
