"""`hwplatform-worker --queues ...`: the Temporal worker process (ADR 0001, 4.2 and 8). See `main`."""

from .main import build_workers, main, run

__all__ = ["build_workers", "main", "run"]
