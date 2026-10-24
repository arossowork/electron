"""The agent worker (`--queues hwflow-agent`) runs in an image without the engine: building its workers must not
import the workflow modules (they import hwflow.buildplan). Found on the VPS deploy (#17): ImportError at start."""

import subprocess
import sys

CODE = """
import sys
sys.modules["hwflow.buildplan"] = None  # as in the agent image: importing it fails
from hwplatform.workers.main import build_workers
assert build_workers(None, None, queues=("hwflow-agent",), activities=lambda q: []) == []
assert "hwplatform.workflows" not in sys.modules, "workflow modules imported for the agent queue"
print("ok")
"""


def test_agent_queue_does_not_import_workflows():
    r = subprocess.run([sys.executable, "-c", CODE], capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr[-2000:]
    assert r.stdout.strip().endswith("ok")
