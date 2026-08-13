#!/usr/bin/env node
'use strict'
/*
 * audit-swarm CLI — thin, zero-dependency dispatcher.
 * Subcommands:
 *   run        forward args to scripts/run-swarm.sh
 *   debate ID  shorthand for: run --debate ID <args...>
 *   aggregate  forward args to scripts/aggregate.py (python3)
 *   init [dir] copy example roles/claims into <dir> (default ./audit-swarm)
 *   assets     print the packaged assets directory path
 *   help       usage
 *
 * Prerequisites on the host: bash, python3, and at least one agent CLI (pi | claude | codex | custom).
 */
const {spawnSync} = require('child_process')
const fs = require('fs')
const path = require('path')

const ROOT = path.resolve(__dirname, '..')
const SCRIPTS = path.join(ROOT, 'scripts')
const ASSETS = path.join(ROOT, 'assets')

const USAGE = `audit-swarm — multi-agent swarm to audit code, verify research, or stress-test a plan

Usage:
  audit-swarm loop [orchestrate.py options]    Discover + debate until every claim is terminal
  audit-swarm run [run-swarm.sh options]       Run one round of the swarm
  audit-swarm debate <ID> [options]            Run one debate round on a split claim ID
  audit-swarm aggregate [aggregate.py options]  Combine verdicts, verify citations, gate on splits
  audit-swarm init [dir]                        Copy example roles/claims into <dir> (default ./audit-swarm)
  audit-swarm assets                            Print the packaged assets directory
  audit-swarm help                              Show this help

The loop (recommended) drives claims to a terminal verdict and promotes discovered issues:
  audit-swarm loop --claims F --roles F --repo DIR --out DIR [--mode audit|research|plan]
    [--agent pi|claude|codex|custom] [--models a,b] [--max-rounds N] [--max-discovery M]

Common run options (forwarded to scripts/run-swarm.sh):
  --mode audit|research|plan     Job type (default audit)
  --agent pi|claude|codex|custom Agent CLI (default pi). custom uses env AGENT_CMD
  --claims FILE                  Claims/questions file (required)
  --roles FILE                   role<TAB>description TSV (required; see 'audit-swarm init')
  --repo DIR                     Context root for citations (comma-separated roots allowed)
  --out DIR                      Output dir for verdict-*.md (default /tmp/swarm)
  --models a,b                   Assign roles across models (round-robin)
  --provider NAME  --thinking L

Examples:
  npx github:nikdumroese/audit-swarm init
  npx github:nikdumroese/audit-swarm run --mode audit --claims ./audit-swarm/claims.md \\
      --roles ./audit-swarm/roles.audit.tsv --repo "$PWD" --out /tmp/swarm
  npx github:nikdumroese/audit-swarm aggregate --out /tmp/swarm --repo "$PWD"
`

function run(cmd, args) {
  const r = spawnSync(cmd, args, {stdio: 'inherit'})
  if (r.error) {
    if (r.error.code === 'ENOENT') {
      console.error(`audit-swarm: '${cmd}' not found on PATH. Install it and retry.`)
      process.exit(127)
    }
    console.error(`audit-swarm: ${r.error.message}`)
    process.exit(1)
  }
  process.exit(r.status === null ? 1 : r.status)
}

function initInto(dir) {
  const target = path.resolve(dir || 'audit-swarm')
  fs.mkdirSync(target, {recursive: true})
  const copied = []
  for (const f of fs.readdirSync(ASSETS)) {
    const dest = path.join(target, f.replace(/^claims\.example\.md$/, 'claims.md'))
    fs.copyFileSync(path.join(ASSETS, f), dest)
    copied.push(path.relative(process.cwd(), dest))
  }
  console.error(`Wrote:\n  ${copied.join('\n  ')}\n\nEdit claims.md, pick a roles.*.tsv, then:\n  audit-swarm run --claims ${path.relative(process.cwd(), path.join(target, 'claims.md'))} --roles ${path.relative(process.cwd(), path.join(target, 'roles.audit.tsv'))} --repo "$PWD"`)
}

const [sub, ...rest] = process.argv.slice(2)
switch (sub) {
  case 'loop':
    run('python3', [path.join(SCRIPTS, 'orchestrate.py'), ...rest])
    break
  case 'run':
    run('bash', [path.join(SCRIPTS, 'run-swarm.sh'), ...rest])
    break
  case 'debate': {
    const id = rest[0]
    if (!id) { console.error('audit-swarm debate: missing <ID>'); process.exit(2) }
    run('bash', [path.join(SCRIPTS, 'run-swarm.sh'), '--debate', id, ...rest.slice(1)])
    break
  }
  case 'aggregate':
    run('python3', [path.join(SCRIPTS, 'aggregate.py'), ...rest])
    break
  case 'init':
    initInto(rest[0]); break
  case 'assets':
    console.log(ASSETS); break
  case undefined:
  case 'help':
  case '-h':
  case '--help':
    process.stdout.write(USAGE); break
  default:
    console.error(`audit-swarm: unknown command '${sub}'\n`)
    process.stdout.write(USAGE)
    process.exit(2)
}
