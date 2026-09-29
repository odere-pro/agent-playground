"""Regenerate the generated parts of the backlog from backlog.py and reuse_map.py.

Keeps each issue's hand-written body (Why, What, Out of scope, Acceptance criteria). Rewrites:
- each issue's frontmatter, ## Reuse, ## Dependencies, and ## References;
- issue file names, when an issue's index changes;
- `NNN ID` references and `NNN-ID-` file links in issues/, poc/, adr/, and tools/reuse_map.py;
- issues/000-plan.md, from plan-template.md.

Run: python3 tools/sync.py
"""
import collections
import re
from pathlib import Path

import backlog
from reuse_map import R

TOOLS = Path(__file__).resolve().parent
ROOT = TOOLS.parent
ISS = ROOT / "issues"
DAYS = {"S": 1, "M": 2.5, "L": 5}
MARKER = "<!-- BODY: replace this line with ## Why, ## What, ## Out of scope, ## Acceptance criteria -->"


def existing_files():
    out = {}
    for f in ISS.glob("[0-9][0-9][0-9]-*.md"):
        m = re.search(r"^epic_id: (\S+)$", f.read_text(), re.M)
        if m:
            out[m.group(1)] = f
    return out


def reuse_section(eid):
    if eid not in R:
        return ""
    lines = "\n".join(f"- {b}" for b in R[eid])
    return f"## Reuse\n\n{lines}\n- Details: [reuse analysis](../poc/010-reuse-analysis.md)\n\n"


def sync_issues(by_idx):
    existing, changed = existing_files(), []
    for it in by_idx.values():
        eid, target = it["epic_id"], ISS / it["file"]
        new = backlog.render(it, by_idx)
        old_file = existing.pop(eid, None)
        if old_file is None:
            target.write_text(new)
            changed.append(f"created {target.name}")
            continue
        cur = old_file.read_text()
        body = re.match(r"^---\n.*?\n---\n(.*?)\n## Dependencies\n", cur, re.S).group(1)
        body = re.sub(r"## Reuse\n.*?(?=## Out of scope\n)", "", body, flags=re.S)
        if MARKER not in body:
            body = body.replace("\n## Out of scope\n", "\n" + reuse_section(eid) + "## Out of scope\n", 1)
        fm = re.match(r"^(---\n.*?\n---\n)", new, re.S).group(1)
        out = fm + body + new[new.index("\n## Dependencies\n"):]
        if old_file != target:
            old_file.rename(target)
            changed.append(f"renamed {old_file.name} -> {target.name}")
        if out != cur:
            target.write_text(out)
            changed.append(f"updated {target.name}")
    for eid, f in existing.items():
        changed.append(f"WARNING: {f.name} ({eid}) is not in backlog.py ROWS; left untouched")
    return changed


def renumber_refs(by_idx):
    id2idx = {v["epic_id"]: k for k, v in by_idx.items()}
    alt = "|".join(re.escape(i) for i in sorted(id2idx, key=len, reverse=True))
    text_ref = re.compile(rf"\b\d{{3}} ({alt})\b")
    file_ref = re.compile(rf"\b\d{{3}}-({alt})-")
    changed = []
    files = [f for folder in ("issues", "poc", "adr") for f in (ROOT / folder).glob("*.md")]
    for f in files + [TOOLS / "reuse_map.py"]:
        s = f.read_text()
        n = text_ref.sub(lambda m: f"{id2idx[m.group(1)]:03d} {m.group(1)}", s)
        n = file_ref.sub(lambda m: f"{id2idx[m.group(1)]:03d}-{m.group(1)}-", n)
        if n != s:
            f.write_text(n)
            changed.append(f"refs {f.parent.name}/{f.name}")
    return changed


def critical_paths(m):
    best = {}
    for i in sorted(m):
        prev = max(m[i]["deps"], key=lambda d: best[d][0], default=None)
        best[i] = ((best[prev][0] if prev else 0) + DAYS[m[i]["size"]], (best[prev][1] if prev else []) + [i])
    return best


def build_plan(m):
    lab = lambda i: f'{i:03d} {m[i]["epic_id"]}'
    link = lambda i: f'[{lab(i)}]({m[i]["file"]})'
    id2 = {v["epic_id"]: k for k, v in m.items()}
    parts = []
    for w, name in backlog.WAVES.items():
        parts.append(f"### {name}\n\n| # | ID | Issue | Layer | Priority | Size | Depends on |\n| - | -- | ----- | ----- | -------- | ---- | ---------- |")
        for i in (v for v in m.values() if v["wave"] == w):
            deps = ", ".join(f'[{d:03d}]({m[d]["file"]})' for d in i["deps"]) or "—"
            parts.append(f'| {i["index"]:03d} | {i["epic_id"]} | [{i["title"]}]({i["file"]}) | {", ".join(i["layers"])} | {i["priority"]} | {i["size"]} | {deps} |')
        parts.append("")
    cov = ["| Epic phase | Issues |", "| ---------- | ------ |"]
    for k, (_, anchor, pname) in backlog.PHASES.items():
        ids = [i["index"] for i in m.values() if i["phase"] == k]
        cov.append(f"| [{pname}](../slm-agent-platform-epic-v3.md#{anchor}) | " + ", ".join(link(i) for i in ids) + " |")
    best = critical_paths(m)
    arrow = lambda p: " → ".join(lab(x) for x in p)
    core = [i for i in m if m[i]["wave"] != "W12"]
    end = max(core, key=lambda k: best[k][0])
    total = sum(DAYS[m[i]["size"]] for i in core)
    prio = collections.Counter(i["priority"] for i in m.values())
    values = {
        "TABLE": "\n".join(parts).rstrip(), "COVERAGE": "\n".join(cov),
        "MVP_PATH": arrow(best[id2["A-3"]][1]), "MVP_DAYS": f'{best[id2["A-3"]][0]:g}',
        "ALL_PATH": arrow(best[end][1]), "TOTAL_DAYS": f"{total:g}", "TOTAL_WEEKS": f"{total / 5:.0f}",
        "N_CHASSIS": sum("chassis" in i["layers"] for i in m.values()),
        "N_AGENT": sum("agent-profile" in i["layers"] for i in m.values()),
        "N_PLATFORM": sum(i["layers"] == ["platform"] for i in m.values()),
        **{p: prio[p] for p in ("P0", "P1", "P2", "P3")},
    }
    t = (TOOLS / "plan-template.md").read_text()
    for k, v in values.items():
        t = t.replace("{{" + k + "}}", str(v))
    t = re.sub(r"\{\{L:([^}]+)\}\}", lambda x: link(id2[x.group(1)]), t)

    def rng(w):
        ix = [i for i in m if m[i]["wave"] == w]
        return f"{min(ix):03d}" if len(ix) == 1 else f"{min(ix):03d}–{max(ix):03d}"

    t = re.sub(r"\{\{R:(W\d+)\}\}", lambda x: rng(x.group(1)), t)
    leftover = re.findall(r"\{\{[^}]+\}\}", t)
    assert not leftover, f"unfilled placeholders in plan-template.md: {leftover}"
    plan = ISS / "000-plan.md"
    changed = plan.read_text() != t if plan.exists() else True
    plan.write_text(t)
    return (["updated 000-plan.md"] if changed else []), total, arrow(best[end][1])


def main():
    by_idx = backlog.build()
    changes = sync_issues(by_idx)
    changes += renumber_refs(by_idx)
    plan_changes, total, path = build_plan(by_idx)
    changes += plan_changes
    print("\n".join(changes) or "no changes")
    print(f"{len(by_idx)} issues, {total:g} engineer-days (W0–W11), longest chain: {path}")


if __name__ == "__main__":
    main()
