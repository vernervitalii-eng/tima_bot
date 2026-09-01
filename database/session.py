from __future__ import annotations

import asyncio
import sqlite3
import tempfile
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, closing
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.schema import CreateIndex, CreateTable
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from database.models import Base

engine = None
SessionFactory: async_sessionmaker[AsyncSession] | None = None
db_lock = asyncio.Lock()


async def init_db(url: str) -> None:
    global engine, SessionFactory
    async with db_lock:
        if engine is not None:
            await engine.dispose()
        connect_args = {"timeout": 30.0} if url.startswith("sqlite") else {}
        engine = create_async_engine(url, echo=False, pool_pre_ping=True, connect_args=connect_args)
        SessionFactory = async_sessionmaker(engine, expire_on_commit=False)
        async with engine.begin() as connection:
            if url.startswith("sqlite"):
                await connection.execute(text("PRAGMA busy_timeout=30000"))
                await connection.execute(text("PRAGMA foreign_keys=ON"))
                await connection.execute(text("PRAGMA journal_mode=WAL"))
                await connection.execute(text("PRAGMA synchronous=NORMAL"))
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
    async with db_lock:
        async with SessionFactory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise


async def close_db() -> None:
    global engine, SessionFactory
    async with db_lock:
        if engine is not None:
            await engine.dispose()
        engine = None
        SessionFactory = None


async def read_sqlite_bytes(db_path: str) -> bytes:
    """Создаёт согласованную online-копию SQLite, включая данные из WAL."""
    if not db_path:
        raise RuntimeError("Для PostgreSQL файловый backup недоступен")
    path = Path(db_path)
    if not path.exists() or not path.is_file():
        raise FileNotFoundError(f"Файл базы данных не найден: {path}")
    def create_backup() -> bytes:
        temporary_name: str | None = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as temporary:
                temporary_name = temporary.name
            with closing(sqlite3.connect(path, timeout=30.0)) as source:
                with closing(sqlite3.connect(temporary_name, timeout=30.0)) as target:
                    source.backup(target)
            return Path(temporary_name).read_bytes()
        finally:
            if temporary_name:
                Path(temporary_name).unlink(missing_ok=True)

    async with db_lock:
        return await asyncio.to_thread(create_backup)
