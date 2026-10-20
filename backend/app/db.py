import ssl
from collections.abc import AsyncIterator
from functools import lru_cache
from typing import Any

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import Settings, get_settings


def build_connect_args(settings: Settings) -> dict[str, Any]:
    """TLS settings for asyncpg. `verify-full` checks the CA and the server hostname."""
    if settings.db_ssl_mode == "disable":
        return {}
    context = ssl.create_default_context(cafile=settings.db_ssl_ca_file or None)
    if settings.db_ssl_mode == "require":
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
    return {"ssl": context}


@lru_cache
def get_engine() -> AsyncEngine:
    settings = get_settings()
    return create_async_engine(
        settings.database_url,
        pool_size=settings.effective_pool_size,
        max_overflow=settings.effective_max_overflow,
        pool_timeout=settings.db_pool_timeout,
        pool_pre_ping=True,
        connect_args=build_connect_args(settings),
    )


@lru_cache
def get_export_engine() -> AsyncEngine:
    """Separate, small pool for streaming exports (see Settings.export_pool_size)."""
    settings = get_settings()
    return create_async_engine(
        settings.database_url,
        pool_size=settings.export_pool_size,
        max_overflow=0,
        pool_timeout=settings.db_pool_timeout,
        pool_pre_ping=True,
        connect_args=build_connect_args(settings),
    )


@lru_cache
def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(get_engine(), expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    """Request-scoped session. Each endpoint commits explicitly via `session.begin()`."""
    async with get_sessionmaker()() as session:
        yield session
