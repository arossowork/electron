"""Design versions from an upload (ADR 0001, sections 2.1 and 6).

A version is an immutable, content-addressed snapshot of a project's files: every file is an artifact in the
tenant's S3 prefix, the file list is a tree artifact (`hwflow.tree/1`, media type TREE_MEDIA_TYPE) and the
version's `content_sha256` is the tree's hash. The same files uploaded again give the same hash, so the API
returns the existing version. Only YAML syntax is checked here (no engine import); a design the engine does
not accept fails in the `check` stage.
"""

from __future__ import annotations

import asyncio
import dataclasses
import io
import json
import mimetypes
import tarfile
import uuid
from collections.abc import Mapping

import yaml
from hwflow.artifacts import TREE_MEDIA_TYPE, ArtifactRef
from hwflow.runs import DesignVersionRef
from sqlalchemy import text

from .db import Database
from .runs import PgRunStore
from .storage import S3ArtifactStore

TREE_SCHEMA = "hwflow.tree/1"
MAX_PATH = 512
_YAML_TYPES = {".yaml": "application/yaml", ".yml": "application/yaml", ".md": "text/markdown"}


class UploadError(ValueError):
    """The upload is not acceptable (bad path, bad YAML, too large). The API answers 422 (413 for size)."""

    def __init__(self, message: str, *, too_large: bool = False):
        super().__init__(message)
        self.too_large = too_large


def normalize_path(raw: str) -> str:
    """A safe POSIX relative path: no absolute path, no '..', no empty or '.' segment (a leading './' is
    dropped), no backslash, NUL or control character."""
    if not raw or len(raw) > MAX_PATH:
        raise UploadError(f"bad file path: {raw[:80]!r}")
    p = raw[2:] if raw.startswith("./") else raw
    if p.startswith("/") or "\\" in p or any(ord(c) < 32 or ord(c) == 127 for c in p):
        raise UploadError(f"bad file path: {raw[:80]!r}")
    parts = p.split("/")
    if any(s in ("", ".", "..") for s in parts):
        raise UploadError(f"bad file path: {raw[:80]!r}")
    return p


def media_type_for(path: str) -> str:
    ext = "." + path.rsplit(".", 1)[-1].lower() if "." in path.rsplit("/", 1)[-1] else ""
    return _YAML_TYPES.get(ext) or mimetypes.guess_type(path)[0] or "application/octet-stream"


@dataclasses.dataclass
class FileSet:
    """path -> (bytes, mode); mode is 0o644 or 0o755 so the tree hash does not depend on umask."""

    files: dict[str, tuple[bytes, int]] = dataclasses.field(default_factory=dict)
    total: int = 0

    def add(self, path: str, data: bytes, mode: int = 0o644, *, max_bytes: int, max_files: int) -> None:
        path = normalize_path(path)
        if path in self.files:
            self.total -= len(self.files[path][0])
        elif len(self.files) >= max_files:
            raise UploadError(f"more than {max_files} files", too_large=True)
        self.total += len(data)
        if self.total > max_bytes:
            raise UploadError(f"files are larger than {max_bytes} bytes", too_large=True)
        self.files[path] = (data, 0o755 if mode & 0o111 else 0o644)


def read_bundle(data: bytes, fs: FileSet, *, max_bytes: int, max_files: int) -> None:
    """Add the regular files of a tar.gz to `fs`. Links, devices and bad paths are refused; the size limits
    count uncompressed bytes (a bomb stops at the cap, it is never fully unpacked)."""
    try:
        tf = tarfile.open(fileobj=io.BytesIO(data), mode="r:gz")
    except (tarfile.TarError, OSError, EOFError) as e:
        raise UploadError("bundle is not a valid tar.gz") from e
    with tf:
        try:
            for m in tf:
                if m.isdir():
                    continue  # directories are implied by file paths
                if not m.isreg():
                    raise UploadError(f"bundle member {m.name[:80]!r} is not a regular file")
                if fs.total + m.size > max_bytes:
                    raise UploadError(f"files are larger than {max_bytes} bytes", too_large=True)
                f = tf.extractfile(m)
                body = f.read(m.size + 1) if f else b""
                if len(body) != m.size:
                    raise UploadError(f"bundle member {m.name[:80]!r} is truncated")
                fs.add(m.name, body, m.mode, max_bytes=max_bytes, max_files=max_files)
        except (tarfile.TarError, OSError, EOFError) as e:
            raise UploadError("bundle is not a valid tar.gz") from e


def check_yaml(path: str, data: bytes) -> None:
    try:
        doc = yaml.safe_load(data)
    except (yaml.YAMLError, UnicodeDecodeError) as e:
        raise UploadError(f"{path} is not valid YAML: {str(e)[:200]}") from e
    if not isinstance(doc, dict):
        raise UploadError(f"{path} must be a YAML mapping")


def tree_bytes(entries: Mapping[str, ArtifactRef], modes: Mapping[str, int]) -> bytes:
    """Canonical tree JSON: sorted keys, no timestamps (ADR 2.1)."""
    doc = {
        "schema": TREE_SCHEMA,
        "files": {
            p: {"sha256": r.sha256, "size": r.size, "mode": modes[p]} for p, r in sorted(entries.items())
        },
    }
    return json.dumps(doc, sort_keys=True, separators=(",", ":")).encode()


async def store_design_version(
    db: Database,
    store: S3ArtifactStore,
    tenant_id: uuid.UUID,
    project_id: uuid.UUID,
    fs: FileSet,
    *,
    user_id: uuid.UUID | None,
    message: str = "",
) -> tuple[DesignVersionRef, bool]:
    """Put every file and the tree in S3, then record the version. Returns (version, created); created is
    False when the project already has a version with this content."""
    if "design.yaml" not in fs.files:
        raise UploadError("design.yaml is required")
    check_yaml("design.yaml", fs.files["design.yaml"][0])
    if "requirements.yaml" in fs.files:
        check_yaml("requirements.yaml", fs.files["requirements.yaml"][0])

    sem = asyncio.Semaphore(8)

    async def put(path: str, data: bytes) -> ArtifactRef:
        async with sem:
            return await asyncio.to_thread(store.put_bytes, data, media_type=media_type_for(path), name=path)

    paths = sorted(fs.files)
    refs = dict(zip(paths, await asyncio.gather(*(put(p, fs.files[p][0]) for p in paths)), strict=True))
    tree = tree_bytes(refs, {p: fs.files[p][1] for p in paths})
    tree_ref = await asyncio.to_thread(store.put_bytes, tree, media_type=TREE_MEDIA_TYPE, name="project")

    pg = PgRunStore(db, tenant_id)
    await pg.register_artifacts([*refs.values(), tree_ref])
    async with db.tx(tenant_id=tenant_id) as s:
        existed = (
            await s.execute(
                text("SELECT 1 FROM design_versions WHERE project_id = :p AND content_sha256 = :c"),
                {"p": project_id, "c": tree_ref.sha256},
            )
        ).first()
    dv = await pg.create_design_version(
        project_id,
        content_sha256=tree_ref.sha256,
        design=refs["design.yaml"],
        requirements=refs.get("requirements.yaml"),
        project=tree_ref,
        source="upload",
        message=message,
        created_by=user_id,
    )
    return dv, existed is None


async def read_tree(store: S3ArtifactStore, tree_sha256: str) -> dict[str, dict]:
    """path -> {sha256, size, mode} of a stored tree."""

    def load() -> dict:
        with store.open(tree_sha256) as f:
            return json.loads(f.read())

    doc = await asyncio.to_thread(load)
    return doc["files"]
