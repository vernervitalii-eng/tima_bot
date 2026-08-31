from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.schema import CreateIndex, CreateTable
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from database.models import Base

engine = None
SessionFactory: async_sessionmaker[AsyncSession] | None = None


async def init_db(url: str) -> None:
    global engine, SessionFactory
    engine = create_async_engine(url, echo=False, pool_pre_ping=True)
    SessionFactory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        # Явный IF NOT EXISTS исключает пересоздание существующих таблиц даже
        # при одновременном старте двух экземпляров приложения.
        for table in Base.metadata.sorted_tables:
            await connection.execute(CreateTable(table, if_not_exists=True))
        # CREATE TABLE не добавляет столбцы в существующую схему. Эти
        # идемпотентные миграции сохраняют данные пользователей ранней версии.
        if url.startswith("sqlite"):
            await _migrate_sqlite(connection)
        elif url.startswith("postgresql"):
            await _migrate_postgresql(connection)
        for table in Base.metadata.sorted_tables:
            for index in table.indexes:
                await connection.execute(CreateIndex(index, if_not_exists=True))


async def _migrate_sqlite(connection) -> None:
    async def columns(table: str) -> set[str]:
        rows = (await connection.execute(text(f"PRAGMA table_info({table})"))).mappings()
        return {row["name"] for row in rows}

    child_columns = await columns("children")
    if "silent_mode" not in child_columns:
        await connection.execute(text("ALTER TABLE children ADD COLUMN silent_mode BOOLEAN NOT NULL DEFAULT 0"))
    user_columns = await columns("users")
    if "display_name" not in user_columns:
        await connection.execute(text("ALTER TABLE users ADD COLUMN display_name VARCHAR(80) NOT NULL DEFAULT 'Член семьи'"))
    activity_columns = await columns("activity_logs")
    if "created_by_user_id" not in activity_columns:
        await connection.execute(text("ALTER TABLE activity_logs ADD COLUMN created_by_user_id INTEGER REFERENCES users(id)"))
    sleep_columns = await columns("sleep_logs")
    if "ended_by_user_id" not in sleep_columns:
        await connection.execute(text("ALTER TABLE sleep_logs ADD COLUMN ended_by_user_id INTEGER REFERENCES users(id)"))
    await connection.execute(text(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_sleep_one_active_per_child "
        "ON sleep_logs(child_id) WHERE end_time IS NULL"
    ))


async def _migrate_postgresql(connection) -> None:
    """Минимальные идемпотентные миграции для ранних production-схем."""
    await connection.execute(text(
        "ALTER TABLE children ADD COLUMN IF NOT EXISTS silent_mode BOOLEAN NOT NULL DEFAULT FALSE"
    ))
    await connection.execute(text(
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS display_name VARCHAR(80) NOT NULL DEFAULT 'Член семьи'"
    ))
    await connection.execute(text(
        "ALTER TABLE activity_logs ADD COLUMN IF NOT EXISTS created_by_user_id INTEGER REFERENCES users(id)"
    ))
    await connection.execute(text(
        "ALTER TABLE sleep_logs ADD COLUMN IF NOT EXISTS ended_by_user_id INTEGER REFERENCES users(id)"
    ))
    await connection.execute(text(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_sleep_one_active_per_child "
        "ON sleep_logs(child_id) WHERE end_time IS NULL"
    ))


@asynccontextmanager
async def db_session() -> AsyncIterator[AsyncSession]:
    if SessionFactory is None:
        raise RuntimeError("База данных не инициализирована")
    async with SessionFactory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def close_db() -> None:
    if engine is not None:
        await engine.dispose()
