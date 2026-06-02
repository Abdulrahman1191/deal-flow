from __future__ import annotations
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import NullPool

from app.config import settings

engine = create_async_engine(settings.database_url, echo=False)
AsyncSessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

# Use NullPool for Celery tasks — each asyncio.run() call creates a fresh event loop,
# and asyncpg connections cannot be shared across loops.
celery_engine = create_async_engine(settings.database_url, echo=False, poolclass=NullPool)
CelerySessionLocal = async_sessionmaker(celery_engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        yield session
