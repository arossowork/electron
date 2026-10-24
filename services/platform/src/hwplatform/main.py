"""Entry points: hwplatform-api (uvicorn) and hwplatform-migrate (alembic upgrade head)."""

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


def run() -> None:
    import uvicorn

    from .api.app import create_app
    from .settings import get_settings

    s = get_settings()
    s.require_jwt_secret()
    uvicorn.run(create_app(s), host=s.host, port=s.port, proxy_headers=True)


if __name__ == "__main__":
    run()
