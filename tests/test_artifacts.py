"""hwflow.artifacts: LocalCAS (content addressing, atomic writes, hash verification) and trees (ADR 0001, 2.1)."""
import hashlib
import json
import os
import stat
import subprocess
import sys

import pytest

from hwflow.artifacts import (TREE_MEDIA_TYPE, ArtifactNotFound, ArtifactRef, LocalCAS, get_tree, put_tree, read_tree,
                              ref_from_dict, ref_to_dict)


def test_put_get_roundtrip_and_layout(tmp_path):
    cas = LocalCAS(tmp_path / "cas")
    ref = cas.put_bytes(b"hello", media_type="text/plain", name="greeting")
    sha = hashlib.sha256(b"hello").hexdigest()
    assert ref == ArtifactRef(sha, 5, "text/plain", "greeting")
    p = tmp_path / "cas" / "sha256" / sha[:2] / sha
    assert p.read_bytes() == b"hello" and not p.stat().st_mode & stat.S_IWUSR  # read-only (0444)
    assert cas.exists(sha) and cas.stat(sha) == ArtifactRef(sha, 5, "text/plain")
    assert cas.get_file(sha, tmp_path / "out" / "g.txt").read_bytes() == b"hello"
    with cas.open(sha) as fh:
        assert fh.read() == b"hello"
    assert ref_from_dict(ref_to_dict(ref)) == ref


def test_put_is_idempotent_and_keeps_the_first_media_type(tmp_path):
    cas = LocalCAS(tmp_path)
    f = tmp_path / "a.bin"
    f.write_bytes(os.urandom(3 * 1024 * 1024))
    r1 = cas.put_file(f, media_type="application/x-a")
    r2 = cas.put_file(f, media_type="application/x-b")
    assert r1.sha256 == r2.sha256 == hashlib.sha256(f.read_bytes()).hexdigest()
    assert cas.stat(r1.sha256).media_type == "application/x-a"
    assert not list((tmp_path / "tmp").iterdir())  # no temporary file left behind


def test_missing_and_corrupt(tmp_path):
    cas = LocalCAS(tmp_path)
    missing = "0" * 64
    assert not cas.exists(missing) and cas.stat(missing) is None
    with pytest.raises(ArtifactNotFound):
        with cas.open(missing):
            pass
    with pytest.raises(ValueError):
        cas.exists("not-a-sha")
    ref = cas.put_bytes(b"good")
    p = cas.local_path(ref.sha256)
    os.chmod(p, 0o644)
    p.write_bytes(b"evil")
    dest = tmp_path / "d" / "x"
    with pytest.raises(ValueError, match="corrupt"):
        cas.get_file(ref.sha256, dest)
    assert not dest.exists() and not list(dest.parent.iterdir())  # nothing half-written


def test_concurrent_writers_of_the_same_bytes(tmp_path):
    code = ("import sys; from hwflow.artifacts import LocalCAS; c = LocalCAS(sys.argv[1]);"
            "[c.put_bytes(b'x' * 1000000) for _ in range(20)]")
    procs = [subprocess.Popen([sys.executable, "-c", code, str(tmp_path)]) for _ in range(4)]
    assert all(p.wait() == 0 for p in procs)
    sha = hashlib.sha256(b"x" * 1000000).hexdigest()
    assert LocalCAS(tmp_path).get_file(sha, tmp_path / "o").stat().st_size == 1000000


def test_tree_roundtrip_is_canonical(tmp_path):
    src = tmp_path / "src"
    (src / "sub").mkdir(parents=True)
    (src / "a.txt").write_text("A")
    (src / "sub" / "b.txt").write_text("B")
    (src / "run.sh").write_text("#!/bin/sh\n")
    os.chmod(src / "run.sh", 0o755)
    (src / "skip.log").write_text("x")
    cas = LocalCAS(tmp_path / "cas")
    ref = put_tree(cas, src, name="t", include=lambda rel: rel.suffix != ".log")
    assert ref.media_type == TREE_MEDIA_TYPE and ref.name == "t"
    files = read_tree(cas, ref)
    assert sorted(files) == ["a.txt", "run.sh", "sub/b.txt"] and files["run.sh"]["mode"] == 0o755
    with cas.open(ref.sha256) as fh:
        doc = json.loads(fh.read())
    assert doc["schema"] == "hwflow.tree/1" and "mtime" not in json.dumps(doc)
    os.utime(src / "a.txt", (1, 1))  # timestamps do not change the tree hash
    assert put_tree(cas, src, name="t", include=lambda rel: rel.suffix != ".log").sha256 == ref.sha256
    out = get_tree(cas, ref, tmp_path / "dst")
    assert len(out) == 3 and (tmp_path / "dst" / "sub" / "b.txt").read_text() == "B"
    assert os.stat(tmp_path / "dst" / "run.sh").st_mode & 0o100


def test_tree_paths_cannot_leave_the_destination(tmp_path):
    cas = LocalCAS(tmp_path / "cas")
    blob = cas.put_bytes(b"x")
    doc = {"schema": "hwflow.tree/1", "files": {"../escape": {"sha256": blob.sha256, "size": 1, "mode": 0o644}}}
    ref = cas.put_bytes(json.dumps(doc).encode(), media_type=TREE_MEDIA_TYPE)
    with pytest.raises(ValueError):
        get_tree(cas, ref, tmp_path / "dst")
    assert not (tmp_path / "escape").exists()
