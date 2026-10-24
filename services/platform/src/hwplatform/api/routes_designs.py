"""Design versions (ADR 0001, section 6): upload, list, get, download a file."""

from __future__ import annotations

import uuid

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
    status,
)
from hwflow.artifacts import ArtifactNotFound, StoreUnavailable
from sqlalchemy import text

from .. import designs
from ..db import Database
from ..tenancy import ProjectRepo, TenantContext
from .deps import get_db, tenant_context, tenant_store
from .downloads import serve_object
from .schemas import DesignVersionDetailOut, DesignVersionOut, ErrorOut, Page, TreeFileOut

router = APIRouter(tags=["design-versions"])

_NO_PROJECT = "project not found"
_NO_VERSION = "design version not found"
_COLS = (
    "id, project_id, seq, content_sha256, design_sha256, requirements_sha256, source, parent_id, message, "
    "created_by, created_at"
)


async def _read_upload(f: UploadFile, cap: int) -> bytes:
    data = await f.read(cap + 1)
    if len(data) > cap:
        raise HTTPException(413, f"upload larger than {cap} bytes")
    return data


@router.post(
    "/projects/{project_id}/design-versions",
    response_model=DesignVersionOut,
    status_code=status.HTTP_201_CREATED,
    summary="Upload a design version",
    description=(
        "Multipart: `design` (design.yaml), `requirements` (requirements.yaml), `notes` (any number of files, "
        "stored as notes/<name>), `bundle` (tar.gz of the project files; explicit parts override its entries). "
        "Returns 201 for a new version and 200 with the existing one when the content is already stored."
    ),
    responses={
        200: {"description": "A version with this content exists", "model": DesignVersionOut},
        404: {"model": ErrorOut},
        413: {"model": ErrorOut},
        422: {"model": ErrorOut},
    },
)
async def upload_design_version(
    project_id: uuid.UUID,
    request: Request,
    response: Response,
    design: UploadFile | None = File(default=None),
    requirements: UploadFile | None = File(default=None),
    notes: list[UploadFile] = File(default_factory=list),
    bundle: UploadFile | None = File(default=None),
    message: str = Form(default="", max_length=2000),
    ctx: TenantContext = Depends(tenant_context),
    db: Database = Depends(get_db),
):
    cfg = request.app.state.settings
    async with db.tx(tenant_id=ctx.tenant_id, user_id=ctx.user_id) as s:
        if await ProjectRepo(s, ctx).get(project_id) is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, _NO_PROJECT)
    cap, nmax = cfg.max_bundle_bytes, cfg.max_bundle_files
    fs = designs.FileSet()
    try:
        if bundle is not None:
            designs.read_bundle(await _read_upload(bundle, cap), fs, max_bytes=cap, max_files=nmax)
        for f in notes:
            name = (f.filename or "").rsplit("/", 1)[-1]
            fs.add(f"notes/{name}", await _read_upload(f, cap), max_bytes=cap, max_files=nmax)
        if requirements is not None:
            fs.add("requirements.yaml", await _read_upload(requirements, cap), max_bytes=cap, max_files=nmax)
        if design is not None:
            fs.add("design.yaml", await _read_upload(design, cap), max_bytes=cap, max_files=nmax)
        dv, created = await designs.store_design_version(
            db,
            tenant_store(request, ctx),
            ctx.tenant_id,
            project_id,
            fs,
            user_id=ctx.user_id,
            message=message,
        )
    except designs.UploadError as e:
        code = 413 if e.too_large else 422
        raise HTTPException(code, str(e)) from e
    except StoreUnavailable as e:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "object store unavailable") from e
    if not created:
        response.status_code = status.HTTP_200_OK
    return await _version_out(db, ctx, dv.id)


async def _version_row(db: Database, ctx: TenantContext, version_id: uuid.UUID | str):
    async with db.tx(tenant_id=ctx.tenant_id, user_id=ctx.user_id) as s:
        row = (
            (
                await s.execute(
                    text(f"SELECT {_COLS} FROM design_versions WHERE tenant_id = :t AND id = :i"),
                    {"t": ctx.tenant_id, "i": uuid.UUID(str(version_id))},
                )
            )
            .mappings()
            .first()
        )
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, _NO_VERSION)
    return row


async def _version_out(db: Database, ctx: TenantContext, version_id) -> dict:
    return dict(await _version_row(db, ctx, version_id))


@router.get(
    "/projects/{project_id}/design-versions",
    response_model=Page[DesignVersionOut],
    summary="List design versions, newest first",
    responses={404: {"model": ErrorOut}},
)
async def list_design_versions(
    project_id: uuid.UUID,
    limit: int = Query(default=50, ge=1, le=200),
    cursor: uuid.UUID | None = None,
    ctx: TenantContext = Depends(tenant_context),
    db: Database = Depends(get_db),
):
    async with db.tx(tenant_id=ctx.tenant_id, user_id=ctx.user_id) as s:
        if await ProjectRepo(s, ctx).get(project_id) is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, _NO_PROJECT)
        rows = (
            (
                await s.execute(
                    text(
                        f"SELECT {_COLS} FROM design_versions WHERE tenant_id = :t AND project_id = :p "
                        "AND (CAST(:c AS uuid) IS NULL OR id < CAST(:c AS uuid)) ORDER BY id DESC LIMIT :n"
                    ),
                    {"t": ctx.tenant_id, "p": project_id, "c": cursor, "n": limit + 1},
                )
            )
            .mappings()
            .all()
        )
    items = [dict(r) for r in rows[:limit]]
    return {"items": items, "next_cursor": str(items[-1]["id"]) if len(rows) > limit else None}


@router.get(
    "/design-versions/{version_id}",
    response_model=DesignVersionDetailOut,
    summary="A design version with its file list",
    responses={404: {"model": ErrorOut}},
)
async def get_design_version(
    version_id: uuid.UUID,
    request: Request,
    ctx: TenantContext = Depends(tenant_context),
    db: Database = Depends(get_db),
):
    row = await _version_row(db, ctx, version_id)
    tree = await _tree(request, db, ctx, version_id)
    files = [TreeFileOut(path=p, **v) for p, v in sorted(tree.items())]
    return {**dict(row), "files": files}


async def _tree(request: Request, db: Database, ctx: TenantContext, version_id: uuid.UUID) -> dict:
    async with db.tx(tenant_id=ctx.tenant_id, user_id=ctx.user_id) as s:
        sha = (
            await s.execute(
                text("SELECT project_tree_sha256 FROM design_versions WHERE tenant_id = :t AND id = :i"),
                {"t": ctx.tenant_id, "i": version_id},
            )
        ).scalar_one_or_none()
    if sha is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, _NO_VERSION)
    try:
        return await designs.read_tree(tenant_store(request, ctx), sha)
    except ArtifactNotFound as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "design version files are gone") from e
    except StoreUnavailable as e:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "object store unavailable") from e


@router.get(
    "/design-versions/{version_id}/files/{path:path}",
    summary="Download one file of a design version",
    description="302 to a presigned URL (the bytes when the API has no public S3 URL).",
    status_code=status.HTTP_302_FOUND,
    responses={404: {"model": ErrorOut}},
)
async def download_design_file(
    version_id: uuid.UUID,
    path: str,
    request: Request,
    ctx: TenantContext = Depends(tenant_context),
    db: Database = Depends(get_db),
):
    tree = await _tree(request, db, ctx, version_id)
    entry = tree.get(path)
    if entry is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "file not found")
    async with db.tx(tenant_id=ctx.tenant_id, user_id=ctx.user_id) as s:  # the artifacts row, under RLS
        media = (
            await s.execute(
                text("SELECT media_type FROM artifacts WHERE tenant_id = :t AND sha256 = :s"),
                {"t": ctx.tenant_id, "s": entry["sha256"]},
            )
        ).scalar_one_or_none()
    if media is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "file not found")
    return await serve_object(request, ctx, entry["sha256"], filename=path, media_type=media)
