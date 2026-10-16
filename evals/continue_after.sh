#!/usr/bin/env bash
# Wait for a trial's running claude turn to end, then continue the trial with the current harness code.
# Usage: evals/continue_after.sh <run_dir> <fixture_id> <session_id>
set -u
cd "$(dirname "$0")/.."
while pgrep -f "resume $3" >/dev/null; do sleep 20; done
uv run python -u evals/harness.py resume "$1" --only "$2" >> "evals/runs/continue_$2.log" 2>&1
