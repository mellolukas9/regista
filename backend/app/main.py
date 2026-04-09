from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.routers import auth, bots, clients, runs, schedules


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield


app = FastAPI(
    title="Regista API",
    version="0.1.0",
    description="RPAaaS — orquestração de bots Python/Playwright",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router, prefix="/api")
app.include_router(clients.router, prefix="/api")
app.include_router(bots.router, prefix="/api")
app.include_router(runs.router, prefix="/api")
app.include_router(schedules.router, prefix="/api")


@app.get("/api/health", tags=["health"])
async def health_check():
    return {"status": "ok", "service": "regista-api"}
