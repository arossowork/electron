"""Entry points: hwplatform-api, -migrate, -relay, -rotate-master, -purge."""

from __future__ import annotations

import sys
from importlib.resources import files


def alembic_config(url: str | None = None, app_role: str | None = None):
    from alembic.config import Config

    from .settings import get_settings

    s = get_settings()
    cfg = Config()
    cfg.set_main_option("script_location", str(files("hwplatform") / "migrations"))
    # ConfigParser interpolation: a %-encoded password must be escaped.
    cfg.set_main_option("sqlalchemy.url", (url or s.migration_database_url).replace("%", "%%"))
    cfg.set_main_option("app_db_role", app_role or s.app_db_role)
    return cfg


def migrate(argv: list[str] | None = None) -> None:
    """hwplatform-migrate [revision]   (default head; 'base' downgrades everything)."""
    from alembic import command

    args = sys.argv[1:] if argv is None else argv
    target = args[0] if args else "head"
    cfg = alembic_config()
    if target == "base" or target.startswith("-"):
        command.downgrade(cfg, target)
    else:
        command.upgrade(cfg, target)
        ensure_event_stream()
        ensure_infra()


def ensure_event_stream() -> None:
    """Create/update the JetStream stream HWFLOW_EVENTS (idempotent); skipped when the NATS URL is empty."""
    import asyncio

    from .events.relay import ensure_stream_at
    from .settings import get_settings

    url = get_settings().nats_url
    if not url:
        return
    try:
        asyncio.run(ensure_stream_at(url))
    except Exception as e:
        sys.exit(f"hwplatform-migrate: database migrated, but JetStream setup at {url} failed: {e!r}")


def ensure_infra() -> None:
    """Bucket CORS rule and Temporal search attributes (idempotent; see bootstrap.py)."""
    import asyncio

    from . import bootstrap
    from .settings import get_settings

    s = get_settings()
    try:
        if bootstrap.ensure_bucket_cors(s):
            print(f"hwplatform-migrate: bucket {s.s3_bucket} CORS allows {', '.join(s.web_origin_list())}")
    except Exception as e:
        sys.exit(f"hwplatform-migrate: database migrated, but the CORS rule on {s.s3_bucket} failed: {e!r}")
    if not s.temporal_address:
        return
    try:
        added = asyncio.run(bootstrap.ensure_search_attributes(s))
    except Exception as e:
        sys.exit(
            f"hwplatform-migrate: database migrated, but the Temporal search attributes at "
            f"{s.temporal_address} failed: {e!r}"
        )
    if added:
        print(f"hwplatform-migrate: registered search attributes {', '.join(added)}")


def relay() -> None:
    """hwplatform-relay: publish the outbox to NATS JetStream (also runs inside hwplatform-api)."""
    import asyncio

    from .events.relay import Relay
    from .observability import configure_logging
    from .settings import get_settings

    s = get_settings()
    configure_logging("relay", level=s.log_level, fmt=s.log_format)
    try:
        asyncio.run(Relay(s).run())
    except KeyboardInterrupt:
        pass


def rotate_master() -> None:
    """hwplatform-rotate-master: re-wrap every tenant's data key under the newest master key.

    Add the new key to HWPLATFORM_MASTER_KEYS first (keep the old ones), run this as the schema owner
    (HWPLATFORM_MIGRATION_DATABASE_URL), then remove old versions that no row uses any more.
    """
    import asyncio

    from .logredact import install_log_redaction
    from .rotation import rotate_all
    from .settings import get_settings

    install_log_redaction()
    from .crypto import CryptoError

    try:
        n, newest = asyncio.run(rotate_all(get_settings()))
    except (
        CryptoError
    ) as e:  # a stored key uses a master key version that is not configured: stop, change nothing
        sys.exit(f"hwplatform-rotate-master: {e}")
    print(f"re-wrapped {n} data key(s) under master key version {newest}")


def purge() -> None:
    """hwplatform-purge: delete expired/revoked refresh tokens and old rate-limit rows (cron it)."""
    import asyncio

    from .logredact import install_log_redaction
    from .purge import purge_all
    from .settings import get_settings

    install_log_redaction()
    r = asyncio.run(purge_all(get_settings()))
    print(" ".join(f"{k}={v}" for k, v in r.items()))


def run() -> None:
    import uvicorn

    from .api.app import create_app
    from .observability import configure_logging
    from .settings import get_settings

    s = get_settings()
    s.require_jwt_secret()
    configure_logging("api", level=s.log_level, fmt=s.log_format)
    # log_config=None: uvicorn keeps our JSON handler; the access line comes from the observability middleware
    app = create_app(s)
    uvicorn.run(app, host=s.host, port=s.port, proxy_headers=True, log_config=None, access_log=False)


if __name__ == "__main__":
    run()
