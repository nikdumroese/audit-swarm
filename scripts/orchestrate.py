#!/usr/bin/env python3
"""audit-swarm orchestrator — loop discovery + debate until every claim is terminal, with a live
event stream while the agents run.

Each round:
  1. Run the swarm on the ACTIVE claims (unresolved carry their prior split verdicts as debate
     context; newly discovered claims are verified fresh). Discovery is on.
  2. Stream verdicts live as each agent finishes; aggregate + verify citations.
  3. Promote deduped `missed[]` items into new claims (dedup by citation overlap + token similarity).
  4. Freeze claims that reached a terminal verdict; keep debating the rest.
Stop when every claim is terminal and no new claims were discovered, or at --max-rounds.
"""
import argparse, os, re, sys, time, json, glob, shutil, subprocess
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import aggregate as agg

TERMINAL = {"audit": {"CONFIRMED", "REFUTED"},
            "research": {"SUPPORTED", "REFUTED"},
            "plan": {"SOUND", "BLOCKED"}}
HERE = os.path.dirname(os.path.abspath(__file__))
RUN_SWARM = os.path.join(HERE, "run-swarm.sh")
CLAIM_RE = re.compile(r"^\s*([A-Z]{1,3}\d+)\.\s+(.*)")

# ---- colour -------------------------------------------------------------------------------------
TTY = sys.stdout.isatty()
def c(s, code): return f"\033[{code}m{s}\033[0m" if TTY else s
GREEN, RED, YEL, DIM, CYAN, BOLD = "32", "31", "33", "2", "36", "1"
def vcolor(v):
    if v in ("CONFIRMED", "SUPPORTED", "SOUND"): return GREEN
    if v == "REFUTED": return RED
    if v in ("N/A", "-", "?"): return DIM
    return YEL
def paint(v): return c(v, vcolor(v))

# ---- dedup --------------------------------------------------------------------------------------
def norm(s): return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()
def sig_of(text):
    toks = {w for w in norm(text).split() if len(w) > 3}
    cites = {os.path.basename(p) for p, _ in agg.CITE.findall(text or "")}
    return (toks, cites)
def is_dup(sig, existing):
    t, cc = sig
    if not t:
        return True
    for et, ec in existing:
        jac = len(t & et) / max(1, len(t | et))
        if jac >= 0.6 or (bool(cc & ec) and jac >= 0.35):
            return True
    return False

# ---- claims parsing -----------------------------------------------------------------------------
def parse_claims(path):
    claims, order, cur = {}, [], None
    for line in open(path, encoding="utf-8"):
        if line.lstrip().startswith("#"):
            continue
        m = CLAIM_RE.match(line)
        if m:
            cur = m.group(1); claims[cur] = m.group(2).rstrip(); order.append(cur)
        elif cur and line.strip():
            claims[cur] += " " + line.strip()
        else:
            cur = None
    return order, claims

def write_round_file(path, mode, active, text, prior):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(f"# Round claims ({mode}). Verify or REFUTE each. Cite a real file:line or URL.\n\n")
        for cid in active:
            fh.write(f"{cid}. {text[cid]}\n")
            if prior.get(cid):
                fh.write("   PRIOR ROUND WAS UNRESOLVED — resolve it. Evidence so far:\n")
                for role, vd, ev in prior[cid]:
                    fh.write(f"     - {role} said {vd}: {ev}\n")
            fh.write("\n")

# ---- live parsing of a single agent file --------------------------------------------------------
def parse_file(path):
    try:
        txt = open(path, encoding="utf-8", errors="replace").read()
    except Exception:
        return None
    blocks = re.findall(r"```json\s*(.*?)```", txt, re.S)
    if not blocks:
        return None
    try:
        return json.loads(blocks[-1])
    except Exception:
        return None

def roles_of(path):
    out = []
    for line in open(path, encoding="utf-8"):
        if line.strip() and not line.lstrip().startswith("#"):
            out.append(line.split("\t", 1)[0].strip())
    return out

# ---- one round, with live stream or dashboard ---------------------------------------------------
def run_round(args, rnd, active, text, prior, repos, index):
    rdir = os.path.join(args.out, f"round-{rnd}")
    os.makedirs(rdir, exist_ok=True)
    write_round_file(os.path.join(rdir, "claims.md"), args.mode, active, text, prior)

    print(c(f"\n▶ round {rnd}: {len(active)} active claim(s)", BOLD))
    if args.dry_run:
        print(c(f"  [dry-run] reading existing verdicts in {rdir}", DIM))
    else:
        cmd = ["bash", RUN_SWARM, "--mode", args.mode, "--agent", args.agent,
               "--claims", os.path.join(rdir, "claims.md"), "--roles", args.roles,
               "--repo", args.repo, "--out", rdir, "--discover", "--thinking", args.thinking]
        if args.models:   cmd += ["--models", args.models]
        if args.provider: cmd += ["--provider", args.provider]
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        expected = roles_of(args.roles)
        if args.live and TTY:
            _dashboard(proc, rdir, rnd, active, expected, repos, index)
        else:
            _stream(proc, rdir, repos, index)

    rows, missed, roles = agg.load_round(rdir, repos, index)
    return agg.consensus(rows, roles), rows, missed, roles

# ---- plain streaming monitor (non-TTY / --no-live) ----------------------------------------------
def _stream(proc, rdir, repos, index):
    printed = set()
    while True:
        for f in sorted(glob.glob(os.path.join(rdir, "verdict-*.md"))):
            role = os.path.basename(f)[len("verdict-"):-3]
            if role in printed:
                continue
            data = parse_file(f)
            if data is None:
                continue
            printed.add(role); _print_agent(role, data, repos, index)
        if proc.poll() is not None:
            for f in sorted(glob.glob(os.path.join(rdir, "verdict-*.md"))):
                role = os.path.basename(f)[len("verdict-"):-3]
                if role in printed:
                    continue
                printed.add(role); data = parse_file(f)
                if data: _print_agent(role, data, repos, index)
                else: print(c(f"  ✗ {role}: no verdict block", RED))
            break
        time.sleep(0.6)

def _print_agent(role, data, repos, index):
    print(c(f"  ✔ {role}", BOLD))
    for v in data.get("verdicts", []):
        vd = v.get("verdict", "?")
        if vd in ("N/A", "-", "?"):
            continue
        ok, _ = agg.cite_ok(v.get("evidence", ""), repos, index)
        print(f"      {v.get('id','?'):5} {paint(vd):20} {c('✓', GREEN) if ok else c('✗cite', RED)}")
    for m in data.get("missed", []):
        print("      " + c(f"✨ discovered: {m.get('summary','')[:100]}", CYAN))

# ---- live in-TTY dashboard (ANSI frame redraw) --------------------------------------------------
ANSI_RE = re.compile(r"\033\[[0-9;?]*m")
SPIN = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
def _vlen(s): return len(ANSI_RE.sub("", s))
def _vtrunc(s, w):
    if _vlen(s) <= w: return s
    return ANSI_RE.sub("", s)[:max(0, w - 1)] + "…"

def _dashboard(proc, rdir, rnd, active, expected, repos, index):
    votes = {cid: [] for cid in active}     # cid -> [(role, verdict, ok)]
    done, discoveries = {}, []              # role -> verdict count ; list of summaries
    seen = set()
    cols = max(40, shutil.get_terminal_size((100, 30)).columns)
    start = time.time(); prev_n = 0; tick = 0; ended_at = None
    sys.stdout.write("\033[?25l")           # hide cursor
    try:
        while True:
            tick += 1
            nfiles = len(glob.glob(os.path.join(rdir, "verdict-*.md")))
            for f in sorted(glob.glob(os.path.join(rdir, "verdict-*.md"))):
                role = os.path.basename(f)[len("verdict-"):-3]
                if role in seen:
                    continue
                data = parse_file(f)
                if data is None:
                    continue
                seen.add(role); n = 0
                for v in data.get("verdicts", []):
                    vd = v.get("verdict", "?")
                    if vd in ("N/A", "-", "?"):
                        continue
                    ok, _ = agg.cite_ok(v.get("evidence", ""), repos, index)
                    if v.get("id") in votes:
                        votes[v["id"]].append((role, vd, ok)); n += 1
                for m in data.get("missed", []):
                    discoveries.append((role, m.get("summary", "")))
                done[role] = n
            prev_n = _paint(_frame(rnd, active, expected, votes, done, discoveries,
                                   start, tick, cols), prev_n)
            if proc.poll() is not None:
                ended_at = ended_at or time.time()
                # exit once every file is parsed, or after a short grace period post-exit
                if len(seen) >= nfiles or time.time() - ended_at > 4:
                    break
            time.sleep(0.5)
    finally:
        sys.stdout.write("\033[?25h")       # restore cursor
        sys.stdout.flush()
    # persist discoveries into scrollback (frame is ephemeral)
    for role, s in discoveries:
        print("  " + c(f"✨ [{role}] {s[:110]}", CYAN))

def _frame(rnd, active, expected, votes, done, discoveries, start, tick, cols):
    el = int(time.time() - start); sp = SPIN[tick % len(SPIN)]
    L = [c(f"audit-swarm · round {rnd} · {el//60:02d}:{el%60:02d}", BOLD)]
    # agents line
    ag = []
    for r in expected:
        if r in done: ag.append(c(f"✔ {r}({done[r]})", GREEN))
        else:         ag.append(c(f"{sp} {r}", YEL))
    L.append("agents: " + "  ".join(ag))
    # claims grid: one glyph per completed vote (green=proven/red=refuted/yellow=other)
    L.append(c("claims: ", DIM) + c("● proven ", GREEN) + c("● refuted ", RED) + c("● unresolved ", YEL) + c("· pending", DIM))
    glyph = {}
    for cid in active:
        g = ""
        for _, vd, ok in votes[cid]:
            col = GREEN if vd in ("CONFIRMED", "SUPPORTED", "SOUND") else RED if vd == "REFUTED" else YEL
            g += c("●", col)
        if not g: g = c("·", DIM)
        glyph[cid] = f"{cid} {g}"
    per = max(1, cols // 16)
    row = []
    for i, cid in enumerate(active):
        row.append(glyph[cid].ljust(16 + (len(glyph[cid]) - _vlen(glyph[cid]))))
        if len(row) == per:
            L.append("  " + "".join(row)); row = []
    if row: L.append("  " + "".join(row))
    # tallies
    prov = sum(1 for cid in active if votes[cid] and all(v in ("CONFIRMED","SUPPORTED","SOUND") for _,v,_ in votes[cid]))
    refu = sum(1 for cid in active if votes[cid] and all(v == "REFUTED" for _,v,_ in votes[cid]))
    L.append(f"totals: {c(str(prov)+' proven',GREEN)}  {c(str(refu)+' refuted',RED)}  "
             f"{c(str(len(discoveries))+' discovered',CYAN)}")
    if discoveries:
        L.append(c("latest ✨ " + discoveries[-1][1][:cols - 12], DIM))
    return [_vtrunc(x, cols) for x in L]

def _paint(lines, prev_n):
    out = []
    if prev_n:
        out.append(f"\033[{prev_n}A")
    out.append("\033[J")
    out.append("\n".join(lines) + "\n")
    sys.stdout.write("".join(out)); sys.stdout.flush()
    return len(lines)

# ---- report -------------------------------------------------------------------------------------
def render_table(order, status, prev):
    print(c("\n── consensus ──", BOLD))
    for cid in order:
        st = status[cid]
        base = st.split()[0] if st.startswith("SPLIT") else st
        arrow = ""
        if prev.get(cid) and prev[cid] != st:
            arrow = c(f"   ({prev[cid]} → {st})", DIM)
        print(f"  {cid:5} {paint(base):20}{arrow}")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--claims", required=True); ap.add_argument("--roles", required=True)
    ap.add_argument("--repo", default=os.getcwd()); ap.add_argument("--out", default="/tmp/swarm-loop")
    ap.add_argument("--mode", default="audit", choices=list(TERMINAL))
    ap.add_argument("--agent", default="pi"); ap.add_argument("--models", default="")
    ap.add_argument("--provider", default=""); ap.add_argument("--thinking", default="high")
    ap.add_argument("--max-rounds", type=int, default=5)
    ap.add_argument("--max-discovery", type=int, default=2)
    ap.add_argument("--live", dest="live", action="store_true", default=True)
    ap.add_argument("--no-live", dest="live", action="store_false")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    repos = [p for p in args.repo.split(",") if p]
    index = agg.build_index(repos)
    order, text = parse_claims(args.claims)
    status = {cid: "PENDING" for cid in order}
    prior, prev = {}, {}
    sigs = [sig_of(text[c]) for c in order]        # dedup signatures (originals + accepted discoveries)
    term = TERMINAL[args.mode]; proven_v = list(term - {"REFUTED"})[0]
    disc_gen = disc_count = 0

    rnd = 0
    while rnd < args.max_rounds:
        active = [c for c in order if status[c] not in term]
        if not active:
            break
        cons, rows, missed, roles = run_round(args, rnd, active, text, prior, repos, index)

        prev = dict(status); prior = {}
        for cid in active:
            if cid not in cons:
                continue
            st = cons[cid]["consensus"]
            status[cid] = st
            if cons[cid]["split"] or st not in term:
                prior[cid] = [(r, rows[cid][r][0], rows[cid][r][2]) for r in roles if r in rows[cid]]

        new = []
        if disc_gen < args.max_discovery:
            for role, summ, ev in missed:
                sig = sig_of((summ or "") + " " + (ev or ""))
                if is_dup(sig, sigs):
                    continue
                sigs.append(sig); disc_count += 1
                nid = f"D{disc_count}"
                text[nid] = f"{summ} (proposed by {role}; evidence: {ev})"
                order.append(nid); status[nid] = "PENDING"; new.append(nid)
            if new:
                disc_gen += 1
                print(c(f"  → promoted {len(new)} discovery→claim: {', '.join(new)}", CYAN))

        render_table(order, status, prev)
        unresolved = [c for c in order if status[c] not in term]
        rnd += 1
        if not unresolved and not new:
            print(c(f"\n✅ converged after {rnd} round(s)", GREEN)); break
    else:
        print(c(f"\n⏹ stopped at max-rounds ({args.max_rounds})", YEL))

    proven  = [c for c in order if status[c] == proven_v]
    refuted = [c for c in order if status[c] == "REFUTED"]
    unresolved = [c for c in order if c not in proven and c not in refuted]
    report = os.path.join(args.out, "orchestrate-report.md")
    with open(report, "w", encoding="utf-8") as fh:
        fh.write(f"# audit-swarm loop report ({args.mode})\n\n")
        fh.write(f"Rounds run: {rnd} (max {args.max_rounds}). "
                 f"Converged: {'yes' if not unresolved else 'NO'}.\n\n")
        for title, ids in (("PROVEN", proven), ("REFUTED", refuted), ("UNRESOLVED", unresolved)):
            fh.write(f"## {title} ({len(ids)})\n\n")
            for cid in ids:
                fh.write(f"- **{cid}** [{status[cid]}] {text[cid]}\n")
            fh.write("\n")
    print(c(f"\nPROVEN {len(proven)}  REFUTED {len(refuted)}  UNRESOLVED {len(unresolved)}", BOLD))
    print(f"report → {report}")
    sys.exit(0 if not unresolved else 1)

if __name__ == "__main__":
    main()
