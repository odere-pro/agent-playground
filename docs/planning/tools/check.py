"""Verify the backlog and the docs. Exit code 1 on any failure.

Usage: python3 tools/check.py [FIRST LAST]
With FIRST LAST (issue indexes), body checks run only on that range; every other check always runs.
"""
import hashlib
import re
import sys
from pathlib import Path

import yaml

TOOLS = Path(__file__).resolve().parent
ROOT = TOOLS.parent
ISS = ROOT / "issues"
EPIC = ROOT / "slm-agent-platform-epic-v3.md"
MARKER = "<!-- BODY: replace"
SECTIONS = ["## Why", "## What", "## Out of scope", "## Acceptance criteria", "## Dependencies", "## References"]

errors = []
err = errors.append


def slugs(text):
    out = set(re.findall(r'<a id="([^"]+)"', text))
    for h in re.findall(r"^#{1,6} (.+)$", text, re.M):
        out.add(re.sub(r"[^a-z0-9 -]", "", h.lower()).replace(" ", "-"))
    return out


# 1. The epic is read-only.
want = (TOOLS / "epic.sha256").read_text().split()[0]
if hashlib.sha256(EPIC.read_bytes()).hexdigest() != want:
    err("EPIC CHANGED: slm-agent-platform-epic-v3.md must not be edited")

epic = EPIC.read_text()
story_ids = set(re.findall(r"^\d+\. \*\*([A-Z]-\d+b?)\*\*", epic, re.M))
rng = (int(sys.argv[1]), int(sys.argv[2])) if len(sys.argv) == 3 else (1, 10**6)

# 2. Issues: numbering, frontmatter, dependency order, body shape.
files = sorted(p for p in ISS.glob("*.md") if p.name != "000-plan.md")
idx = [int(p.name[:3]) for p in files]
if idx != list(range(1, len(files) + 1)):
    err(f"issue indexes are not contiguous from 001: {idx}")
covered = []
for p in files:
    s, n = p.read_text(), int(p.name[:3])
    m = re.match(r"^---\n(.*?)\n---\n", s, re.S)
    if not m:
        err(f"{p.name}: no frontmatter")
        continue
    fm = yaml.safe_load(m.group(1))
    for k in ("title", "labels", "milestone", "index", "epic_id", "depends_on", "blocks"):
        if k not in fm:
            err(f"{p.name}: frontmatter missing {k}")
    if fm.get("index") != n:
        err(f"{p.name}: index {fm.get('index')} != {n}")
    eid = str(fm.get("epic_id"))
    if not p.name.startswith(f"{n:03d}-{eid}-"):
        err(f"{p.name}: epic_id {eid} not in file name")
    covered.append(eid)
    for d in fm.get("depends_on", []):
        if int(d[:3]) >= n:
            err(f"{p.name}: depends on a later issue {d}")
    for b in fm.get("blocks", []):
        if int(b[:3]) <= n:
            err(f"{p.name}: blocks an earlier issue {b}")
    if rng[0] <= n <= rng[1]:
        if MARKER in s:
            err(f"{p.name}: body not written")
            continue
        pos = [s.find("\n" + h + "\n") for h in SECTIONS]
        if -1 in pos or pos != sorted(pos):
            err(f"{p.name}: sections missing or out of order")
        ac = s.split("## Acceptance criteria", 1)[-1].split("## Dependencies", 1)[0]
        if ac.count("- [ ]") < 3:
            err(f"{p.name}: fewer than 3 acceptance criteria")

# 3. Coverage: every epic story exactly once (X-1 and P-2 are split into a/b). DEC-, L-, and CH- issues are not epic stories.
base = [re.sub(r"^(X-1|P-2)[ab]$", r"\1", c) for c in covered if not c.startswith(("DEC-", "L-", "CH-"))]
for sid in sorted(story_ids):
    k, want_k = base.count(sid), 2 if sid in ("X-1", "P-2") else 1
    if k != want_k:
        err(f"story {sid} covered {k} times, expected {want_k}")
if set(base) - story_ids:
    err(f"issue IDs not in the epic: {sorted(set(base) - story_ids)}")

# 4. Links and anchors in issues/, poc/, adr/; frontmatter parses everywhere.
for f in sorted(p for d in ("issues", "poc", "adr") for p in (ROOT / d).glob("*.md")):
    s = f.read_text()
    if s.startswith("---\n"):
        try:
            yaml.safe_load(re.match(r"^---\n(.*?)\n---\n", s, re.S).group(1))
        except yaml.YAMLError as e:
            err(f"{f.parent.name}/{f.name}: bad frontmatter: {e}")
    for t in re.findall(r"\]\(([^)\s]+)\)", s):
        if t.startswith(("http://", "https://", "mailto:")):
            continue
        path, _, anchor = t.partition("#")
        target = (f.parent / path).resolve() if path else f.resolve()
        if not target.exists():
            err(f"{f.parent.name}/{f.name}: broken link {t}")
        elif anchor and anchor not in slugs(target.read_text()):
            err(f"{f.parent.name}/{f.name}: bad anchor {t}")

print(f"{len(files)} issues, {len(story_ids)} epic stories")
if errors:
    print("\n".join(errors))
    sys.exit(1)
print("OK")
