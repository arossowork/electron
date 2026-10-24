from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.exc import IntegrityError

from ..db import Database
from ..tenancy import ProjectRepo, TenantContext, slugify
from .deps import get_db, tenant_context
from .schemas import ProjectIn, ProjectOut

router = APIRouter(prefix="/projects", tags=["projects"])

_NOT_FOUND = "project not found"  # same answer for "absent" and "another tenant's"


@router.post("", status_code=status.HTTP_201_CREATED, response_model=ProjectOut)
async def create_project(
    body: ProjectIn, ctx: TenantContext = Depends(tenant_context), db: Database = Depends(get_db)
):
    try:
        async with db.tx(tenant_id=ctx.tenant_id, user_id=ctx.user_id) as s:
            return await ProjectRepo(s, ctx).create(body.name, body.slug or slugify(body.name))
    except IntegrityError as e:
        raise HTTPException(status.HTTP_409_CONFLICT, "a project with this slug exists") from e


@router.get("", response_model=list[ProjectOut])
async def list_projects(ctx: TenantContext = Depends(tenant_context), db: Database = Depends(get_db)):
    async with db.tx(tenant_id=ctx.tenant_id, user_id=ctx.user_id) as s:
        return await ProjectRepo(s, ctx).list()


@router.get("/{project_id}", response_model=ProjectOut)
async def get_project(
    project_id: uuid.UUID, ctx: TenantContext = Depends(tenant_context), db: Database = Depends(get_db)
):
    async with db.tx(tenant_id=ctx.tenant_id, user_id=ctx.user_id) as s:
        p = await ProjectRepo(s, ctx).get(project_id)
    if p is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, _NOT_FOUND)
    return p


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(
    project_id: uuid.UUID, ctx: TenantContext = Depends(tenant_context), db: Database = Depends(get_db)
) -> Response:
    async with db.tx(tenant_id=ctx.tenant_id, user_id=ctx.user_id) as s:
        deleted = await ProjectRepo(s, ctx).delete(project_id)
    if not deleted:
        raise HTTPException(status.HTTP_404_NOT_FOUND, _NOT_FOUND)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
