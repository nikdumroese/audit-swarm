---
name: audit-swarm
description: >-
  Run a rigorous multi-agent swarm to audit code, verify research, or stress-test a plan. Spawns
  parallel headless agents in two rounds (independent pass, then adversarial verification with a
  red-team), enforces a machine-readable verdict contract, mechanically aggregates consensus,
  verifies every citation (file:line or URL), and blocks completion until disagreements are
  resolved. Harness-agnostic: works with pi, claude, codex, or any headless agent CLI. Triggers:
  "audit swarm", "verify these findings", "re-audit with subagents", "red-team this", "cross-check
  with multiple agents", "stress-test this plan", "research swarm".
---

# Audit swarm

A verification-first multi-agent harness for three job types:

- **audit** — verify claims about a codebase (default). Verdicts: CONFIRMED / REFUTED / PARTIAL /
  RUNTIME_ONLY. Citations are `file:line`.
- **research** — verify factual claims / synthesise from web + docs. Verdicts: SUPPORTED / REFUTED /
  MIXED / UNCERTAIN. Citations are URLs or `file:line`.
- **plan** — stress-test a plan or strategy from multiple lenses. Verdicts: SOUND / RISKY / BLOCKED /
  UNKNOWN. "Missed" collects omitted risks, dependencies, failure modes, and alternatives.

The mechanics are identical across modes; only the verdict vocabulary and what counts as a citation
change. Pick the mode with `--mode`.

## Why this design (research-grounded)

- **Independent sampling + majority vote** — self-consistency (Wang et al. 2022). Consensus tally in
  `aggregate.py`.
- **Generator/verifier split** — verifying is easier than generating (Cobbe et al. 2021;
  LLM-as-a-judge, Zheng et al. 2023). Round 2 verifies Round 1 rather than re-generating.
- **Chain-of-Verification** — Dhuliawala et al. 2023 (arXiv:2309.11495). Each verdict must carry an
  independent, checkable citation, not a recollection.
- **Multi-agent debate** — Du et al. 2023 (arXiv:2305.14325). Used **only on disagreements** (SPLIT),
  to bound cost (`--debate ID`).
- **Adversarial falsification (Popperian)** — a `redteam` role must try to *disprove* every claim and
  find what the set *misses*. This is what most often surfaces the real issue.
- **Cognitive/model diversity** — correlated errors are the failure mode of clone swarms. Assign
  roles across **different models** (`--models a,b`) to decorrelate.
- **Orchestrator–worker with artifacts** — workers write verdict files; the orchestrator aggregates
  lightweight references, avoiding context bloat.

## Running inside an agent harness (pi / Claude Code / Codex)

Inside another agent, this runs as a captured, non-interactive subprocess — **not a TTY** — so the
in-place dashboard does not apply. Instead you get a **designed, append-only stream** that reads well
as monospace text: a boxed banner, per-round rules, one aligned line per agent
(`▸ edge  ✓0 ✗0 •0  ✨n`), a discovery feed, and a consensus grid + result box drawn with Unicode
symbols (`✓` proven, `✗` refuted, `•` open, `·` pending). It works three ways:

1. **Line-flushed stdout.** Every event is flushed per line, so a harness that streams tool output
   (pi streams bash output as it arrives) shows them live.
2. **Tailable progress log.** Every run appends to `<out>/progress.log` (`--progress-file` to
   override). Run the loop in the background and `tail -f` it, or poll it between agent turns:
   ```bash
   audit-swarm loop --claims ... --roles ... --repo ... --out /tmp/swarm-loop &
   tail -f /tmp/swarm-loop/progress.log
   ```
3. **Colour is opt-in.** Output is monochrome-clean by default (no risk of raw escape codes). If your
   harness renders ANSI, add `--color always`; `--color never` forces plain.

The repaint dashboard is for a real terminal; `--no-live` forces the plain stream anywhere.

## Hard rules (do not skip)

1. **Two rounds minimum, or loop to convergence.** Independent pass, then role-specialized
   verification with a red-team. Prefer `loop`, which repeats until every claim is terminal.
2. **Discovery is first-class.** Agents propose new problems in `missed[]`; the loop promotes them
   into new claims (`D1`, `D2`, ...) and verifies them in the next round. Do not restrict the swarm
   to the starting claim set.
3. **Machine-readable verdicts.** Every agent ends with one `json` block. Aggregate mechanically;
   never eyeball N prose reports.
4. **Verify every citation.** `aggregate.py` resolves each `file:line` against `--repo` (comma list
   allowed) and accepts http(s) URLs. A verdict with a broken citation is downgraded and does not
   count toward consensus.
5. **Human-verify anything consequential.** Any REFUTED/SPLIT verdict, or anything that changes a
   conclusion, is checked by hand before you act.
6. **Allow abstention.** RUNTIME_ONLY / UNCERTAIN / UNKNOWN are valid terminal-at-cap states.
   Forcing a binary verdict manufactures false certainty.
7. **Drive to terminal.** The loop debates non-terminal claims (SPLIT, PARTIAL, RUNTIME_ONLY) again,
   carrying the prior round's opposing evidence, until each is CONFIRMED/REFUTED or `--max-rounds`.

## Workflow

**Recommended: one command, loops to convergence with discovery.**
```bash
bash scripts/orchestrate.py \   # or: audit-swarm loop
  --mode audit --agent pi \
  --claims /tmp/swarm/claims.md \
  --roles  assets/roles.audit.tsv \
  --repo   "$PWD" --out /tmp/swarm-loop \
  --models "model-a,model-b" \
  --max-rounds 5 --max-discovery 2
```
Each round: run the swarm on the active claims (unresolved carry their prior split evidence as debate
context; discovered claims are verified fresh) → aggregate + verify citations → promote deduped
`missed[]` into new claims → freeze terminal claims. Stops when all claims are terminal and nothing
new was discovered, or at `--max-rounds`. Writes `orchestrate-report.md` (PROVEN / REFUTED /
UNRESOLVED) and per-round `round-N/` artifacts.

**Manual (single round at a time):**

1. **Write the claims/questions file** (`assets/claims.example.md`).
2. **Define roles** — a TSV of `role<TAB>description` (`assets/roles.*.tsv`); always include `redteam`.
3. **Run one round:** `bash scripts/run-swarm.sh --mode audit --agent pi --claims ... --roles ...
   --repo "$PWD" --out /tmp/swarm --discover` (`--discover` makes agents propose new claims).
4. **Aggregate + verify:** `python3 scripts/aggregate.py --out /tmp/swarm --repo "$PWD"` (exits
   non-zero while any SPLIT or broken citation remains).
5. **Resolve disagreements** — `--debate <ID>` for a targeted debate round.
6. **Hand-verify the consequential verdicts, then synthesise.**

## Harness-agnostic agent selection

`--agent pi` (default) · `claude` · `codex` · `custom`. For `custom`, set `AGENT_CMD` to your CLI;
`{PROMPT}` is substituted if present, otherwise the prompt is piped on stdin. All presets run
read-only. Assign different models with `--models` (round-robin across roles).

## Mode cheat-sheet

| Mode | Roles (example) | Verdicts | Citation |
|---|---|---|---|
| audit | edge/infra, client, receiver, redteam | CONFIRMED/REFUTED/PARTIAL/RUNTIME_ONLY | `file:line` |
| research | primary-source, secondary-source, skeptic, redteam | SUPPORTED/REFUTED/MIXED/UNCERTAIN | URL or `file:line` |
| plan | feasibility, dependencies, cost/risk, redteam | SOUND/RISKY/BLOCKED/UNKNOWN | `file:line` or URL |

For planning, pair with the `grilling` and `commons-lens` skills: use `grilling` to produce the plan,
`audit-swarm --mode plan` to stress-test it, and `commons-lens` as one red-team lens.
