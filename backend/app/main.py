from fastapi import Depends, FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import TimeoutError as PoolTimeoutError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_engine, get_session
from app.observability import RequestContextMiddleware, configure_logging, render_metrics
from app.routers import admin, batches, dev, images, reports


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings)
    app = FastAPI(title="Data Labeler API", version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID", "Retry-After"],
    )
    # Outermost, so it sees (and times) everything including CORS and error responses.
    app.add_middleware(RequestContextMiddleware)

    @app.exception_handler(PoolTimeoutError)
    async def pool_exhausted(_: Request, __: PoolTimeoutError) -> JSONResponse:
        # Every DB connection is busy: shed load quickly instead of queueing requests forever.
        return JSONResponse(
            status_code=503,
            content={
                "detail": {
                    "code": "db_busy",
                    "message": "The service is busy, please retry shortly",
                }
            },
            headers={"Retry-After": "2"},
        )

    @app.get("/healthz", tags=["ops"])
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz", tags=["ops"])
    async def readyz() -> dict[str, str]:
        async with get_engine().connect() as conn:
            await conn.execute(text("SELECT 1"))
        return {"status": "ready"}

    @app.get("/metrics", tags=["ops"], include_in_schema=False)
    async def metrics(request: Request, session: AsyncSession = Depends(get_session)) -> Response:
        # For in-cluster scrapers only. Anything that came through the load balancer carries
        # X-Forwarded-For (and the ingress also answers 404 for this path).
        if not settings.metrics_enabled or "x-forwarded-for" in request.headers:
            return JSONResponse(status_code=404, content={"detail": "Not Found"})
        body, content_type = await render_metrics(session)
        return Response(body, media_type=content_type)

    app.include_router(admin.router)
    app.include_router(batches.router)
    app.include_router(images.router)
    app.include_router(reports.router)
    if settings.auth_mode == "dev":
        app.include_router(dev.router)
    return app


app = create_app()
