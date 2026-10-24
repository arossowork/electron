#!/usr/bin/env bash
# Unattended /hw-design run on the Claude subscription (no API key).
# Usage: scripts/hw-run.sh <project> [model] ["extra instruction"]
# Loops until state.md says done, or until the agent needs product answers (inbox/questions.json).
set -euo pipefail
cd "$(dirname "$0")/.."
P="$1"; MODEL="${2:-opus}"; EXTRA="${3:-}"
DIR="projects/$P"; mkdir -p "$DIR"
unset ANTHROPIC_API_KEY CLAUDE_EFFORT CLAUDE_CODE_EFFORT_LEVEL  # an inherited effort overrides the agents' `effort:`
export HWFLOW_HEADLESS=1
# Same lean context and 400k auto-compact window as hwharness.agent (designer_args, headless_env).
export CLAUDE_CODE_AUTO_COMPACT_WINDOW="${HWFLOW_AUTOCOMPACT_WINDOW:-400000}"
export CLAUDE_CODE_SUBAGENT_PROMPT_CACHE_TTL="${CLAUDE_CODE_SUBAGENT_PROMPT_CACHE_TTL:-1h}"
ARGS=(--model "$MODEL" --setting-sources project,local --strict-mcp-config
      --tools "Bash,Read,Write,Edit,Agent,Skill,WebSearch,WebFetch" --permission-mode acceptEdits --output-format json)
[ -n "${HWFLOW_EFFORT:-}" ] && ARGS+=(--effort "$HWFLOW_EFFORT")
LOG="$DIR/.runs.jsonl"
SID_FILE="$DIR/.session"
for i in $(seq 1 "${MAX_ROUNDS:-8}"); do
  if [ -f "$DIR/inbox/questions.json" ]; then
    echo "The agent needs product answers: $DIR/inbox/questions.json"
    echo "Write them to $DIR/inbox/answers.md, delete questions.json, and run this script again."
    exit 2
  fi
  if grep -qiE '^\s*[-*]?\s*status:\s*done' "$DIR/state.md" 2>/dev/null; then
    echo "Done. See $DIR/SUMMARY.md and $DIR/build/REPORT.md"; exit 0
  fi
  if [ -s "$SID_FILE" ]; then
    MSG="Continue the /hw-design flow from state.md. Answers, if any, are in inbox/answers.md. $EXTRA"
    OUT=$(claude -p "$MSG" --resume "$(cat "$SID_FILE")" "${ARGS[@]}")
  else
    OUT=$(claude -p "/hw-design continue $P . $EXTRA" "${ARGS[@]}")
  fi
  echo "$OUT" >> "$LOG"
  # Subscription usage window exhausted: wait for the reset, then retry the same round.
  WAIT=$(echo "$OUT" | uv run python -c "import json,sys; from hwharness import agent as h
d=json.loads(sys.stdin.read() or '{}'); t=str(d.get('result',''))
print(h._limit_wait_seconds(t) if d.get('is_error') and h.LIMIT_RE.search(t) else 0)")
  if [ "$WAIT" -gt 0 ]; then
    echo "usage limit reached: waiting $((WAIT / 60)) min for the window to refresh"
    sleep "$WAIT"; continue
  fi
  echo "$OUT" | python3 -c "import json,sys; d=json.load(sys.stdin); open('$SID_FILE','w').write(d.get('session_id','')); print('round $i:', d.get('num_turns'), 'turns;', str(d.get('result'))[:300])"
done
