from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Literal

import structlog
from fastapi import FastAPI, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import text

from regista_api.auth.router import router as auth_router
from regista_api.core.config import Settings, get_settings
from regista_api.core.db import create_engine, create_session_factory
from regista_api.core.email import EmailSender, create_email_sender
from regista_api.core.keys import LocalKeyProvider
from regista_api.core.logging import configure_logging

log = structlog.get_logger()

# Responses under these prefixes carry session state, TOTP secrets or recovery codes.
_NO_STORE_PREFIXES = ("/auth", "/account")


class HealthResponse(BaseModel):
    status: Literal["ok", "unavailable"]


def _is_no_store(path: str) -> bool:
    return any(path == p or path.startswith(f"{p}/") for p in _NO_STORE_PREFIXES)


def create_app(
    settings: Settings | None = None, *, email_sender: EmailSender | None = None
) -> FastAPI:
    settings = settings or get_settings()
    settings.validate_for_runtime()
    key_provider = LocalKeyProvider(settings.master_key)
    sender = email_sender or create_email_sender(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        configure_logging(settings.log_level, json=settings.environment == "prod")
        engine = create_engine(settings.database_url)
        app.state.settings = settings
        app.state.session_factory = create_session_factory(engine)
        app.state.key_provider = key_provider
        app.state.email_sender = sender
        try:
            yield
        finally:
            await engine.dispose()

    app = FastAPI(title="Regista API", lifespan=lifespan)

    @app.middleware("http")
    async def security_headers(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        if _is_no_store(request.url.path):
            response.headers["Cache-Control"] = "no-store"
        return response

    app.include_router(auth_router)

    @app.exception_handler(RequestValidationError)
    async def validation_error(_request: Request, exc: RequestValidationError) -> JSONResponse:
        # FastAPI's default body echoes the rejected input, which would put passwords and
        # tokens in responses and logs. Keep only where and why.
        errors = [{"loc": list(e["loc"]), "msg": e["msg"], "type": e["type"]} for e in exc.errors()]
        return JSONResponse(status_code=422, content={"detail": errors})

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
