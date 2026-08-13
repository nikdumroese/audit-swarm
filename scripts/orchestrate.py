#!/usr/bin/env python3
"""audit-swarm orchestrator — loop discovery + debate until every claim is terminal.

Each round:
  1. Run the swarm on the ACTIVE claims (unresolved carry their prior split verdicts as debate
     context; newly discovered claims are verified fresh). Discovery is on, so agents also propose
     new problems in `missed[]`.
  2. Aggregate + verify citations.
  3. Promote deduped `missed[]` items into new claims (bounded by --max-discovery generations).
  4. Freeze claims that reached a terminal verdict; keep debating the rest.
Stop when every claim is terminal and no new claims were discovered, or at --max-rounds.

Terminal verdicts by mode:
  audit    CONFIRMED | REFUTED
  research SUPPORTED | REFUTED
  plan     SOUND     | BLOCKED
Anything else (PARTIAL, RUNTIME_ONLY, MIXED, RISKY, UNKNOWN, SPLIT) is non-terminal and is debated
again; if it survives to --max-rounds it is reported as unresolved with its final state.
"""
import argparse, os, re, sys, subprocess, collections
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import aggregate as agg

TERMINAL = {
    "audit":    {"CONFIRMED", "REFUTED"},
    "research": {"SUPPORTED", "REFUTED"},
    "plan":     {"SOUND", "BLOCKED"},
}
HERE = os.path.dirname(os.path.abspath(__file__))
RUN_SWARM = os.path.join(HERE, "run-swarm.sh")
CLAIM_RE = re.compile(r"^\s*([A-Z]{1,3}\d+)\.\s+(.*)")


def parse_claims(path):
    """Parse 'C1. text ...' blocks (continuation lines until next id or blank-gap); skip # comments."""
    claims, cur = {}, None
    order = []
    for line in open(path, encoding="utf-8"):
        if line.lstrip().startswith("#"):
            continue
        m = CLAIM_RE.match(line)
        if m:
            cur = m.group(1)
            claims[cur] = m.group(2).rstrip()
            order.append(cur)
        elif cur and line.strip():
            claims[cur] += " " + line.strip()
        else:
            cur = None
    return order, claims


def norm(s):
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def write_round_file(path, mode, active, claim_text, prior):
    """active: list of ids to (re)evaluate. prior: {id: [(role,verdict,ev)]} for debate context."""
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(f"# Round claims ({mode}). Verify or REFUTE each. Cite a real file:line or URL.\n")
        fh.write("# For claims marked SPLIT, resolve the disagreement; address the opposing evidence.\n\n")
        for cid in active:
            fh.write(f"{cid}. {claim_text[cid]}\n")
            if cid in prior and prior[cid]:
                fh.write("   PRIOR ROUND WAS UNRESOLVED — resolve it. Evidence so far:\n")
                for role, vd, ev in prior[cid]:
                    fh.write(f"     - {role} said {vd}: {ev}\n")
            fh.write("\n")


def run_round(args, rnd, active, claim_text, prior, repos, index):
    rdir = os.path.join(args.out, f"round-{rnd}")
    os.makedirs(rdir, exist_ok=True)
    rfile = os.path.join(rdir, "claims.md")
    write_round_file(rfile, args.mode, active, claim_text, prior)
    if not args.dry_run:
        cmd = ["bash", RUN_SWARM, "--mode", args.mode, "--agent", args.agent,
               "--claims", rfile, "--roles", args.roles, "--repo", args.repo,
               "--out", rdir, "--discover", "--thinking", args.thinking]
        if args.models:   cmd += ["--models", args.models]
        if args.provider: cmd += ["--provider", args.provider]
        print(f"\n### round {rnd}: {len(active)} active claim(s) -> {rdir}", file=sys.stderr)
        subprocess.run(cmd, check=False)
    else:
        # dry-run: reuse pre-existing verdict-*.md already sitting in rdir (or seeded)
        print(f"\n### [dry-run] round {rnd}: reading existing verdicts in {rdir}", file=sys.stderr)
    rows, missed, roles = agg.load_round(rdir, repos, index)
    return agg.consensus(rows, roles), rows, missed, roles


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--claims", required=True)
    ap.add_argument("--roles", required=True)
    ap.add_argument("--repo", default=os.getcwd())
    ap.add_argument("--out", default="/tmp/swarm-loop")
    ap.add_argument("--mode", default="audit", choices=list(TERMINAL))
    ap.add_argument("--agent", default="pi")
    ap.add_argument("--models", default="")
    ap.add_argument("--provider", default="")
    ap.add_argument("--thinking", default="high")
    ap.add_argument("--max-rounds", type=int, default=5)
    ap.add_argument("--max-discovery", type=int, default=2,
                    help="stop promoting new claims after this many discovery generations")
    ap.add_argument("--dry-run", action="store_true",
                    help="do not spawn agents; read existing round-*/verdict-*.md (for testing)")
    args = ap.parse_args()

    repos = [p for p in args.repo.split(",") if p]
    index = agg.build_index(repos)
    order, text = parse_claims(args.claims)
    status = {cid: "PENDING" for cid in order}      # terminal verdict string or PENDING/UNRESOLVED
    prior = {}                                       # id -> [(role, verdict, evidence)]
    seen = {norm(text[c]) for c in order}            # discovery dedup signatures
    disc_gen, disc_count = 0, 0
    term = TERMINAL[args.mode]

    rnd = 0
    while rnd < args.max_rounds:
        active = [c for c in order if status[c] not in term and status[c] != "REFUTED"]
        if not active:
            break
        cons, rows, missed, roles = run_round(args, rnd, active, text, prior, repos, index)

        # update status + carry debate context for the unresolved
        prior = {}
        for cid in active:
            if cid not in cons:
                continue
            c = cons[cid]
            if not c["split"] and c["consensus"] in term:
                status[cid] = c["consensus"]
            else:
                status[cid] = c["consensus"]            # keep non-terminal state visible
                prior[cid] = [(r, rows[cid][r][0], rows[cid][r][2]) for r in roles if r in rows[cid]]

        # discovery: promote deduped missed -> new claims (bounded generations)
        new = []
        if disc_gen < args.max_discovery:
            for role, summ, ev in missed:
                sig = norm(summ)[:80]
                if not sig or sig in seen:
                    continue
                seen.add(sig)
                disc_count += 1
                nid = f"D{disc_count}"
                text[nid] = f"{summ} (proposed by {role}; evidence: {ev})"
                order.append(nid); status[nid] = "PENDING"; new.append(nid)
            if new:
                disc_gen += 1
                print(f"### discovered {len(new)} new claim(s): {', '.join(new)}", file=sys.stderr)

        unresolved = [c for c in order if status[c] not in term and status[c] != "REFUTED"]
        if not unresolved and not new:
            rnd += 1
            print(f"### converged after {rnd} round(s)", file=sys.stderr)
            break
        rnd += 1

    # final report
    report = os.path.join(args.out, "orchestrate-report.md")
    proven   = [c for c in order if status[c] == (list(term - {"REFUTED"})[0])]
    refuted  = [c for c in order if status[c] == "REFUTED"]
    unresolved = [c for c in order if c not in proven and c not in refuted]
    with open(report, "w", encoding="utf-8") as fh:
        fh.write(f"# audit-swarm loop report ({args.mode})\n\n")
        fh.write(f"Rounds run: {rnd} (max {args.max_rounds}). "
                 f"Converged: {'yes' if not unresolved else 'NO — hit cap or non-binary'}.\n\n")
        for title, ids in (("PROVEN", proven), ("REFUTED", refuted), ("UNRESOLVED", unresolved)):
            fh.write(f"## {title} ({len(ids)})\n\n")
            for cid in ids:
                fh.write(f"- **{cid}** [{status[cid]}] {text[cid]}\n")
            fh.write("\n")
    print(open(report).read())
    print(f"\nreport -> {report}")
    sys.exit(0 if not unresolved else 1)


if __name__ == "__main__":
    main()
