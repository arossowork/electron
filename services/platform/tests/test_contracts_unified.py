"""One source of truth (issue #9): hwplatform re-uses the engine's contract modules, it does not copy them."""

import dataclasses
import uuid

import hwflow.artifacts as engine_artifacts
import hwflow.runs as engine_runs
from hwflow.runs import TRANSITIONS, Run, RunStatus, envelope, now

import hwplatform.contracts as contracts
import hwplatform.runs as pg_runs
import hwplatform.storage as storage
from hwplatform.events import Event, rfc3339


def test_platform_modules_use_the_engine_objects():
    assert pg_runs.RunStatus is engine_runs.RunStatus
    assert pg_runs.InvalidTransition is engine_runs.InvalidTransition
    assert pg_runs.RunNotFound is engine_runs.RunNotFound
    assert pg_runs.run_to_manifest is engine_runs.run_to_manifest
    assert pg_runs.ArtifactRef is engine_artifacts.ArtifactRef
    assert storage.DEFAULT_MEDIA_TYPE == engine_artifacts.DEFAULT_MEDIA_TYPE
    assert rfc3339 is engine_runs.rfc3339
    assert contracts.ArtifactRef is engine_artifacts.ArtifactRef


def test_contracts_module_holds_only_the_wire_dataclasses():
    own = {
        n for n, v in vars(contracts).items() if isinstance(v, type) and v.__module__ == contracts.__name__
    }
    assert own == {"RunCtx", "BuildInput", "DesignInput", "QuestionAnswer"}
    assert all(dataclasses.is_dataclass(getattr(contracts, n)) for n in own)


def test_pg_transition_set_is_the_engine_table():
    # PgRunStore builds its UPDATE ... WHERE status = ANY(...) from allowed_from(), derived from TRANSITIONS
    for to in RunStatus:
        assert set(engine_runs.allowed_from(to.value)) == {
            f for f, tos in TRANSITIONS.items() if to.value in tos
        }


def test_event_envelope_has_the_engine_envelope_shape():
    run = Run(
        run_id=str(uuid.uuid4()),
        kind="build",
        tenant_id=str(uuid.uuid4()),
        project_id="p",
        design_version=None,
        status=RunStatus.running,
        verdict=None,
        options={},
        options_hash="",
        settings={},
        code={},
        tools={},
        stages=[],
        artifacts={},
        created_at=now(),
    )
    eng = envelope(run, 1, "run.started", f"runs/{run.run_id}", {})
    plat = Event(
        "run.started", run.tenant_id, f"runs/{run.run_id}", {}, runid=run.run_id, seq=1
    ).to_envelope()
    assert set(eng) == set(plat)
    assert eng["dataschema"] == plat["dataschema"]
