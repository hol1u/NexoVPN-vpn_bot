import asyncio
import logging
from pathlib import Path
from typing import Any

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
            await connection.execute(
                f"SELECT pg_advisory_xact_lock({SCHEMA_LOCK_ID})"
            )
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


async def get_user_balance(telegram_id: int) -> int:
    """Возвращает баланс пользователя в копейках."""
    async with _get_pool().acquire() as connection:
        balance = await connection.fetchval(
            """
            SELECT balance_kopecks
            FROM users
            WHERE telegram_id = $1
            """,
            telegram_id,
        )

    if balance is None:
        return 0

    return int(balance)


async def add_user_balance(telegram_id: int, amount_kopecks: int) -> None:
    """Увеличивает баланс пользователя на указанную сумму в копейках."""
    if amount_kopecks <= 0:
        raise ValueError("amount_kopecks must be greater than zero")

    async with _get_pool().acquire() as connection:
        await connection.execute(
            """
            UPDATE users
            SET balance_kopecks = balance_kopecks + $1
            WHERE telegram_id = $2
            """,
            amount_kopecks,
            telegram_id,
        )


PLAN_COLUMNS = """
    id, code, type, duration_days, price_kopecks,
    device_limit, is_active, created_at
"""


async def _get_active_plans(plan_type: str) -> list[dict[str, Any]]:
    async with _get_pool().acquire() as connection:
        rows = await connection.fetch(
            f"""
            SELECT {PLAN_COLUMNS}
            FROM plans
            WHERE type = $1 AND is_active = TRUE
            ORDER BY duration_days
            """,
            plan_type,
        )

    return [dict(row) for row in rows]


async def get_active_single_plans() -> list[dict[str, Any]]:
    """Активные обычные тарифы, от короткого срока к длинному."""
    return await _get_active_plans("single")


async def get_active_family_plans() -> list[dict[str, Any]]:
    """Активные семейные тарифы, от короткого срока к длинному."""
    return await _get_active_plans("family")


async def get_plan_by_code(
    code: str,
    only_active: bool = True,
) -> dict[str, Any] | None:
    """Возвращает тариф по стабильному идентификатору (code) или None.

    По умолчанию отключённые тарифы (is_active = FALSE) не возвращаются.
    Цена всегда в копейках (int), без float.
    """
    query = f"SELECT {PLAN_COLUMNS} FROM plans WHERE code = $1"

    if only_active:
        query += " AND is_active = TRUE"

    async with _get_pool().acquire() as connection:
        row = await connection.fetchrow(query, code)

    return dict(row) if row is not None else None
