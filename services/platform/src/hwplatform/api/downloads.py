"""Hand out an object of the caller's tenant: a 302 to a presigned URL, or the bytes when no public S3 URL is set."""

from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncIterator

from fastapi import HTTPException, Request, status
from fastapi.responses import RedirectResponse, Response, StreamingResponse

from ..contracts import ArtifactNotFound
from ..tenancy import TenantContext
from .deps import download_store, tenant_store


async def serve_object(
    request: Request, ctx: TenantContext, sha256: str, *, filename: str, media_type: str, inline: bool = False
) -> Response:
    """The caller has already proved (under RLS) that the tenant owns a row naming this sha256."""
    signer = download_store(request, ctx)
    if signer is not None:
        ttl = request.app.state.settings.s3_download_url_ttl_s
        url = signer.presigned_get_url(sha256, expires_s=ttl, filename=filename)
        return RedirectResponse(url, status_code=status.HTTP_302_FOUND)
    store = tenant_store(request, ctx)
    if await asyncio.to_thread(store.stat, sha256) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "object not found")
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", filename.rsplit("/", 1)[-1])

    async def body() -> AsyncIterator[bytes]:
        cm = store.open(sha256)
        try:
            f = await asyncio.to_thread(cm.__enter__)
        except ArtifactNotFound:
            return
        try:
            while chunk := await asyncio.to_thread(f.read, 1 << 20):
                yield chunk
        finally:
            await asyncio.to_thread(cm.__exit__, None, None, None)

    disp = "inline" if inline else "attachment"
    return StreamingResponse(
        body(), media_type=media_type, headers={"Content-Disposition": f'{disp}; filename="{safe}"'}
    )
