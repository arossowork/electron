"""Content-addressed artifacts (ADR 0001, section 2.1): refs, the store protocol, the local store, and trees.

Stdlib only (the platform's contracts import these names; contract "engine contract modules are stdlib-only" in
pyproject.toml). The S3 store lives in `hwplatform.storage`; the engine never imports boto3.

Layout of `LocalCAS(root)`: `<root>/sha256/<aa>/<sha256>` (aa = first two hex), written to a temporary file in the same
folder and renamed (`os.replace`), then made read-only (0444). The media type of the first put is kept next to it in
`<sha256>.type`, so `stat` returns it.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import stat as _stat
import tempfile
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, ContextManager, Protocol

TREE_MEDIA_TYPE = "application/vnd.hwflow.tree+json"
DEFAULT_MEDIA_TYPE = "application/octet-stream"
TREE_SCHEMA = "hwflow.tree/1"
CHUNK = 8 * 1024 * 1024  # read size (ADR 2.1): nothing loads a large file into memory
_SHA = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class ArtifactRef:
    """sha256: 64 lowercase hex of the bytes; name: logical, e.g. "board", "fab", "sim.result.json"."""
    sha256: str
    size: int
    media_type: str = DEFAULT_MEDIA_TYPE
    name: str = ""


class ArtifactNotFound(KeyError):
    pass


class StoreUnavailable(OSError):
    """Retryable (network, 5xx); anything else is a bug."""


class ArtifactStore(Protocol):
    """Content-addressed bytes. Idempotent puts; the store keeps bytes only, the ref carries the metadata."""

    def put_file(self, path: Path, *, media_type: str = ..., name: str = "") -> ArtifactRef: ...  # streams
    def put_bytes(self, data: bytes, *, media_type: str = ..., name: str = "") -> ArtifactRef: ...
    def open(self, sha256: str) -> ContextManager[BinaryIO]: ...  # streaming read; raises ArtifactNotFound
    def get_file(self, sha256: str, dest: Path) -> Path: ...  # atomic (tmp + rename), verifies the hash
    def exists(self, sha256: str) -> bool: ...
    def stat(self, sha256: str) -> ArtifactRef | None: ...  # size and media type as first stored


def ref_to_dict(r: ArtifactRef) -> dict:
    """Wire and manifest form of a ref: {sha256, size, media_type, name}."""
    return {"sha256": r.sha256, "size": r.size, "media_type": r.media_type, "name": r.name}


def ref_from_dict(d: dict) -> ArtifactRef:
    return ArtifactRef(d["sha256"], int(d["size"]), d.get("media_type", DEFAULT_MEDIA_TYPE), d.get("name", ""))


def sha256_file(path: Path) -> tuple[str, int]:
    """(hex digest, size) of a file, read in chunks."""
    h, n = hashlib.sha256(), 0
    with open(path, "rb") as fh:
        while chunk := fh.read(CHUNK):
            h.update(chunk)
            n += len(chunk)
    return h.hexdigest(), n


def _check_sha(sha256: str) -> str:
    if not _SHA.match(sha256 or ""):
        raise ValueError(f"not a sha256: {sha256!r}")
    return sha256


class LocalCAS:
    """The local content-addressed store (CLI and dev). Safe for concurrent writers: a put writes a temporary file and
    renames it over the final path; two writers of the same bytes write the same content."""

    def __init__(self, root: Path | str):
        self.root = Path(root).expanduser()

    def _path(self, sha256: str) -> Path:
        _check_sha(sha256)
        return self.root / "sha256" / sha256[:2] / sha256

    def _write_type(self, p: Path, media_type: str):
        tp = p.with_name(p.name + ".type")
        if not tp.exists():
            fd, tmp = tempfile.mkstemp(dir=p.parent, prefix=p.name + ".", suffix=".ttmp")
            with os.fdopen(fd, "w") as fh:
                fh.write(media_type)
            os.replace(tmp, tp)

    def _put_stream(self, src: BinaryIO, media_type: str, name: str) -> ArtifactRef:
        tmpdir = self.root / "tmp"
        tmpdir.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=tmpdir, prefix="put.")
        h, n = hashlib.sha256(), 0
        try:
            with os.fdopen(fd, "wb") as out:
                while chunk := src.read(CHUNK):
                    h.update(chunk)
                    n += len(chunk)
                    out.write(chunk)
            sha = h.hexdigest()
            p = self._path(sha)
            if p.exists():
                os.unlink(tmp)
            else:
                p.parent.mkdir(parents=True, exist_ok=True)
                os.chmod(tmp, 0o444)
                os.replace(tmp, p)
            self._write_type(p, media_type)
        except BaseException:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise
        return ArtifactRef(sha, n, media_type, name)

    def put_file(self, path: Path, *, media_type: str = DEFAULT_MEDIA_TYPE, name: str = "") -> ArtifactRef:
        with open(path, "rb") as fh:
            return self._put_stream(fh, media_type, name)

    def put_bytes(self, data: bytes, *, media_type: str = DEFAULT_MEDIA_TYPE, name: str = "") -> ArtifactRef:
        sha = hashlib.sha256(data).hexdigest()
        p = self._path(sha)
        if p.exists():
            self._write_type(p, media_type)
            return ArtifactRef(sha, len(data), media_type, name)
        import io
        return self._put_stream(io.BytesIO(data), media_type, name)

    @contextlib.contextmanager
    def open(self, sha256: str) -> Iterator[BinaryIO]:
        p = self._path(sha256)
        try:
            fh = open(p, "rb")
        except FileNotFoundError:
            raise ArtifactNotFound(sha256) from None
        with fh:
            yield fh

    def get_file(self, sha256: str, dest: Path) -> Path:
        """Copy the bytes to `dest` (tmp + rename) and verify the hash on the way; a corrupt object raises."""
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=dest.parent, prefix=dest.name + ".", suffix=".get")
        try:
            h = hashlib.sha256()
            with os.fdopen(fd, "wb") as out, self.open(sha256) as src:
                while chunk := src.read(CHUNK):
                    h.update(chunk)
                    out.write(chunk)
            if h.hexdigest() != sha256:
                raise ValueError(f"artifact {sha256} is corrupt (content hash {h.hexdigest()})")
            os.chmod(tmp, 0o644)
            os.replace(tmp, dest)
        except BaseException:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise
        return dest

    def exists(self, sha256: str) -> bool:
        return self._path(sha256).exists()

    def stat(self, sha256: str) -> ArtifactRef | None:
        p = self._path(sha256)
        try:
            size = p.stat().st_size
        except FileNotFoundError:
            return None
        tp = p.with_name(p.name + ".type")
        mt = tp.read_text() if tp.exists() else DEFAULT_MEDIA_TYPE
        return ArtifactRef(sha256, size, mt)

    def local_path(self, sha256: str) -> Path | None:
        """The stored file itself (read-only), for hardlinking a checkout; None when absent. LocalCAS only."""
        p = self._path(sha256)
        return p if p.exists() else None


# ------------------------------------------------------------------ trees

def tree_doc(files: dict[str, dict]) -> bytes:
    """The canonical bytes of a tree: keys sorted, no timestamps, so the same directory gives the same hash."""
    return json.dumps({"schema": TREE_SCHEMA, "files": dict(sorted(files.items()))}, sort_keys=True,
                      separators=(",", ":")).encode()


def put_tree(store: ArtifactStore, root: Path, *, name: str,
             include: Callable[[Path], bool] = lambda p: True) -> ArtifactRef:
    """Store every regular file under `root` (for which `include(relpath)` holds) and the tree document listing them.
    Symlinks are followed when they point at files; empty directories are not kept."""
    root = Path(root)
    files: dict[str, dict] = {}
    for p in sorted(root.rglob("*")) if root.is_dir() else []:
        rel = p.relative_to(root)
        if not p.is_file() or not include(rel):
            continue
        ref = store.put_file(p)
        mode = 0o755 if p.stat().st_mode & _stat.S_IXUSR else 0o644
        files[rel.as_posix()] = {"sha256": ref.sha256, "size": ref.size, "mode": mode}
    return store.put_bytes(tree_doc(files), media_type=TREE_MEDIA_TYPE, name=name)


def read_tree(store: ArtifactStore, ref: ArtifactRef | str) -> dict[str, dict]:
    """The files of a tree: {posix relpath: {sha256, size, mode}}."""
    sha = ref if isinstance(ref, str) else ref.sha256
    with store.open(sha) as fh:
        doc = json.loads(fh.read())
    if doc.get("schema") != TREE_SCHEMA:
        raise ValueError(f"artifact {sha} is not a {TREE_SCHEMA} tree")
    return doc["files"]


def get_tree(store: ArtifactStore, ref: ArtifactRef, dest: Path) -> list[Path]:
    """Write the files of a tree under `dest` (each atomic and hash-verified). Paths that leave `dest` are refused."""
    dest = Path(dest)
    out = []
    for rel, e in read_tree(store, ref).items():
        p = dest / rel
        if ".." in Path(rel).parts or Path(rel).is_absolute():
            raise ValueError(f"tree path leaves its root: {rel!r}")
        store.get_file(e["sha256"], p)
        os.chmod(p, e.get("mode", 0o644))
        out.append(p)
    return out
