"""The per-run agent workspace (ADR 0001, 7.1): materialized from a project tree in S3, saved back as one.

Layout under the worker dir: `agent/<run_id>/<stage>/ws` (the session's cwd, the project files), `.../home`
(HOME and CLAUDE_CONFIG_DIR of the subprocess: the session transcripts) and `.../bin` (the launcher stub). The
paths are deterministic, so a retried activity on another worker resumes the same session (the transcript
directory name depends on the cwd).
"""

from __future__ import annotations

import io
import os
import shutil
import tarfile
from pathlib import Path, PurePosixPath

from hwflow.artifacts import ArtifactRef, ArtifactStore, get_tree, put_tree

from .sandbox import REF_DIR

SESSION_MEDIA_TYPE = "application/x-hwflow-agent-session+tar.gz"
# Never materialized and never saved: Claude Code configuration that an uploaded design could use to add hooks
# or MCP servers, and the read-only reference copies.
NEVER = (".claude", ".mcp.json", REF_DIR)
# Saved in the workspace snapshot but not in a design version: build outputs (except the approval baseline the
# check stage reads). Downloaded datasheets (`datasheets/`) are project files and go into the version.
NOT_IN_VERSION = ("build",)
KEEP_IN_VERSION = ("build/approved.json",)
REFERENCE_FILES = ("CLAUDE.md", "docs/design-yaml.md", "docs/hwsw-codesign.md", "docs/layout-flow.md")
REFERENCE_DIRS = ("fw/framework",)


class Dirs:
    def __init__(self, root: Path, run_id: str, stage: str):
        self.base = Path(root) / "agent" / run_id / stage
        self.ws = self.base / "ws"
        self.home = self.base / "home"
        self.config = self.home / ".claude"
        self.bin = self.base / "bin"
        self.tmp = self.base / "tmp"

    def reset(self) -> None:
        shutil.rmtree(self.base, ignore_errors=True)
        for d in (self.ws, self.config, self.bin, self.tmp):
            d.mkdir(parents=True, exist_ok=True)

    def remove(self) -> None:
        shutil.rmtree(self.base, ignore_errors=True)


def _top(rel: Path | str) -> str:
    return PurePosixPath(str(rel)).parts[0] if str(rel) not in ("", ".") else ""


def in_snapshot(rel: Path, root: Path) -> bool:
    return _top(rel) not in NEVER and not (root / rel).is_symlink()


def in_version(rel: Path, root: Path) -> bool:
    posix = PurePosixPath(rel).as_posix()
    if posix in KEEP_IN_VERSION:
        return not (root / rel).is_symlink()
    return in_snapshot(rel, root) and _top(rel) not in NOT_IN_VERSION


def materialize(store: ArtifactStore, tree: ArtifactRef, ws: Path) -> None:
    get_tree(store, tree, ws)
    for name in NEVER:
        p = ws / name
        if p.is_dir() and not p.is_symlink():
            shutil.rmtree(p)
        elif p.exists() or p.is_symlink():
            p.unlink()


def install_references(prompts_root: Path, ws: Path) -> None:
    """Read-only copies of the docs and the firmware framework the agent files name, under `.hwflow-ref/`."""
    ref = ws / REF_DIR
    for rel in REFERENCE_FILES:
        src = prompts_root / rel
        if src.is_file():
            (ref / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, ref / rel)
    for rel in REFERENCE_DIRS:
        src = prompts_root / rel
        if src.is_dir():
            shutil.copytree(src, ref / rel, dirs_exist_ok=True, symlinks=False)
    for dirpath, _, files in os.walk(ref):
        for f in files:
            os.chmod(Path(dirpath) / f, 0o444)


def snapshot(store: ArtifactStore, ws: Path) -> ArtifactRef:
    """The workspace as a tree (what the next stage and a resumed session start from)."""
    return put_tree(store, ws, name="workspace", include=lambda rel: in_snapshot(rel, ws))


def version_tree(store: ArtifactStore, ws: Path) -> ArtifactRef:
    """The project files as a design version tree (no build outputs, no reference copies)."""
    return put_tree(store, ws, name="project", include=lambda rel: in_version(rel, ws))


def save_session(store: ArtifactStore, config_dir: Path) -> ArtifactRef | None:
    """The session transcripts (CLAUDE_CONFIG_DIR/projects) as a tar.gz artifact; None when there are none."""
    projects = Path(config_dir) / "projects"
    if not projects.is_dir() or not any(projects.rglob("*.jsonl")):
        return None
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for p in sorted(projects.rglob("*")):
            if p.is_file() and not p.is_symlink():
                data = p.read_bytes()
                if p.suffix == ".jsonl":  # the CLI may be appending: keep whole lines only
                    data = data[: data.rfind(b"\n") + 1]
                info = tarfile.TarInfo(str(p.relative_to(config_dir)))
                info.size, info.mode = len(data), 0o600
                tf.addfile(info, io.BytesIO(data))
    return store.put_bytes(buf.getvalue(), media_type=SESSION_MEDIA_TYPE, name="session")


def restore_session(store: ArtifactStore, ref: ArtifactRef, config_dir: Path) -> None:
    with store.open(ref.sha256) as fh:
        data = fh.read()
    config_dir = Path(config_dir)
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tf:
        for m in tf.getmembers():
            parts = PurePosixPath(m.name).parts
            if (
                not m.isreg()
                or not parts
                or parts[0] != "projects"
                or ".." in parts
                or m.name.startswith("/")
            ):
                continue  # only regular transcript files under projects/
            dest = config_dir / m.name
            dest.parent.mkdir(parents=True, exist_ok=True)
            src = tf.extractfile(m)
            if src is not None:
                dest.write_bytes(src.read())


def apply_tree(store: ArtifactStore, tree: ArtifactRef, ws: Path) -> list[str]:
    """Write the files of `tree` that differ from the workspace (an engine command's output). Returns the
    paths."""
    from hwflow.artifacts import read_tree, sha256_file

    changed = []
    for rel, e in sorted(read_tree(store, tree).items()):
        if _top(rel) in NEVER or ".." in PurePosixPath(rel).parts or rel.startswith("/"):
            continue
        p = ws / rel
        if p.is_symlink():  # never write through a link
            p.unlink()
        if p.is_file() and sha256_file(p)[0] == e["sha256"]:
            continue
        store.get_file(e["sha256"], p)
        changed.append(rel)
    return changed
