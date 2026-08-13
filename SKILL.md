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

## Hard rules (do not skip)

1. **Two rounds.** Independent pass, then role-specialized verification with a red-team. Never ship
   round-1 conclusions unverified.
2. **Machine-readable verdicts.** Every agent ends with one `json` block. Aggregate mechanically;
   never eyeball N prose reports.
3. **Verify every citation.** `aggregate.py` resolves each `file:line` against `--repo` and accepts
   http(s) URLs. A verdict with a broken citation is downgraded and does not count toward consensus.
4. **Human-verify anything consequential.** Any REFUTED/SPLIT verdict, or anything that changes a
   conclusion, is checked by hand before you act.
5. **Allow abstention.** RUNTIME_ONLY / UNCERTAIN / UNKNOWN are valid. Forcing a binary verdict
   manufactures false certainty.
6. **Block on disagreement.** `aggregate.py` exits non-zero while any SPLIT or broken citation
   remains. Do not report "done" until the gate passes.

## Workflow

1. **Write the claims/questions file** (`assets/claims.example.md`). One verifiable assertion per
   entry. For pure discovery, use open questions instead.
2. **Define roles** — a TSV of `role<TAB>description` (`assets/roles.*.tsv`). Partition the surface
   area and always include a `redteam` role.
3. **Run:**
   ```bash
   bash scripts/run-swarm.sh \
     --mode audit --agent pi \
     --claims /tmp/swarm/claims.md \
     --roles  assets/roles.audit.tsv \
     --repo   "$PWD" --out /tmp/swarm \
     --models "claude-opus-4.8,claude-sonnet-4.5"
   ```
4. **Aggregate + verify citations:**
   ```bash
   python3 scripts/aggregate.py --out /tmp/swarm --repo "$PWD"
   ```
5. **Resolve disagreements** — hand-check, or `--debate <ID>` for a targeted debate round.
6. **Hand-verify the consequential verdicts, then synthesise.** Record code-proven vs
   needs-runtime/needs-data.

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
