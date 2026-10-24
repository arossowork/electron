"""`python -I -m hwplatform.agent.launch <args>`: exec the real Claude Code CLI with the allowlisted
environment.

The SDK starts the CLI with the worker's whole environment merged under its options (database, S3 and master
key included). This launcher is the SDK's `cli_path` (through the stub of `sandbox.write_launcher`): it keeps
only `sandbox.child_env` and replaces itself with the binary named by HWPLATFORM_AGENT_REAL_CLI.
"""

from __future__ import annotations

import os
import sys

from .sandbox import REAL_CLI_ENV, child_env


def main() -> None:
    real = os.environ.get(REAL_CLI_ENV)
    if not real:
        sys.stderr.write(f"{REAL_CLI_ENV} is not set\n")
        raise SystemExit(127)
    os.execve(real, [real, *sys.argv[1:]], child_env(dict(os.environ)))  # noqa: S606 - no shell, by design


if __name__ == "__main__":
    main()
