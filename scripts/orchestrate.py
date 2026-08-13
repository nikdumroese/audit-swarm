#!/usr/bin/env python3
"""audit-swarm orchestrator — loop discovery + debate until every claim is terminal.

Renders a designed, append-only progress view that streams live inside agent harnesses (pi / Claude
Code / Codex) as well as in a plain terminal. A real TTY additionally gets an in-place dashboard.
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
ANSI_RE = re.compile(r"\033\[[0-9;?]*m")
SPIN = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
GREEN, RED, YEL, DIM, CYAN, BOLD = "32", "31", "33", "2", "36", "1"

# ---- colour / output ----------------------------------------------------------------------------
TTY = sys.stdout.isatty()
def _auto_color():
    # Conservative: colour only where we know SGR is rendered. The Unicode layout still looks good
    # without colour, so harnesses default to monochrome (no risk of raw escape codes). Opt in with
    # --color always if your harness renders ANSI (pi/Claude Code in a real terminal do).
    if os.environ.get("NO_COLOR"): return False
    if os.environ.get("FORCE_COLOR"): return True
    return TTY
COLOR = _auto_color()
def c(s, code): return f"\033[{code}m{s}\033[0m" if COLOR else s
def vcolor(v):
    if v in ("CONFIRMED", "SUPPORTED", "SOUND"): return GREEN
    if v == "REFUTED": return RED
    if v in ("N/A", "-", "?"): return DIM
    return YEL
def paint(v): return c(v, vcolor(v))
def vlen(s): return len(ANSI_RE.sub("", s))
def vtrunc(s, w):
    return s if vlen(s) <= w else ANSI_RE.sub("", s)[:max(0, w - 1)] + "…"

_PROG = None
def emit(msg="", stdout=True):
    if stdout:
        print(msg, flush=True)
    if _PROG is not None:
        _PROG.write(ANSI_RE.sub("", msg) + "\n"); _PROG.flush()

def gstat(st):
    b = st.split()[0]
    if b in ("CONFIRMED", "SUPPORTED", "SOUND"): return c("✓", GREEN)
    if b == "REFUTED": return c("✗", RED)
    if b == "PENDING": return c("·", DIM)
    return c("•", YEL)

def box(lines, pad=1):
    w = max(vlen(x) for x in lines)
    out = ["╭" + "─" * (w + pad * 2) + "╮"]
    for x in lines:
        out.append("│" + " " * pad + x + " " * (w - vlen(x)) + " " * pad + "│")
    out.append("╰" + "─" * (w + pad * 2) + "╯")
    return out

# ---- claims -------------------------------------------------------------------------------------
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

# ---- dedup --------------------------------------------------------------------------------------
def norm(s): return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()
def sig_of(text):
    toks = {w for w in norm(text).split() if len(w) > 3}
    cites = {os.path.basename(p) for p, _ in agg.CITE.findall(text or "")}
    return (toks, cites)
def is_dup(sig, existing):
    t, cc = sig
    if not t: return True
    for et, ec in existing:
        jac = len(t & et) / max(1, len(t | et))
        if jac >= 0.6 or (bool(cc & ec) and jac >= 0.35):
            return True
    return False

# ---- one agent's file ---------------------------------------------------------------------------
def parse_file(path):
    try:
        txt = open(path, encoding="utf-8", errors="replace").read()
    except Exception:
        return None
    blocks = re.findall(r"```json\s*(.*?)```", txt, re.S)
    if not blocks: return None
    try: return json.loads(blocks[-1])
    except Exception: return None

def roles_of(path):
    out = []
    for line in open(path, encoding="utf-8"):
        if line.strip() and not line.lstrip().startswith("#"):
            out.append(line.split("\t", 1)[0].strip())
    return out

def agent_counts(data, repos, index):
    cnt = {"ok": 0, "no": 0, "op": 0}
    for v in data.get("verdicts", []):
        vd = v.get("verdict", "?")
        if vd in ("N/A", "-", "?"): continue
        if vd in ("CONFIRMED", "SUPPORTED", "SOUND"): cnt["ok"] += 1
        elif vd == "REFUTED": cnt["no"] += 1
        else: cnt["op"] += 1
    return cnt

# ---- round --------------------------------------------------------------------------------------
def run_round(args, rnd, active, text, prior, repos, index):
    rdir = os.path.join(args.out, f"round-{rnd}")
    os.makedirs(rdir, exist_ok=True)
    write_round_file(os.path.join(rdir, "claims.md"), args.mode, active, text, prior)

    bar = "━" * max(6, 44 - len(str(rnd)))
    emit(c(f"\n┏━ round {rnd} · {len(active)} active {bar}", BOLD))
    if args.dry_run:
        emit(c("  [dry-run] reading existing verdicts", DIM))
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

def _stream(proc, rdir, repos, index):
    printed = set()
    while True:
        for f in sorted(glob.glob(os.path.join(rdir, "verdict-*.md"))):
            role = os.path.basename(f)[len("verdict-"):-3]
            if role in printed: continue
            data = parse_file(f)
            if data is None: continue
            printed.add(role); _agent_line(role, data, repos, index)
        if proc.poll() is not None:
            for f in sorted(glob.glob(os.path.join(rdir, "verdict-*.md"))):
                role = os.path.basename(f)[len("verdict-"):-3]
                if role in printed:
                    continue
                printed.add(role); data = parse_file(f)
                if data: _agent_line(role, data, repos, index)
                else: emit(c(f"  ✗ {role}: no verdict block", RED))
            break
        time.sleep(0.5)

def _agent_line(role, data, repos, index):
    n = agent_counts(data, repos, index); d = data.get("missed", [])
    emit(f"  {c('▸', BOLD)} {role:9} "
         f"{c('✓'+str(n['ok']), GREEN)} {c('✗'+str(n['no']), RED)} {c('•'+str(n['op']), YEL)}"
         f"   {c('✨'+str(len(d)), CYAN) if d else c('✨0', DIM)}")
    for m in d:
        emit("      " + c("✨ " + m.get("summary", "")[:96], CYAN))

def render_round(order, status, prev, term, proven_v, cols):
    emit(c("  ┈ consensus", DIM))
    cells = [f"{gstat(status[c_])} {c_}" for c_ in order]
    per = max(4, (cols - 4) // 9)
    for i in range(0, len(cells), per):
        emit("    " + "  ".join(cells[i:i + per]))
    prov = sum(1 for c_ in order if status[c_] == proven_v)
    refu = sum(1 for c_ in order if status[c_] == "REFUTED")
    openn = len(order) - prov - refu
    emit(f"  {c('✓ '+str(prov)+' proven', GREEN)}   {c('✗ '+str(refu)+' refuted', RED)}   "
         f"{c('• '+str(openn)+' open', YEL)}")
    tr = [f"{cid} {gstat(prev[cid])}→{gstat(status[cid])}"
          for cid in order if prev.get(cid) and prev[cid] != status[cid] and prev[cid] != "PENDING"]
    if tr:
        emit(c("  resolved: ", DIM) + "  ".join(tr))

# ---- in-TTY dashboard ---------------------------------------------------------------------------
def _dashboard(proc, rdir, rnd, active, expected, repos, index):
    votes = {cid: [] for cid in active}; done, disc, seen = {}, [], set()
    cols = max(40, shutil.get_terminal_size((100, 30)).columns)
    start = time.time(); prev_n = 0; tick = 0; ended_at = None
    sys.stdout.write("\033[?25l")
    try:
        while True:
            tick += 1
            nfiles = len(glob.glob(os.path.join(rdir, "verdict-*.md")))
            for f in sorted(glob.glob(os.path.join(rdir, "verdict-*.md"))):
                role = os.path.basename(f)[len("verdict-"):-3]
                if role in seen: continue
                data = parse_file(f)
                if data is None: continue
                seen.add(role); n = 0
                for v in data.get("verdicts", []):
                    vd = v.get("verdict", "?")
                    if vd in ("N/A", "-", "?"): continue
                    ok, _ = agg.cite_ok(v.get("evidence", ""), repos, index)
                    if v.get("id") in votes: votes[v["id"]].append((role, vd, ok)); n += 1
                for m in data.get("missed", []): disc.append((role, m.get("summary", "")))
                done[role] = n
            prev_n = _paint(_frame(rnd, active, expected, votes, done, disc, start, tick, cols), prev_n)
            if proc.poll() is not None:
                ended_at = ended_at or time.time()
                if len(seen) >= nfiles or time.time() - ended_at > 4: break
            time.sleep(0.5)
    finally:
        sys.stdout.write("\033[?25h"); sys.stdout.flush()
    for role, n in done.items(): emit(c(f"  ▸ {role} · {n} verdicts", BOLD), stdout=False)
    for role, s in disc: emit("  " + c(f"✨ [{role}] {s[:110]}", CYAN))

def _frame(rnd, active, expected, votes, done, disc, start, tick, cols):
    el = int(time.time() - start); sp = SPIN[tick % len(SPIN)]
    L = [c(f"audit-swarm · round {rnd} · {el//60:02d}:{el%60:02d}", BOLD)]
    ag = [c(f"✔ {r}({done[r]})", GREEN) if r in done else c(f"{sp} {r}", YEL) for r in expected]
    L.append("agents: " + "  ".join(ag))
    L.append(c("claims: ", DIM) + c("● proven ", GREEN) + c("● refuted ", RED)
             + c("● unresolved ", YEL) + c("· pending", DIM))
    glyph = {}
    for cid in active:
        g = ""
        for _, vd, ok in votes[cid]:
            col = GREEN if vd in ("CONFIRMED", "SUPPORTED", "SOUND") else RED if vd == "REFUTED" else YEL
            g += c("●", col)
        glyph[cid] = f"{cid} {g or c('·', DIM)}"
    per = max(1, cols // 16); row = []
    for cid in active:
        cell = glyph[cid]; row.append(cell + " " * max(0, 16 - vlen(cell)))
        if len(row) == per: L.append("  " + "".join(row)); row = []
    if row: L.append("  " + "".join(row))
    prov = sum(1 for cid in active if votes[cid] and all(v in ("CONFIRMED", "SUPPORTED", "SOUND") for _, v, _ in votes[cid]))
    refu = sum(1 for cid in active if votes[cid] and all(v == "REFUTED" for _, v, _ in votes[cid]))
    L.append(f"totals: {c(str(prov)+' proven', GREEN)}  {c(str(refu)+' refuted', RED)}  "
             f"{c(str(len(disc))+' discovered', CYAN)}")
    if disc: L.append(c("latest ✨ " + disc[-1][1][:cols - 12], DIM))
    return [vtrunc(x, cols) for x in L]

def _paint(lines, prev_n):
    out = [f"\033[{prev_n}A"] if prev_n else []
    out += ["\033[J", "\n".join(lines) + "\n"]
    sys.stdout.write("".join(out)); sys.stdout.flush()
    return len(lines)

# ---- main ---------------------------------------------------------------------------------------
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
    ap.add_argument("--color", choices=["auto", "always", "never"], default="auto")
    ap.add_argument("--progress-file", default="")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    global _PROG, COLOR
    if args.color == "always": COLOR = True
    elif args.color == "never": COLOR = False
    try: sys.stdout.reconfigure(line_buffering=True)
    except Exception: pass
    os.makedirs(args.out, exist_ok=True)
    prog_path = args.progress_file or os.path.join(args.out, "progress.log")
    _PROG = open(prog_path, "a", encoding="utf-8")

    repos = [p for p in args.repo.split(",") if p]
    index = agg.build_index(repos)
    order, text = parse_claims(args.claims)
    cols = max(40, shutil.get_terminal_size((100, 30)).columns)
    for ln in box([c(f"audit-swarm · {args.mode} · {len(order)} claims", BOLD),
                   c(f"agent {args.agent}" + (f" · models {args.models}" if args.models else ""), DIM)]):
        emit(ln)
    emit(c(f"live log → tail -f {prog_path}", DIM))

    status = {cid: "PENDING" for cid in order}
    prior, prev = {}, {}
    sigs = [sig_of(text[c_]) for c_ in order]
    term = TERMINAL[args.mode]; proven_v = list(term - {"REFUTED"})[0]
    disc_gen = disc_count = 0

    rnd = 0
    while rnd < args.max_rounds:
        active = [c_ for c_ in order if status[c_] not in term]
        if not active: break
        cons, rows, missed, roles = run_round(args, rnd, active, text, prior, repos, index)

        prev = dict(status); prior = {}
        for cid in active:
            if cid not in cons: continue
            st = cons[cid]["consensus"]; status[cid] = st
            if cons[cid]["split"] or st not in term:
                prior[cid] = [(r, rows[cid][r][0], rows[cid][r][2]) for r in roles if r in rows[cid]]

        new = []
        if disc_gen < args.max_discovery:
            for role, summ, ev in missed:
                sig = sig_of((summ or "") + " " + (ev or ""))
                if is_dup(sig, sigs): continue
                sigs.append(sig); disc_count += 1; nid = f"D{disc_count}"
                text[nid] = f"{summ} (proposed by {role}; evidence: {ev})"
                order.append(nid); status[nid] = "PENDING"; new.append(nid)
            if new:
                disc_gen += 1
                emit(c(f"  ✨→ promoted {len(new)} discovery to claim: {', '.join(new)}", CYAN))

        render_round(order, status, prev, term, proven_v, cols)
        unresolved = [c_ for c_ in order if status[c_] not in term]
        rnd += 1
        if not unresolved and not new:
            emit(c(f"  ✅ converged after {rnd} round(s)", GREEN)); break
    else:
        emit(c(f"  ⏹ stopped at max-rounds ({args.max_rounds})", YEL))

    proven  = [c_ for c_ in order if status[c_] == proven_v]
    refuted = [c_ for c_ in order if status[c_] == "REFUTED"]
    unresolved = [c_ for c_ in order if c_ not in proven and c_ not in refuted]
    report = os.path.join(args.out, "orchestrate-report.md")
    with open(report, "w", encoding="utf-8") as fh:
        fh.write(f"# audit-swarm loop report ({args.mode})\n\n")
        fh.write(f"Rounds: {rnd}/{args.max_rounds}. Converged: {'yes' if not unresolved else 'NO'}.\n\n")
        for title, ids in (("PROVEN", proven), ("REFUTED", refuted), ("UNRESOLVED", unresolved)):
            fh.write(f"## {title} ({len(ids)})\n\n")
            for cid in ids: fh.write(f"- **{cid}** [{status[cid]}] {text[cid]}\n")
            fh.write("\n")

    emit("")
    for ln in box([c("RESULT", BOLD),
                   f"{c('✓ '+str(len(proven))+' proven', GREEN)}   "
                   f"{c('✗ '+str(len(refuted))+' refuted', RED)}   "
                   f"{c('• '+str(len(unresolved))+' unresolved', YEL)}"]):
        emit(ln)
    if proven:  emit("  " + c("✓ ", GREEN) + " ".join(proven))
    if refuted: emit("  " + c("✗ ", RED) + " ".join(refuted))
    if unresolved:
        emit("  " + c("• ", YEL) + " ".join(f"{cid}({status[cid].split()[0]})" for cid in unresolved))
    emit(c(f"  report → {report}", DIM))
    if _PROG is not None: _PROG.close()
    sys.exit(0 if not unresolved else 1)

if __name__ == "__main__":
    main()
