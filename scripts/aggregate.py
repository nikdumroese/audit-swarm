#!/usr/bin/env python3
"""Aggregate audit-swarm verdicts. Mode-agnostic (audit / research / plan).

Importable API (used by orchestrate.py):
  build_index(repos) -> {basename: [abs_path]}
  cite_ok(evidence, repos, index) -> (bool, note)
  load_round(out_dir, repos, index) -> (rows, missed, roles)
  consensus(rows, roles) -> {id: {"consensus": str, "split": bool, "verdicts": [..]}}

CLI: prints the consensus table, evidence, missed items, broken citations; exits non-zero if any
SPLIT or broken citation remains (gate).
"""
import argparse, json, re, glob, os, sys, collections

CITE = re.compile(r"([\w./+-]+\.[A-Za-z0-9]+):(\d+)")   # file.ext:line
URL  = re.compile(r"https?://\S+")
ABSTAIN = {"N/A", "-", "?"}


def build_index(repos):
    idx = collections.defaultdict(list)
    skip = {".git", "node_modules", "dist", ".next", ".turbo", ".cache", "build", "coverage"}
    for repo in repos:
        for root, dirs, files in os.walk(repo):
            dirs[:] = [d for d in dirs if d not in skip]
            for fn in files:
                idx[fn].append(os.path.join(root, fn))
    return idx


def _has_line(full, ln):
    try:
        with open(full, "rb") as fh:
            return sum(1 for _ in fh) >= int(ln)
    except Exception:
        return False


def cite_ok(ev, repos, index):
    urls = URL.findall(ev or "")
    fps = CITE.findall(ev or "")
    if not urls and not fps:
        return (False, "no citation")
    for path, ln in fps:
        if os.path.isabs(path) and _has_line(path, ln):
            return (True, "")
        for repo in repos:
            if _has_line(os.path.join(repo, path), ln):
                return (True, "")
        for cand in index.get(os.path.basename(path), []):
            if _has_line(cand, ln):
                return (True, "basename")
    if urls:
        return (True, "url")
    return (False, "unresolved: " + ", ".join(p + ":" + l for p, l in fps))


def load_round(out_dir, repos, index):
    rows = collections.defaultdict(dict)
    missed, roles = [], []
    for f in sorted(glob.glob(os.path.join(out_dir, "verdict-*.md"))):
        role = os.path.basename(f)[len("verdict-"):-3]
        roles.append(role)
        blocks = re.findall(r"```json\s*(.*?)```",
                            open(f, encoding="utf-8", errors="replace").read(), re.S)
        if not blocks:
            print(f"!! {role}: no json block", file=sys.stderr); continue
        try:
            data = json.loads(blocks[-1])
        except Exception as e:
            print(f"!! {role}: bad json ({e})", file=sys.stderr); continue
        for v in data.get("verdicts", []):
            ev = v.get("evidence", ""); ok, _ = cite_ok(ev, repos, index)
            rows[v.get("id", "?")][role] = (v.get("verdict", "?"), v.get("confidence", "?"), ev, ok)
        for m in data.get("missed", []):
            missed.append((role, m.get("summary", ""), m.get("evidence", "")))
    return rows, missed, roles


def consensus(rows, roles):
    out = {}
    for cid in rows:
        real = []
        for r in roles:
            if r in rows[cid]:
                vd, cf, ev, ok = rows[cid][r]
                # a verdict with a broken citation does not count toward consensus
                if vd not in ABSTAIN and ok:
                    real.append(vd)
        if not real:
            out[cid] = {"consensus": "(uncovered)", "split": False, "verdicts": real}
        else:
            c = collections.Counter(real)
            if len(c) == 1:
                out[cid] = {"consensus": real[0], "split": False, "verdicts": real}
            else:
                out[cid] = {"consensus": "SPLIT " + str(dict(c)), "split": True, "verdicts": real}
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="/tmp/swarm")
    ap.add_argument("--repo", default=os.getcwd(),
                    help="comma-separated root(s) to resolve file:line citations against")
    args = ap.parse_args()
    repos = [p for p in args.repo.split(",") if p]
    index = build_index(repos)
    rows, missed, roles = load_round(args.out, repos, index)
    cons = consensus(rows, roles)
    ids = sorted(rows.keys(), key=lambda s: (len(s), s))

    print("\n=== CONSENSUS (broken-citation verdicts marked *) ===\n")
    hdr = "ID    | " + " | ".join(f"{r:10.10}" for r in roles) + " | CONSENSUS"
    print(hdr); print("-" * len(hdr))
    broken, splits = [], []
    for cid in ids:
        cells = []
        for r in roles:
            if r in rows[cid]:
                vd, cf, ev, ok = rows[cid][r]
                cells.append(f"{(vd + ('' if ok else '*')):10.10}")
                if not ok and vd not in ABSTAIN:
                    broken.append(f"{cid} [{r}] {vd}")
            else:
                cells.append(f"{'-':10.10}")
        print(f"{cid:5} | " + " | ".join(cells) + f" | {cons[cid]['consensus']}")
        if cons[cid]["split"]:
            splits.append(cid)

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
        print("\n=== BROKEN CITATIONS (not counted toward consensus) ===")
        for b in broken:
            print(" -", b)

    print("\n=== GATE ===")
    print(f"splits unresolved: {splits or 'none'}")
    print(f"broken citations : {len(broken)}")
    sys.exit(1 if (splits or broken) else 0)


if __name__ == "__main__":
    main()
