"""The committed services/platform/openapi.json matches what the app serves, and the schema is complete."""

import json

from hwplatform.api.openapi import DEFAULT_PATH, render


def test_committed_openapi_is_up_to_date():
    assert DEFAULT_PATH.exists(), "run: uv run --package hwplatform python -m hwplatform.api.openapi"
    assert DEFAULT_PATH.read_text() == render(), (
        "services/platform/openapi.json is out of date; regenerate it with "
        "`uv run --package hwplatform python -m hwplatform.api.openapi` and commit it"
    )


def test_every_operation_is_tagged_and_documented():
    doc = json.loads(render())
    declared = {t["name"] for t in doc["tags"]}
    expected = {
        "POST /api/v1/projects/{project_id}/design-versions",
        "GET /api/v1/projects/{project_id}/design-versions",
        "GET /api/v1/design-versions/{version_id}",
        "GET /api/v1/design-versions/{version_id}/files/{path}",
        "POST /api/v1/projects/{project_id}/runs",
        "GET /api/v1/projects/{project_id}/runs",
        "GET /api/v1/runs/{run_id}",
        "POST /api/v1/runs/{run_id}/cancel",
        "GET /api/v1/runs/{run_id}/manifest",
        "GET /api/v1/runs/{run_id}/report",
        "GET /api/v1/runs/{run_id}/artifacts",
        "GET /api/v1/runs/{run_id}/artifacts/{name}",
        "GET /api/v1/runs/{run_id}/questions",
        "POST /api/v1/questions/{question_id}/answer",
        "GET /api/v1/runs/{run_id}/events",
        "GET /api/v1/projects/{project_id}/events",
        "GET /api/v1/events",
    }
    seen = set()
    for path, ops in doc["paths"].items():
        for method, op in ops.items():
            seen.add(f"{method.upper()} {path}")
            assert op.get("tags"), f"{method} {path} has no tag"
            assert set(op["tags"]) <= declared, (method, path, op["tags"])
            assert op.get("summary"), f"{method} {path} has no summary"
    assert expected <= seen, sorted(expected - seen)
    assert not [t for t in doc["tags"] if not t.get("description")]
