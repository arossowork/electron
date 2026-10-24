"""Entry points: hwplatform-api, hwplatform-migrate, hwplatform-rotate-master, hwplatform-purge."""

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
    from .settings import get_settings

    s = get_settings()
    s.require_jwt_secret()
    uvicorn.run(create_app(s), host=s.host, port=s.port, proxy_headers=True)


if __name__ == "__main__":
    run()
