import asyncio
import logging
import secrets
from pathlib import Path
from typing import Any

import asyncpg

from app.config import DATABASE_URL

logger = logging.getLogger(__name__)

SCHEMA_PATH = Path(__file__).with_name("schema.sql")
SCHEMA_LOCK_ID = 727001

_pool: asyncpg.Pool | None = None


# ============================================================
# DATABASE CORE
# ============================================================

def _get_pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError(
            "Database pool is not initialized: call init_db() first"
        )

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
        raise RuntimeError(
            "Could not connect to PostgreSQL"
        ) from last_error

    await apply_schema()


async def apply_schema() -> None:
    schema_sql = SCHEMA_PATH.read_text(
        encoding="utf-8"
    )

    async with _get_pool().acquire() as connection:
        async with connection.transaction():
            await connection.execute(
                f"""
                SELECT pg_advisory_xact_lock(
                    {SCHEMA_LOCK_ID}
                )
                """
            )

            await connection.execute(
                schema_sql
            )

    logger.info(
        "Схема базы данных применена"
    )


async def close_db() -> None:
    global _pool

    if _pool is not None:
        await _pool.close()
        _pool = None


async def check_db() -> bool:
    if _pool is None:
        return False

    try:
        async with _get_pool().acquire() as connection:
            await connection.fetchval(
                "SELECT 1"
            )

        return True

    except Exception:
        return False


# ============================================================
# USERS
# ============================================================

async def _generate_unique_referral_code(
    connection: asyncpg.Connection,
) -> str:
    """
    Генерирует уникальный referral-код пользователя.

    Пример:
        ref_X7kP2mQa
    """

    for _ in range(30):
        code = "ref_" + secrets.token_urlsafe(6)

        exists = await connection.fetchval(
            """
            SELECT 1
            FROM users
            WHERE referral_code = $1
            """,
            code,
        )

        if not exists:
            return code

    raise RuntimeError(
        "Не удалось создать уникальный referral code"
    )


async def _ensure_referral_code(
    connection: asyncpg.Connection,
    user_id: int,
) -> str:
    """
    Проверяет наличие referral-кода у пользователя.

    Если код уже существует — возвращает его.

    Если код отсутствует — создаёт новый.
    """

    existing = await connection.fetchval(
        """
        SELECT referral_code
        FROM users
        WHERE id = $1
        """,
        user_id,
    )

    if existing:
        return str(existing)

    code = await _generate_unique_referral_code(
        connection
    )

    await connection.execute(
        """
        UPDATE users
        SET referral_code = $1
        WHERE id = $2
        """,
        code,
        user_id,
    )

    return code


async def register_user(
    telegram_id: int,
    username: str | None,
    first_name: str | None,
    referral_code: str | None = None,
) -> None:
    """
    Создаёт пользователя или обновляет существующего.

    При первой регистрации:
    - создаётся уникальный referral-код;
    - при наличии корректного referral_code
      пользователь привязывается к пригласившему;
    - сам себя пригласить нельзя;
    - referred_at записывается автоматически.

    При повторном /start:
    - username обновляется;
    - first_name обновляется;
    - существующий реферер НЕ изменяется;
    - отсутствующий referral-код восстанавливается.
    """

    async with _get_pool().acquire() as connection:
        async with connection.transaction():

            # ------------------------------------------------
            # Проверяем, существует ли пользователь
            # ------------------------------------------------

            existing = await connection.fetchrow(
                """
                SELECT
                    id,
                    referred_by
                FROM users
                WHERE telegram_id = $1
                FOR UPDATE
                """,
                telegram_id,
            )

            # ------------------------------------------------
            # Пользователь уже существует
            # ------------------------------------------------

            if existing is not None:
                await connection.execute(
                    """
                    UPDATE users
                    SET username = $1,
                        first_name = $2
                    WHERE telegram_id = $3
                    """,
                    username,
                    first_name,
                    telegram_id,
                )

                # На случай старых пользователей,
                # у которых referral_code ещё отсутствует.
                await _ensure_referral_code(
                    connection,
                    int(existing["id"]),
                )

                # Важно:
                # referral существующего пользователя
                # здесь намеренно НЕ изменяется.

                return

            # ------------------------------------------------
            # Новый пользователь
            # ------------------------------------------------

            referred_by = None

            clean_referral_code = (
                referral_code.strip()
                if referral_code
                else None
            )

            # ------------------------------------------------
            # Проверяем referral-код пригласившего
            # ------------------------------------------------

            if clean_referral_code:
                referrer = await connection.fetchrow(
                    """
                    SELECT
                        id,
                        telegram_id
                    FROM users
                    WHERE referral_code = $1
                    """,
                    clean_referral_code,
                )

                if (
                    referrer is not None
                    and int(referrer["telegram_id"]) != telegram_id
                ):
                    referred_by = int(
                        referrer["id"]
                    )

            # ------------------------------------------------
            # Создаём собственный referral-код
            # ------------------------------------------------

            new_referral_code = (
                await _generate_unique_referral_code(
                    connection
                )
            )

            # ------------------------------------------------
            # Создаём пользователя
            # ------------------------------------------------
            #
            # ВАЖНО:
            # $5 имеет явный тип BIGINT.
            #
            # Без ::BIGINT PostgreSQL не может определить
            # тип параметра в конструкции:
            #
            #     WHEN $5 IS NOT NULL
            #
            # и возникает:
            #
            #     asyncpg.exceptions.AmbiguousParameterError
            #
            # ------------------------------------------------

            await connection.execute(
                """
                INSERT INTO users (
                    telegram_id,
                    username,
                    first_name,
                    referral_code,
                    referred_by,
                    referred_at
                )
                VALUES (
                    $1,
                    $2,
                    $3,
                    $4,
                    $5,
                    CASE
                        WHEN $5::BIGINT IS NOT NULL
                        THEN NOW()
                        ELSE NULL
                    END
                )
                """,
                telegram_id,
                username,
                first_name,
                new_referral_code,
                referred_by,
            )


async def upsert_user(
    telegram_id: int,
    username: str | None,
    first_name: str | None,
) -> None:
    """
    Совместимость со старыми обработчиками.

    Создаёт пользователя либо обновляет
    username / first_name.
    """

    await register_user(
        telegram_id=telegram_id,
        username=username,
        first_name=first_name,
        referral_code=None,
    )


async def count_users() -> int:
    async with _get_pool().acquire() as connection:
        result = await connection.fetchval(
            "SELECT COUNT(*) FROM users"
        )

    return int(result or 0)


# ============================================================
# BALANCE
# ============================================================

async def get_user_balance(
    telegram_id: int,
) -> int:
    async with _get_pool().acquire() as connection:
        balance = await connection.fetchval(
            """
            SELECT balance_kopecks
            FROM users
            WHERE telegram_id = $1
            """,
            telegram_id,
        )

    return int(balance or 0)


async def add_user_balance(
    telegram_id: int,
    amount_kopecks: int,
) -> None:
    if amount_kopecks <= 0:
        raise ValueError(
            "amount_kopecks must be greater than zero"
        )

    async with _get_pool().acquire() as connection:
        await connection.execute(
            """
            UPDATE users
            SET balance_kopecks =
                balance_kopecks + $1
            WHERE telegram_id = $2
            """,
            amount_kopecks,
            telegram_id,
        )


# ============================================================
# REFERRALS
# ============================================================

async def get_user_referral_code(
    telegram_id: int,
) -> str | None:
    """
    Возвращает referral-код пользователя.

    Если пользователь существует, но код отсутствует,
    код создаётся автоматически.
    """

    async with _get_pool().acquire() as connection:
        async with connection.transaction():

            user_id = await connection.fetchval(
                """
                SELECT id
                FROM users
                WHERE telegram_id = $1
                """,
                telegram_id,
            )

            if user_id is None:
                return None

            return await _ensure_referral_code(
                connection,
                int(user_id),
            )


async def count_referrals(
    telegram_id: int,
) -> int:
    """
    Количество пользователей,
    зарегистрированных по referral-коду данного пользователя.
    """

    async with _get_pool().acquire() as connection:
        result = await connection.fetchval(
            """
            SELECT COUNT(*)
            FROM users AS invited
            JOIN users AS inviter
                ON invited.referred_by = inviter.id
            WHERE inviter.telegram_id = $1
            """,
            telegram_id,
        )

    return int(result or 0)


# ============================================================
# PLANS
# ============================================================

PLAN_COLUMNS = """
    id,
    code,
    type,
    duration_days,
    price_kopecks,
    device_limit,
    is_active,
    created_at
"""


async def _get_active_plans(
    plan_type: str,
) -> list[dict[str, Any]]:
    async with _get_pool().acquire() as connection:
        rows = await connection.fetch(
            f"""
            SELECT {PLAN_COLUMNS}
            FROM plans
            WHERE type = $1
              AND is_active = TRUE
            ORDER BY duration_days
            """,
            plan_type,
        )

    return [
        dict(row)
        for row in rows
    ]


async def get_active_single_plans() -> list[dict[str, Any]]:
    """
    Возвращает активные тарифы Single.
    """

    return await _get_active_plans(
        "single"
    )


async def get_active_family_plans() -> list[dict[str, Any]]:
    """
    Возвращает активные тарифы Family.
    """

    return await _get_active_plans(
        "family"
    )


async def get_plan_by_code(
    code: str,
    only_active: bool = True,
) -> dict[str, Any] | None:
    """
    Возвращает тариф по его code.

    Примеры:
        single_1m
        single_3m
        family_1m
        family_12m
    """

    query = (
        f"SELECT {PLAN_COLUMNS} "
        "FROM plans "
        "WHERE code = $1"
    )

    if only_active:
        query += (
            " AND is_active = TRUE"
        )

    async with _get_pool().acquire() as connection:
        row = await connection.fetchrow(
            query,
            code,
        )

    return (
        dict(row)
        if row is not None
        else None
    )
