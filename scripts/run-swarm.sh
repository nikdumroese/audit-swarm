#!/usr/bin/env bash
# audit-swarm harness — spawn role-specialized headless agents in parallel, each emitting a
# machine-readable JSON verdict block. Read-only. Harness-agnostic: works with any agent CLI.
#
# Usage:
#   run-swarm.sh --claims FILE --roles FILE [--repo DIR] [--out DIR] [--mode audit|research|plan]
#                [--agent pi|claude|codex|custom] [--models m1,m2] [--provider NAME]
#                [--thinking LEVEL] [--debate ID]
#
# Agent selection (harness-agnostic):
#   --agent pi        (default)  pi -p PROMPT --no-session -t read,bash
#   --agent claude               claude -p PROMPT --allowedTools Read,Bash,Grep,Glob
#   --agent codex                codex exec --sandbox read-only PROMPT
#   --agent custom  + AGENT_CMD  custom command template. If it contains the token {PROMPT} that is
#                                substituted; otherwise the prompt is piped to the command on stdin.
#                                e.g. AGENT_CMD='my-llm --json --tools read' run-swarm.sh --agent custom ...
#
# roles file: TSV, one "role<TAB>description" per line. Include a role named "redteam".
set -u

CLAIMS="" ROLES="" REPO="$PWD" OUT="/tmp/swarm" MODE="audit"
AGENT="${AGENT:-pi}" AGENT_CMD="${AGENT_CMD:-}"
MODELS="" PROVIDER="" THINKING="high" DEBATE="" DISCOVER=""
while [ $# -gt 0 ]; do
  case "$1" in
    --claims) CLAIMS="$2"; shift 2;;
    --roles) ROLES="$2"; shift 2;;
    --repo) REPO="$2"; shift 2;;
    --out) OUT="$2"; shift 2;;
    --mode) MODE="$2"; shift 2;;
    --agent) AGENT="$2"; shift 2;;
    --models) MODELS="$2"; shift 2;;
    --provider) PROVIDER="$2"; shift 2;;
    --thinking) THINKING="$2"; shift 2;;
    --debate) DEBATE="$2"; shift 2;;
    --discover) DISCOVER=1; shift 1;;
    *) echo "unknown arg: $1" >&2; exit 2;;
  esac
done
[ -f "$CLAIMS" ] || { echo "missing --claims file" >&2; exit 2; }
[ -f "$ROLES" ]  || { echo "missing --roles file"  >&2; exit 2; }
mkdir -p "$OUT"

# --- mode -> verdict vocabulary --------------------------------------------------------------
case "$MODE" in
  audit)    VOCAB="CONFIRMED|REFUTED|PARTIAL|RUNTIME_ONLY|N/A"
            VMEAN="CONFIRMED=code proves it; REFUTED=code contradicts it; PARTIAL=partly; RUNTIME_ONLY=needs live/runtime behaviour; N/A=outside your role."
            MISS="a bug or drop the claims do NOT cover" ;;
  research) VOCAB="SUPPORTED|REFUTED|MIXED|UNCERTAIN|N/A"
            VMEAN="SUPPORTED=credible sources agree; REFUTED=sources contradict; MIXED=sources disagree; UNCERTAIN=insufficient/low-quality evidence; N/A=outside your role."
            MISS="a claim, source, or counter-argument the set is MISSING" ;;
  plan)     VOCAB="SOUND|RISKY|BLOCKED|UNKNOWN|N/A"
            VMEAN="SOUND=well-founded and executable; RISKY=has a material risk (state it); BLOCKED=a hard dependency/blocker makes it infeasible as written; UNKNOWN=needs a decision or data; N/A=outside your role."
            MISS="a risk, dependency, failure mode, or alternative the plan OMITS" ;;
  *) echo "unknown --mode: $MODE (audit|research|plan)" >&2; exit 2;;
esac

CONTRACT='
End your response with EXACTLY ONE fenced ```json block, nothing after it:
```json
{"role":"<role>","verdicts":[
  {"id":"C1","verdict":"'"$VOCAB"'","confidence":"high|med|low","evidence":"CITATION — one-line reason"}
],"missed":[{"summary":"'"$MISS"'","evidence":"CITATION"}]}
```
Verdict meanings: '"$VMEAN"'
CITATION rules: cite a real, checkable source — a file:line you actually opened, or an http(s) URL
you actually fetched. The aggregator verifies citations; uncheckable evidence is discarded. Include
a verdict for every claim in scope for your role; use N/A for the rest.'

# --- agent adapter (harness-agnostic) --------------------------------------------------------
IFS=',' read -r -a MODEL_ARR <<< "${MODELS:-}"
model_for(){ [ -z "${MODELS:-}" ] && { echo ""; return; }; echo "${MODEL_ARR[$(( $1 % ${#MODEL_ARR[@]} ))]}"; }

run_agent(){ # $1 prompt  $2 model  -> writes agent's final text to stdout
  local prompt="$1" model="$2"
  case "$AGENT" in
    pi)     pi -p "$prompt" ${PROVIDER:+--provider "$PROVIDER"} ${model:+--model "$model"} \
              --thinking "$THINKING" --no-session -t read,bash ;;
    claude) claude -p "$prompt" ${model:+--model "$model"} --allowedTools "Read,Bash,Grep,Glob" ;;
    codex)  codex exec --sandbox read-only ${model:+-m "$model"} "$prompt" ;;
    custom) [ -n "$AGENT_CMD" ] || { echo "AGENT_CMD required for --agent custom" >&2; exit 2; }
            if printf '%s' "$AGENT_CMD" | grep -q '{PROMPT}'; then
              eval "${AGENT_CMD//\{PROMPT\}/$(printf '%q' "$prompt")}"
            else
              printf '%s' "$prompt" | eval "$AGENT_CMD"
            fi ;;
    *) echo "unknown --agent: $AGENT" >&2; exit 2;;
  esac
}

CLAIMS_TXT="$(cat "$CLAIMS")"
DISCOVER_TXT=""
[ -n "$DISCOVER" ] && DISCOVER_TXT='
DISCOVERY MANDATE: also actively search for problems NOT listed in the claims. Put each new problem
in the "missed" array with a precise, checkable citation. Discovery is a first-class goal this round
— do not restrict yourself to the listed claims.'
launch(){ # $1 role  $2 desc  $3 idx  $4 extra
  local role="$1" desc="$2" idx="$3" extra="$4" model; model="$(model_for "$idx")"
  local prompt="You are a read-only $MODE reviewer. Do NOT edit files. Context root: $REPO.

ROLE: $desc
$extra$DISCOVER_TXT

CLAIMS / QUESTIONS UNDER REVIEW:
$CLAIMS_TXT
$CONTRACT"
  run_agent "$prompt" "$model" > "$OUT/verdict-$role.md" 2>"$OUT/verdict-$role.err" &
  echo "  launched $role${model:+ [$model]} (pid $!)"
}

if [ -n "$DEBATE" ]; then
  echo "== debate round on: $DEBATE (agent=$AGENT, mode=$MODE) =="
  ALL="$(for f in "$OUT"/verdict-*.md; do echo "### $(basename "$f")"; cat "$f"; echo; done)"
  idx=0
  while IFS=$'\t' read -r role desc; do
    [ -z "$role" ] && continue; case "$role" in \#*) continue;; esac
    launch "debate-$role" "$desc" "$idx" "The prior round SPLIT on $DEBATE. All prior verdicts:
$ALL
Re-examine ONLY $DEBATE, address the strongest opposing evidence explicitly, then give your final verdict for $DEBATE."
    idx=$((idx+1))
  done < "$ROLES"
  wait; echo "== debate done =="; exit 0
fi

echo "== swarm: agent=$AGENT mode=$MODE roles=$ROLES =="
idx=0
while IFS=$'\t' read -r role desc; do
  [ -z "$role" ] && continue; case "$role" in \#*) continue;; esac
  launch "$role" "$desc" "$idx" ""
  idx=$((idx+1))
done < "$ROLES"
wait
echo "== swarm done: $idx agents =="
for f in "$OUT"/verdict-*.md; do echo "  $(basename "$f"): $(wc -l < "$f") lines"; done
