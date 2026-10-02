from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Literal

import structlog
from fastapi import FastAPI, Response, status
from pydantic import BaseModel
from sqlalchemy import text

from regista_api.core.config import Settings, get_settings
from regista_api.core.db import create_engine, create_session_factory
from regista_api.core.logging import configure_logging

log = structlog.get_logger()


class HealthResponse(BaseModel):
    status: Literal["ok", "unavailable"]


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        configure_logging(settings.log_level, json=settings.environment == "prod")
        engine = create_engine(settings.database_url)
        app.state.session_factory = create_session_factory(engine)
        try:
            yield
        finally:
            await engine.dispose()

    app = FastAPI(title="Regista API", lifespan=lifespan)

    @app.get("/health", response_model=HealthResponse)
    async def health(response: Response) -> HealthResponse:
        # Uses the runtime role (regista_app) with no tenant: only checks connectivity.
        try:
            async with app.state.session_factory() as session:
                await session.execute(text("SELECT 1"))
        except Exception:
            # Details go to the log, never to the response.
            log.exception("health_check_failed")
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
            return HealthResponse(status="unavailable")
        return HealthResponse(status="ok")

    return app


def __getattr__(name: str) -> FastAPI:
    # `uvicorn regista_api.main:app` builds the app on first access, so importing this
    # module (tests, tooling) never requires a configured environment.
    if name == "app":
        return create_app()
    raise AttributeError(name)
