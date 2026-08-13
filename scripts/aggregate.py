#!/usr/bin/env python3
"""Aggregate audit-swarm verdicts. Mode-agnostic: works for audit / research / plan.
- Parses the last ```json block from each verdict-*.md.
- Verifies every citation: file:line resolves against --repo; http(s) URLs are accepted as-is.
- Tallies consensus per claim id, flags SPLITs, downgrades verdicts with broken citations.
- Prints missed/gaps/risks. Exits non-zero if any SPLIT or broken citation remains (gate).
"""
import argparse, json, re, glob, os, sys, collections

ap = argparse.ArgumentParser()
ap.add_argument("--out", default="/tmp/swarm")
ap.add_argument("--repo", default=os.getcwd(),
                help="comma-separated root(s) to resolve file:line citations against")
args = ap.parse_args()
REPOS = [p for p in args.repo.split(",") if p]

CITE = re.compile(r"([\w./+-]+\.[A-Za-z0-9]+):(\d+)")      # file.ext:line
URL  = re.compile(r"https?://\S+")

_index = None
def _basename_index():
    """Map basename -> list of absolute paths under --repo (built once, skips vcs/vendor dirs)."""
    global _index
    if _index is not None:
        return _index
    _index = collections.defaultdict(list)
    skip = {".git", "node_modules", "dist", ".next", ".turbo", ".cache", "build", "coverage"}
    for repo in REPOS:
        for root, dirs, files in os.walk(repo):
            dirs[:] = [d for d in dirs if d not in skip]
            for fn in files:
                _index[fn].append(os.path.join(root, fn))
    return _index

def _has_line(full, ln):
    try:
        with open(full, "rb") as fh:
            return sum(1 for _ in fh) >= int(ln)
    except Exception:
        return False

def cite_ok(ev):
    """A citation resolves if a file:line exists (by exact path or unique-enough basename) or a URL is present."""
    urls = URL.findall(ev or "")
    fps  = CITE.findall(ev or "")
    if not urls and not fps:
        return (False, "no citation")
    for path, ln in fps:
        if os.path.isabs(path) and os.path.isfile(path) and _has_line(path, ln):
            return (True, "")
        for repo in REPOS:
            full = os.path.join(repo, path)
            if os.path.isfile(full) and _has_line(full, ln):
                return (True, "")
        # fallback: match by basename anywhere in the tree(s)
        for cand in _basename_index().get(os.path.basename(path), []):
            if _has_line(cand, ln):
                return (True, "basename")
    if urls:
        return (True, "url")           # offline: accept URL, do not fetch
    return (False, f"unresolved: {', '.join(p+':'+l for p,l in fps)}")

rows = collections.defaultdict(dict)   # id -> role -> (verdict, conf, evidence, cite_ok)
missed, roles, broken = [], [], []

for f in sorted(glob.glob(f"{args.out}/verdict-*.md")):
    role = os.path.basename(f)[len("verdict-"):-3]
    roles.append(role)
    blocks = re.findall(r"```json\s*(.*?)```", open(f, encoding="utf-8", errors="replace").read(), re.S)
    if not blocks:
        print(f"!! {role}: no json block"); continue
    try:
        data = json.loads(blocks[-1])
    except Exception as e:
        print(f"!! {role}: bad json ({e})"); continue
    for v in data.get("verdicts", []):
        ev = v.get("evidence", ""); ok, note = cite_ok(ev)
        vd = v.get("verdict", "?")
        rows[v.get("id", "?")][role] = (vd, v.get("confidence", "?"), ev, ok)
        if not ok and vd not in ("N/A", "-", "?", "UNKNOWN", "UNCERTAIN"):
            broken.append(f"{v.get('id','?')} [{role}] {vd}: {note}")
    for m in data.get("missed", []):
        missed.append((role, m.get("summary", ""), m.get("evidence", "")))

ids = sorted(rows.keys(), key=lambda s: (len(s), s))
ABSTAIN = {"N/A", "-", "?"}
splits = []

print("\n=== CONSENSUS (broken-citation verdicts marked *) ===\n")
hdr = "ID    | " + " | ".join(f"{r:10.10}" for r in roles) + " | CONSENSUS"
print(hdr); print("-"*len(hdr))
for cid in ids:
    cells, real = [], []
    for r in roles:
        if r in rows[cid]:
            vd, cf, ev, ok = rows[cid][r]
            cells.append(f"{(vd+('' if ok else '*')):10.10}")
            if vd not in ABSTAIN: real.append(vd)
        else:
            cells.append(f"{'-':10.10}")
    if not real:
        cons = "(uncovered)"
    else:
        c = collections.Counter(real)
        if len(c) == 1: cons = real[0]
        else: cons = f"SPLIT {dict(c)}"; splits.append(cid)
    print(f"{cid:5} | " + " | ".join(cells) + f" | {cons}")

print("\n=== EVIDENCE ===")
for cid in ids:
    for r in roles:
        if r in rows[cid] and rows[cid][r][0] not in ABSTAIN:
            vd, cf, ev, ok = rows[cid][r]
            print(f"{cid} [{r}/{vd}/{cf}]{'' if ok else ' [BROKEN CITE]'} {ev}")

print("\n=== MISSED / GAPS / RISKS (agent-proposed) ===")
for r, s, ev in missed:
    print(f"- [{r}] {s}  ({ev})")

if broken:
    print("\n=== BROKEN CITATIONS (verdicts NOT counted as proven) ===")
    for b in broken: print(" -", b)

print("\n=== GATE ===")
print(f"splits unresolved: {splits or 'none'}")
print(f"broken citations : {len(broken)}")
sys.exit(1 if (splits or broken) else 0)
