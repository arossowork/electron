"""One ArtifactStore contract, two backends (ADR 0001, section 2.1; issue #6, "build passes against S3 and LocalFS").

The same suite runs against the engine's `LocalCAS` and the platform's `S3ArtifactStore` (compose SeaweedFS).
The tree helpers (`put_tree`, `read_tree`, `get_tree`) are the engine's and take any store, so a project that
round-trips here round-trips in a build.
"""

from __future__ import annotations

import hashlib
import os

import pytest
from hwflow.artifacts import (
    TREE_MEDIA_TYPE,
    ArtifactNotFound,
    ArtifactStore,
    LocalCAS,
    get_tree,
    put_tree,
    read_tree,
    tree_doc,
)

BACKENDS = ["local", pytest.param("s3", marks=pytest.mark.integration)]


@pytest.fixture(params=BACKENDS)
def store(request, tmp_path) -> ArtifactStore:
    if request.param == "local":
        return LocalCAS(tmp_path / "cas")
    return request.getfixturevalue("s3_store")()


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def test_satisfies_the_engine_protocol(store):
    for name in ("put_file", "put_bytes", "open", "get_file", "exists", "stat"):
        assert callable(getattr(store, name)), name


def test_bytes_roundtrip(store, tmp_path):
    data = b"conformance " + os.urandom(16)
    ref = store.put_bytes(data, media_type="text/plain", name="greeting")
    assert (ref.sha256, ref.size, ref.media_type, ref.name) == (
        sha(data),
        len(data),
        "text/plain",
        "greeting",
    )
    assert store.exists(ref.sha256)
    st = store.stat(ref.sha256)
    assert st is not None and st.size == len(data) and st.media_type == "text/plain"
    with store.open(ref.sha256) as f:
        assert f.read() == data
    out = store.get_file(ref.sha256, tmp_path / "sub" / "out.bin")
    assert out.read_bytes() == data
    assert [p.name for p in (tmp_path / "sub").iterdir()] == ["out.bin"]  # no temp file left behind


def test_file_roundtrip(store, tmp_path):
    p = tmp_path / "f.bin"
    p.write_bytes(os.urandom(300_000))
    ref = store.put_file(p, media_type="application/x-f", name="f")
    assert ref.sha256 == sha(p.read_bytes()) and ref.size == 300_000
    assert store.get_file(ref.sha256, tmp_path / "back.bin").read_bytes() == p.read_bytes()


def test_put_is_idempotent_and_keeps_the_first_media_type(store):
    data = os.urandom(64)
    r1 = store.put_bytes(data, media_type="application/x-first")
    r2 = store.put_bytes(data, media_type="application/x-second")
    assert r1.sha256 == r2.sha256
    assert store.stat(r1.sha256).media_type == "application/x-first"


def test_missing_object(store, tmp_path):
    ghost = sha(b"never stored " + os.urandom(8))
    assert not store.exists(ghost) and store.stat(ghost) is None
    with pytest.raises(ArtifactNotFound), store.open(ghost):
        pass
    with pytest.raises(ArtifactNotFound):
        store.get_file(ghost, tmp_path / "x")
    assert not (tmp_path / "x").exists()


def test_tree_roundtrip_is_canonical(store, tmp_path):
    src = tmp_path / "proj"
    (src / "build").mkdir(parents=True)
    (src / "design.yaml").write_text("name: x\n")
    (src / "build" / "REPORT.md").write_text("PASS\n")
    script = src / "run.sh"
    script.write_text("#!/bin/sh\n")
    script.chmod(0o755)
    ref = put_tree(store, src, name="project")
    assert ref.media_type == TREE_MEDIA_TYPE
    files = read_tree(store, ref)
    assert sorted(files) == ["build/REPORT.md", "design.yaml", "run.sh"]
    assert ref.sha256 == sha(tree_doc(files))  # the same directory always gives the same hash
    assert put_tree(store, src, name="project").sha256 == ref.sha256
    dest = tmp_path / "out"
    written = get_tree(store, ref, dest)
    assert {p.relative_to(dest).as_posix() for p in written} == set(files)
    assert (dest / "design.yaml").read_text() == "name: x\n"
    assert os.access(dest / "run.sh", os.X_OK) and not os.access(dest / "design.yaml", os.X_OK)


def test_tree_paths_cannot_leave_the_destination(store, tmp_path):
    evil = store.put_bytes(b"x")
    doc = tree_doc({"../escape.txt": {"sha256": evil.sha256, "size": 1, "mode": 0o644}})
    ref = store.put_bytes(doc, media_type=TREE_MEDIA_TYPE, name="evil")
    with pytest.raises(ValueError):
        get_tree(store, ref, tmp_path / "dest")
    assert not (tmp_path / "escape.txt").exists()
