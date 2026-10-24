"""Generate the committed OpenAPI document: `uv run --package hwplatform python -m hwplatform.api.openapi`.

Writes services/platform/openapi.json (or the path given as the first argument). A test fails when the file is
out of date, so an API change and its OpenAPI diff are reviewed together.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from ..settings import Settings
from .app import create_app

DEFAULT_PATH = Path(__file__).resolve().parents[3] / "openapi.json"


def render() -> str:
    # The schema does not depend on settings or on any running service: the lifespan is not started.
    app = create_app(Settings(jwt_secret="x" * 32))
    return json.dumps(app.openapi(), indent=2, ensure_ascii=False) + "\n"


def main() -> None:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_PATH
    path.write_text(render())
    print(f"wrote {path}")


if __name__ == "__main__":
    main()
