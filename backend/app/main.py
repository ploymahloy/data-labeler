from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.config import get_settings
from app.db import get_engine
from app.routers import batches, dev, images, reports


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="Data Labeler API", version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/healthz", tags=["ops"])
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz", tags=["ops"])
    async def readyz() -> dict[str, str]:
        async with get_engine().connect() as conn:
            await conn.execute(text("SELECT 1"))
        return {"status": "ready"}

    app.include_router(batches.router)
    app.include_router(images.router)
    app.include_router(reports.router)
    if settings.auth_mode == "dev":
        app.include_router(dev.router)
    return app


app = create_app()
