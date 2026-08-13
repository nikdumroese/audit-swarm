# audit-swarm — run a multi-agent swarm to audit code, verify research, or stress-test a plan

**Author:** nikdumroese
**Date:** 2026-08-13
**Version:** 1.0
**Reading time:** 2 minutes to run it; 5 minutes for the full page
**Written for:** Engineers and researchers who use a headless agent CLI (pi, Claude, Codex, or their own)
**Status:** Stable

---

## What this is

audit-swarm runs several AI agents in parallel to check the same work, then combines their verdicts
mechanically. You give it a set of claims (or questions) and a set of roles. It runs the agents,
verifies every citation they produce, tallies where they agree, and **loops**: it debates the claims
the agents disagree on, and **promotes new problems the agents discover** into fresh claims, until
every claim is proven or refuted (or a round cap is reached).

Use it to do one of three jobs:

- **audit** — check claims about a codebase.
- **research** — check facts against web pages and documents.
- **plan** — stress-test a plan or strategy.

## Are you the right reader?

- **You want to run it now** → go to [Warnings](#warnings), then [Quickstart](#quickstart).
- **You want to decide whether to use it** → read [What problem it solves](#what-problem-it-solves)
  and [How it works](#how-it-works).
- **You want to install it as a skill** → go to [Install](#install).

---

## What problem it solves

One agent, or several identical agents, tend to make the same mistakes. They share blind spots, cite
files or sources that do not exist, and state claims with more confidence than the evidence supports.

audit-swarm reduces these failures three ways. It runs **different roles** (and, if you want,
different models) so the agents do not fail the same way. It runs a **red-team** whose only job is to
disprove each claim and to find what the claim set missed. And it **verifies every citation** and
**blocks completion** while any disagreement remains.

---

## Warnings

Read these before you run the commands below.

- **Cost.** The tool starts one agent per role, and can run two rounds. Cost grows with the number of
  roles, rounds, and the model size. Start small.
- **Read-only, but confirm your sandbox.** Every built-in agent preset runs in read-only mode. If you
  supply your own agent with `--agent custom`, confirm that it cannot edit files.
- **A passing gate is not proof.** The tool checks that citations resolve and that agents agree. It
  does not prove the agents are correct. Hand-verify any verdict that changes a decision.

---

## Quickstart

```bash
# 1. Write the claims or questions (see assets/claims.example.md).
# 2. Pick the roles for the job (assets/roles.audit.tsv, roles.research.tsv, or roles.plan.tsv).

# 3a. RECOMMENDED: loop until every claim is proven or refuted, promoting discovered issues.
npx github:nikdumroese/audit-swarm loop \
  --mode audit --agent pi \
  --claims ./audit-swarm/claims.md \
  --roles  ./audit-swarm/roles.audit.tsv \
  --repo   "$PWD" --out /tmp/swarm-loop \
  --models "model-a,model-b" \
  --max-rounds 5 --max-discovery 2

# 3b. OR run one round at a time and combine the verdicts yourself.
npx github:nikdumroese/audit-swarm run --mode audit --agent pi \
  --claims ./audit-swarm/claims.md --roles ./audit-swarm/roles.audit.tsv \
  --repo "$PWD" --out /tmp/swarm --discover
npx github:nikdumroese/audit-swarm aggregate --out /tmp/swarm --repo "$PWD"
```

`loop` writes `orchestrate-report.md` (PROVEN / REFUTED / UNRESOLVED) plus per-round `round-N/`
artifacts. In a terminal it shows a **live dashboard** that repaints in place: a header with a round
timer, an agents row (`✔ done` / spinner while running), a per-claim grid where a coloured dot lands
for each agent's vote (green = proven, red = refuted, yellow = unresolved, dim `·` = pending), live
totals, and the latest `✨` discovery. When output is not a terminal (piped/CI) or with `--no-live`, it
The colour dashboard is for a real terminal. **Inside an agent harness (pi / Claude Code / Codex)**
the run is a captured non-TTY subprocess, so instead: stdout events are line-flushed (a streaming
harness shows them live), and every run appends a plain-text log to `<out>/progress.log`
(`--progress-file` to override). To watch live, run the loop in the background and `tail -f` that log
— its path is printed as the first line. `--max-rounds` bounds total rounds; `--max-discovery` bounds how many generations of new claims are promoted. `aggregate` exits non-zero while any split
verdict or broken citation remains, so you can use it to gate a script or a CI step.

## Choose your agent CLI

`--agent` selects the command the swarm calls. This is what makes the tool harness-agnostic.

- `pi` (default), `claude`, and `codex` use built-in read-only presets.
- `custom` runs your own command. Set `AGENT_CMD` to the command. The tool substitutes the token
  `{PROMPT}` if it is present; otherwise it sends the prompt on standard input.

```bash
AGENT_CMD='my-llm --json --readonly' bash scripts/run-swarm.sh --agent custom ...
```

## Verdicts and citations by mode

| Mode | Verdict values | Citation type |
|---|---|---|
| audit | CONFIRMED, REFUTED, PARTIAL, RUNTIME_ONLY | `file:line` |
| research | SUPPORTED, REFUTED, MIXED, UNCERTAIN | URL or `file:line` |
| plan | SOUND, RISKY, BLOCKED, UNKNOWN | `file:line` or URL |

Every mode also allows N/A, for claims outside a role's scope.

---

## How it works

The design follows published methods. Each name below is a technique you can look up.

- **Self-consistency** (Wang et al. 2022): take several independent answers, then take the majority.
- **Generator/verifier split** (Cobbe et al. 2021; LLM-as-a-judge, Zheng et al. 2023): checking an
  answer is easier than producing one, so round two verifies round one.
- **Chain-of-Verification** (Dhuliawala et al. 2023, arXiv:2309.11495): each verdict must carry an
  independent citation you can check.
- **Multi-agent debate** (Du et al. 2023, arXiv:2305.14325): agents argue to improve accuracy. This
  tool debates only the claims that split, to limit cost.
- **Adversarial falsification**: the red-team role tries to disprove each claim and to list what the
  claim set missed.

The run is an orchestrator-worker pattern: each agent writes its verdict to its own file, and the
aggregator reads those files. Agents do not pass long outputs to each other, which keeps the context
small.

## Install

You have three ways to run it.

**Run with npx, no install (recommended).** This runs the current version straight from GitHub. You
need Node.js, plus `bash`, `python3`, and one agent CLI on your PATH.

```bash
npx github:nikdumroese/audit-swarm init                 # copy example roles/claims into ./audit-swarm
npx github:nikdumroese/audit-swarm run \
  --mode audit --agent pi \
  --claims ./audit-swarm/claims.md \
  --roles  ./audit-swarm/roles.audit.tsv \
  --repo   "$PWD" --out /tmp/swarm
npx github:nikdumroese/audit-swarm aggregate --out /tmp/swarm --repo "$PWD"
```

The `npx` command name maps to the scripts: `run` and `debate` call `run-swarm.sh`; `aggregate` calls
`aggregate.py`; `init` copies the example files; `assets` prints the packaged assets path.

**Clone as a skill.** Put it in your skills directory so an agent can discover and update it:

```bash
git clone https://github.com/nikdumroese/audit-swarm ~/.agents/skills/audit-swarm
```

**Call the scripts directly.** After cloning, run `scripts/run-swarm.sh` and `scripts/aggregate.py`
as shown in the [Quickstart](#quickstart).

## Files

| Path | What it is |
|---|---|
| `SKILL.md` | The skill manifest, usable directly by pi or Claude Skills |
| `scripts/orchestrate.py` | The loop: discover + debate until every claim is terminal; writes the report |
| `scripts/run-swarm.sh` | Runs one round of role agents on any CLI; writes `verdict-<role>.md` |
| `scripts/aggregate.py` | Reads verdicts, verifies citations, tallies consensus, gates on splits |
| `assets/roles.*.tsv` | Example role sets for audit, research, and plan |
| `assets/claims.example.md` | Template for the claims or questions file |

## Glossary

| Term | Meaning |
|---|---|
| Agent | One AI process the tool runs with a single role. |
| Role | The job and viewpoint you assign to an agent, such as `redteam`. |
| Red-team | A role whose job is to disprove the claims and find what is missing. |
| Claim | One statement the swarm must confirm or refute. |
| Verdict | An agent's judgment on one claim, from the mode's value list. |
| Citation | The evidence for a verdict: a `file:line` or a URL the tool can check. |
| SPLIT | A claim on which the agents disagree. The gate blocks until you resolve it. |
| Gate | The aggregator's exit status: non-zero while a split or broken citation remains. |
| Orchestrator-worker | A pattern where each agent writes its own output file and one step combines them. |

## How to respond

- **Report a problem or request a change:** open an issue on the GitHub repository.
- **Contribute:** open a pull request.

## License

MIT. See [LICENSE](LICENSE).
