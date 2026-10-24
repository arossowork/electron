"""Idempotent infrastructure bootstrap run by `hwplatform-migrate`, after the database upgrade.

- Bucket CORS rule, so a browser can follow a presigned download URL (HWPLATFORM_WEB_ORIGINS).
- Temporal search attributes the API sets when it starts a workflow (ADR 0001, section 4.4).

Both can run any number of times: they set or add only what is missing.
"""

from __future__ import annotations

import asyncio

from .settings import Settings

SEARCH_ATTRIBUTES = ("HwTenantId", "HwProjectId", "HwRunId", "HwRunKind")


def cors_rules(origins: list[str]) -> dict:
    return {
        "CORSRules": [
            {
                "ID": "hwplatform-web",
                "AllowedOrigins": origins,
                "AllowedMethods": ["GET", "HEAD"],
                "AllowedHeaders": ["*"],
                "ExposeHeaders": ["ETag", "Content-Length", "Content-Type", "Content-Disposition"],
                "MaxAgeSeconds": 3600,
            }
        ]
    }


def ensure_bucket_cors(settings: Settings, client=None) -> bool:
    """Set the CORS rule of the artifact bucket to the web origins of the settings. Returns False (does
    nothing) when HWPLATFORM_WEB_ORIGINS is empty; the call replaces the whole rule set, so it is
    idempotent."""
    origins = settings.web_origin_list()
    if not origins:
        return False
    if client is None:
        from .storage import make_s3_client

        client = make_s3_client(settings)
    client.put_bucket_cors(Bucket=settings.s3_bucket, CORSConfiguration=cors_rules(origins))
    return True


async def ensure_search_attributes(settings: Settings) -> list[str]:
    """Register the missing Keyword search attributes in the namespace; returns the names it added."""
    from temporalio.api.enums.v1 import IndexedValueType
    from temporalio.api.operatorservice.v1 import AddSearchAttributesRequest, ListSearchAttributesRequest
    from temporalio.client import Client
    from temporalio.service import RPCError, RPCStatusCode

    client = await asyncio.wait_for(
        Client.connect(settings.temporal_address, namespace=settings.temporal_namespace), timeout=30
    )
    ops = client.operator_service
    have = await ops.list_search_attributes(
        ListSearchAttributesRequest(namespace=settings.temporal_namespace)
    )
    missing = [a for a in SEARCH_ATTRIBUTES if a not in have.custom_attributes]
    if not missing:
        return []
    try:
        await ops.add_search_attributes(
            AddSearchAttributesRequest(
                namespace=settings.temporal_namespace,
                search_attributes={a: IndexedValueType.INDEXED_VALUE_TYPE_KEYWORD for a in missing},
            )
        )
    except RPCError as e:
        if e.status != RPCStatusCode.ALREADY_EXISTS:  # a concurrent migrate won the race: fine
            raise
    return missing
