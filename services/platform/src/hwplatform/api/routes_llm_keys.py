"""BYOK endpoints (#14), owner and admin only. The credential goes in on PUT and never comes out.

A credential is an Anthropic API key (`credential_type` api_key, the default) or, for the operator's own tenants
only (HWPLATFORM_SUBSCRIPTION_TENANT_IDS), a Claude subscription token (subscription_token); see
hwplatform.credentials."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel
from sqlalchemy import text

from .. import llm_keys
from ..credentials import SUBSCRIPTION_TOKEN, CredentialForbidden, CredentialService
from ..crypto import Keyring
from ..db import Database
from ..gateway import GatewayError, WorkflowGateway
from ..tenancy import TenantContext
from .deps import get_db, get_gateway, require_admin

log = logging.getLogger("hwplatform.api.llm_keys")
router = APIRouter(prefix="/tenant/llm-keys", tags=["llm-keys"])


class LlmKeyIn(BaseModel):
    # No length/pattern constraints here: a 422 from pydantic would echo the submitted value.
    api_key: str
    credential_type: Literal["api_key", "subscription_token"] = "api_key"


class LlmKeyOut(BaseModel):
    provider: str
    last4: str
    status: str
    created_at: datetime
    rotated_at: datetime | None
    last_validated_at: datetime | None
    credential_type: str


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
    gateway: WorkflowGateway = Depends(get_gateway),
):
    """Validate the credential with its adapter, then store it encrypted (replaces the current one; one active
    credential per tenant and provider). A subscription token from a tenant that is not an operator tenant is
    refused (403). Design runs waiting for a valid key are signalled `retry`."""
    provider = _provider(provider)
    ring = _keyring(request)
    st_ = request.app.state
    creds = CredentialService(
        st_.settings,
        db,
        ring,
        validators={"api_key": st_.key_validator, SUBSCRIPTION_TOKEN: st_.token_validator},
    )
    try:
        st = await creds.put(ctx.tenant_id, ctx.user_id, provider, body.credential_type, body.api_key)
    except CredentialForbidden as e:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(e)) from None
    except (llm_keys.KeyFormatError, llm_keys.KeyRejected) as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e)) from None
    except llm_keys.ValidatorUnavailable as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"could not validate the key: {e}") from None
    except llm_keys.KeyConflict as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e)) from None
    await _signal_waiting_runs(db, gateway, ctx)
    return LlmKeyOut(**st.__dict__)


async def _signal_waiting_runs(db: Database, gateway: WorkflowGateway, ctx: TenantContext) -> None:
    """ADR 7.4: every design run of the tenant that waits (it may wait for a key) gets `retry`. A run that waits
    for an answer ignores it. Best effort: the key is stored either way, and a run that missed the signal still
    retries on its next `retry` or fails after its wait."""
    async with db.tx(tenant_id=ctx.tenant_id, user_id=ctx.user_id) as s:
        rows = (
            (
                await s.execute(
                    text(
                        "SELECT workflow_id FROM runs WHERE tenant_id = :t AND kind = 'design' "
                        "AND status = 'waiting' ORDER BY id"
                    ),
                    {"t": ctx.tenant_id},
                )
            )
            .scalars()
            .all()
        )
    for wf in rows:
        try:
            await gateway.signal_retry(wf)
        except GatewayError as e:
            log.warning("retry signal to %s failed: %s", wf, type(e).__name__)


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
