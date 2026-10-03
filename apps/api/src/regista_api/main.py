import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Literal

import structlog
from fastapi import Depends, FastAPI, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import text

from regista_api.auth.account import router as account_router
from regista_api.auth.deps import PublicRoute
from regista_api.auth.router import router as auth_router
from regista_api.bots.router import router as bots_router
from regista_api.core.config import Settings, get_settings
from regista_api.core.db import create_engine, create_session_factory
from regista_api.core.email import EmailSender, create_email_sender
from regista_api.core.keys import LocalKeyProvider
from regista_api.core.limits import BodyLimitMiddleware
from regista_api.core.logging import configure_logging
from regista_api.jobs.agent import router as job_agent_router
from regista_api.jobs.router import router as jobs_router
from regista_api.jobs.waiters import JobListener, JobWaiters, asyncpg_dsn
from regista_api.machines.agent import router as agent_router
from regista_api.machines.router import router as machines_router
from regista_api.tenants.clients import router as clients_router
from regista_api.tenants.users import router as users_router

log = structlog.get_logger()

# Responses under these prefixes carry session state, TOTP secrets, recovery codes or the
# agent's nonces and access tokens.
_NO_STORE_PREFIXES = ("/auth", "/account", "/agent")
# What the agent may send is a handful of short fields; anything bigger is refused unread.
_AGENT_BODY_LIMIT = 16 * 1024


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
        # Agents waiting for work (long-polling) share one listening connection (ADR 0020).
        waiters = JobWaiters(settings.agent_max_waiters)
        listener = JobListener(asyncpg_dsn(settings.database_url), waiters)
        app.state.job_waiters = waiters
        app.state.job_listener = listener
        await listener.start()
        try:
            yield
        finally:
            await listener.stop()
            await engine.dispose()

    app = FastAPI(title="Regista API", lifespan=lifespan)

    # Registered before the `http` middleware below, so it sits inside it (the last one added is
    # the outermost). That matters: BaseHTTPMiddleware reads the body inside an anyio task group,
    # which would wrap the 413 in an ExceptionGroup that FastAPI turns into a generic 400.
    app.add_middleware(BodyLimitMiddleware, prefix="/agent/", max_bytes=_AGENT_BODY_LIMIT)

    @app.middleware("http")
    async def http_middleware(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        started = time.perf_counter()
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        if _is_no_store(request.url.path):
            response.headers["Cache-Control"] = "no-store"
        # Access log: method, path and outcome only. Secrets never travel in the URL, and
        # bodies, headers and cookies are never logged.
        log.info(
            "http_request",
            method=request.method,
            path=request.url.path,
            status=response.status_code,
            ms=round((time.perf_counter() - started) * 1000, 1),
        )
        return response

    app.include_router(auth_router)
    app.include_router(account_router)
    app.include_router(clients_router)
    app.include_router(users_router)
    app.include_router(machines_router)
    app.include_router(bots_router)
    app.include_router(jobs_router)
    app.include_router(agent_router)
    app.include_router(job_agent_router)

    @app.exception_handler(RequestValidationError)
    async def validation_error(_request: Request, exc: RequestValidationError) -> JSONResponse:
        # FastAPI's default body echoes the rejected input, which would put passwords and
        # tokens in responses and logs. Keep only where and why.
        errors = [{"loc": list(e["loc"]), "msg": e["msg"], "type": e["type"]} for e in exc.errors()]
        return JSONResponse(status_code=422, content={"detail": errors})

    @app.get("/health", response_model=HealthResponse, dependencies=[Depends(PublicRoute())])
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
