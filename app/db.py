import asyncio
import logging
from pathlib import Path

import asyncpg

from app.config import DATABASE_URL

logger = logging.getLogger(__name__)

# Файл со структурой таблиц лежит рядом: app/schema.sql
SCHEMA_PATH = Path(__file__).with_name("schema.sql")

# Произвольное число-замок: два одновременных запуска бота
# не будут создавать таблицы в один и тот же момент.
SCHEMA_LOCK_ID = 727001

_pool: asyncpg.Pool | None = None


def _get_pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("Database pool is not initialized: call init_db() first")
    return _pool


async def init_db() -> None:
    global _pool

    last_error = None

    for attempt in range(1, 6):
        try:
            _pool = await asyncpg.create_pool(
                DATABASE_URL,
                min_size=1,
                max_size=5,
            )
            break
        except Exception as exc:
            last_error = exc

            if attempt < 5:
                await asyncio.sleep(2)
    else:
        raise RuntimeError("Could not connect to PostgreSQL") from last_error

    await apply_schema()


async def apply_schema() -> None:
    """Создаёт таблицы из app/schema.sql, если их ещё нет."""
    schema_sql = SCHEMA_PATH.read_text(encoding="utf-8")

    async with _get_pool().acquire() as connection:
        async with connection.transaction():
            await connection.execute(f"SELECT pg_advisory_xact_lock({SCHEMA_LOCK_ID})")
            await connection.execute(schema_sql)

    logger.info("Схема базы данных применена")


async def close_db() -> None:
    global _pool

    if _pool is not None:
        await _pool.close()
        _pool = None


async def check_db() -> bool:
    if _pool is None:
        return False

    try:
        async with _pool.acquire() as connection:
            await connection.fetchval("SELECT 1")
        return True
    except Exception:
        return False


async def upsert_user(
    telegram_id: int,
    username: str | None,
    first_name: str | None,
) -> None:
    """Сохраняет пользователя Telegram.

    Если пользователь с таким telegram_id уже есть, дубликат не создаётся:
    обновляются username и first_name.
    """
    async with _get_pool().acquire() as connection:
        await connection.execute(
            """
            INSERT INTO users (telegram_id, username, first_name)
            VALUES ($1, $2, $3)
            ON CONFLICT (telegram_id) DO UPDATE
            SET username = EXCLUDED.username,
                first_name = EXCLUDED.first_name
            """,
            telegram_id,
            username,
            first_name,
        )


async def count_users() -> int:
    """Общее количество зарегистрированных пользователей."""
    async with _get_pool().acquire() as connection:
        return await connection.fetchval("SELECT COUNT(*) FROM users")
