"""Smoke of the agent image (deploy/images/agent.Dockerfile, #15), without any API call:
    docker run --rm --read-only --network none --cap-drop ALL --tmpfs /tmp \
      --tmpfs /scratch:uid=10001,gid=10001,size=256m -v "$PWD/deploy/images/agent-smoke.py:/smoke.py:ro" \
      hwplatform-agent:dev python /smoke.py
"""

import importlib.util
import subprocess

import hwplatform.activities.agent  # noqa: F401 - the agent activity imports without the engine
from hwharness.prompts import stage_prompt

from hwplatform.agent.sandbox import bundled_cli
from hwplatform.contracts import DESIGN_STAGES
from hwplatform.workers.main import agent_ready, usable_queues

assert agent_ready() is None and usable_queues(("hwflow-agent",)) == ("hwflow-agent",)
for s in DESIGN_STAGES:
    assert stage_prompt(s).model.startswith("claude-"), s
v = subprocess.run(
    [bundled_cli(), "--version"],
    capture_output=True,
    text=True,
    env={"HOME": "/scratch/home", "PATH": "/usr/bin:/bin"},
    check=True,
).stdout
assert "Claude Code" in v, v
assert importlib.util.find_spec("numpy") is None and importlib.util.find_spec("hwflow.core") is None
print("agent image ok:", v.strip())
