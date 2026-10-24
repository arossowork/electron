import os

import pytest


@pytest.fixture(autouse=True, scope="session")
def _private_cas(tmp_path_factory):
    """Builds in tests store artifacts and stage-cache entries in a session folder, not in ~/.cache/hwflow/cas."""
    old = os.environ.get("HWFLOW_CAS_DIR")
    os.environ["HWFLOW_CAS_DIR"] = str(tmp_path_factory.mktemp("cas"))
    yield
    if old is None:
        os.environ.pop("HWFLOW_CAS_DIR", None)
    else:
        os.environ["HWFLOW_CAS_DIR"] = old
