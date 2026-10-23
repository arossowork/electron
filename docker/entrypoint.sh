#!/usr/bin/env bash
# Runs every container command under a virtual display, so `kicad-cli pcb render` has an OpenGL context.
set -euo pipefail
unset ANTHROPIC_API_KEY  # the agent runs on the Claude subscription (CLAUDE_CODE_OAUTH_TOKEN or a saved login)
mkdir -p /app/projects /app/evals/runs
# Trust /app once, so headless `claude -p` applies the repo's .claude/settings.json allow list (uv run hwflow, ...).
/opt/venv/bin/python - <<'PY'
import json, os
from pathlib import Path
p = Path(os.environ.get("CLAUDE_CONFIG_DIR", Path.home() / ".claude")) / ".claude.json"
p.parent.mkdir(parents=True, exist_ok=True)
d = json.loads(p.read_text()) if p.exists() else {}
proj = d.setdefault("projects", {}).setdefault("/app", {})
if not proj.get("hasTrustDialogAccepted"):
    proj["hasTrustDialogAccepted"] = True
    p.write_text(json.dumps(d, indent=2))
PY
exec xvfb-run -a -s "-screen 0 1920x1080x24" "$@"
