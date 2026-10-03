"""`regista-worker`: the process that runs background jobs, apart from the API (ADR 0018).

It has the same settings as the API and connects as `regista_app`. Logs go through the same
redaction as the API's. Stop it with Ctrl+C or SIGTERM: it finishes the running job first.
"""

import asyncio

import structlog

from regista_api.core.config import Settings, get_settings
from regista_api.core.db import create_engine, create_session_factory
from regista_api.core.logging import configure_logging
from regista_api.tasks.app import build_app

log = structlog.get_logger()


async def run(settings: Settings) -> None:
    engine = create_engine(settings.database_url)
    try:
        app = build_app(settings, create_session_factory(engine))
        async with app.open_async():
            log.info("worker_started", sweep_cron=settings.machine_sweep_cron)
            await app.run_worker_async(name="regista-worker")
            log.info("worker_stopped")
    finally:
        await engine.dispose()


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level, json=settings.environment == "prod")
    # psycopg's async mode does not work on Windows' default (Proactor) event loop.
    asyncio.run(run(settings), loop_factory=asyncio.SelectorEventLoop)


if __name__ == "__main__":
    main()
