#!/usr/bin/env bash
# Unattended /hw-design run on the Claude subscription (no API key).
# Usage: scripts/hw-run.sh <project> [model] ["extra instruction"]
# Loops until state.md says done, or until the agent needs product answers (inbox/questions.json).
set -euo pipefail
cd "$(dirname "$0")/.."
P="$1"; MODEL="${2:-opus}"; EXTRA="${3:-}"
DIR="projects/$P"; mkdir -p "$DIR"
unset ANTHROPIC_API_KEY
export HWFLOW_HEADLESS=1
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
    OUT=$(claude -p "$MSG" --resume "$(cat "$SID_FILE")" --model "$MODEL" --permission-mode acceptEdits --output-format json)
  else
    OUT=$(claude -p "/hw-design continue $P . $EXTRA" --model "$MODEL" --permission-mode acceptEdits --output-format json)
  fi
  echo "$OUT" >> "$LOG"
  echo "$OUT" | python3 -c "import json,sys; d=json.load(sys.stdin); open('$SID_FILE','w').write(d.get('session_id','')); print('round $i:', d.get('num_turns'), 'turns;', str(d.get('result'))[:300])"
done
