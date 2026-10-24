"""BYOK endpoints (#14), owner and admin only. The key goes in on PUT and never comes out."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel

from .. import llm_keys
from ..crypto import Keyring
from ..db import Database
from ..tenancy import TenantContext
from .deps import get_db, require_admin

router = APIRouter(prefix="/tenant/llm-keys", tags=["llm-keys"])


class LlmKeyIn(BaseModel):
    # No length/pattern constraints here: a 422 from pydantic would echo the submitted value.
    api_key: str


class LlmKeyOut(BaseModel):
    provider: str
    last4: str
    status: str
    created_at: datetime
    rotated_at: datetime | None
    last_validated_at: datetime | None


def _provider(provider: str) -> str:
    if provider not in llm_keys.PROVIDERS:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown provider")
    return provider


def _keyring(request: Request) -> Keyring:
    ring = request.app.state.keyring
    if ring is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "key storage is not configured")
    return ring


@router.put("/{provider}", response_model=LlmKeyOut)
async def put_key(
    provider: str,
    body: LlmKeyIn,
    request: Request,
    ctx: TenantContext = Depends(require_admin),
    db: Database = Depends(get_db),
):
    """Validate the key with the provider, then store it encrypted (replaces the current one)."""
    provider = _provider(provider)
    ring = _keyring(request)
    try:
        st = await llm_keys.set_key(
            db, ring, request.app.state.key_validator, ctx.tenant_id, ctx.user_id, provider, body.api_key
        )
    except (llm_keys.KeyFormatError, llm_keys.KeyRejected) as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e)) from None
    except llm_keys.ValidatorUnavailable as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"could not validate the key: {e}") from None
    except llm_keys.KeyConflict as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e)) from None
    return LlmKeyOut(**st.__dict__)


@router.get("/{provider}", response_model=LlmKeyOut)
async def get_key_status(
    provider: str, ctx: TenantContext = Depends(require_admin), db: Database = Depends(get_db)
):
    st = await llm_keys.key_status(db, ctx.tenant_id, ctx.user_id, _provider(provider))
    if st is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no key stored")
    return LlmKeyOut(**st.__dict__)


@router.delete("/{provider}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_key(
    provider: str, ctx: TenantContext = Depends(require_admin), db: Database = Depends(get_db)
) -> Response:
    if not await llm_keys.delete_key(db, ctx.tenant_id, ctx.user_id, _provider(provider)):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no key stored")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
