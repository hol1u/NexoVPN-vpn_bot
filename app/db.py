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
        async with _pool.acquire() as connection:
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
    Создаёт короткий случайный referral-код.
    Например: X7kP2mQa.
    """

    for _ in range(20):
        code = secrets.token_urlsafe(6)

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


async def register_user(
    telegram_id: int,
    username: str | None,
    first_name: str | None,
    referral_code: str | None = None,
) -> None:
    """
    Создаёт пользователя при первом запуске.

    Если пользователь уже существует:
    - username обновляется;
    - first_name обновляется;
    - существующий referral не меняется.

    Если пользователь новый и referral_code корректный,
    он получает referred_by от пригласившего пользователя.

    Сам себя пригласить нельзя.
    """

    async with _get_pool().acquire() as connection:
        async with connection.transaction():

            existing = await connection.fetchrow(
                """
                SELECT
                    id,
                    referred_by
                FROM users
                WHERE telegram_id = $1
                """,
                telegram_id,
            )

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

                return

            new_referral_code = (
                await _generate_unique_referral_code(
                    connection
                )
            )

            referred_by = None

            if referral_code:
                clean_referral_code = referral_code.strip()
                if clean_referral_code.startswith("ref_"):
                    clean_referral_code = clean_referral_code[4:]

                referrer = await connection.fetchrow(
                    """
                    SELECT id, telegram_id
                    FROM users
                    WHERE referral_code = $1
                       OR referral_code = $2
                    LIMIT 1
                    """,
                    clean_referral_code,
                    "ref_" + clean_referral_code,
                )

                if (
                    referrer is not None
                    and int(referrer["telegram_id"]) != telegram_id
                ):
                    referred_by = referrer["id"]

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
                        WHEN $5::BIGINT IS NOT NULL THEN NOW()
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
    Совместимость со старым кодом.

    Нового пользователя создаёт без referral-кода.
    """

    await register_user(
        telegram_id=telegram_id,
        username=username,
        first_name=first_name,
        referral_code=None,
    )


async def count_users() -> int:
    async with _get_pool().acquire() as connection:
        return await connection.fetchval(
            "SELECT COUNT(*) FROM users"
        )


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

    if balance is None:
        return 0

    return int(balance)


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


async def activate_promo_code(
    telegram_id: int,
    promo_code: str,
) -> bool:
    if promo_code.strip().upper() != "NERONEX":
        return False

    async with _get_pool().acquire() as connection:
        updated = await connection.fetchval(
            """
            UPDATE users
            SET promo_activated = TRUE
            WHERE telegram_id = $1
              AND promo_activated = FALSE
            RETURNING telegram_id
            """,
            telegram_id,
        )

    return updated is not None


async def is_promo_activated(
    telegram_id: int,
) -> bool:
    async with _get_pool().acquire() as connection:
        activated = await connection.fetchval(
            """
            SELECT promo_activated AND NOT promo_used
            FROM users
            WHERE telegram_id = $1
            """,
            telegram_id,
        )

    return bool(activated)


async def touch_user_activity(
    telegram_id: int,
) -> None:
    async with _get_pool().acquire() as connection:
        await connection.execute(
            """
            UPDATE users
            SET last_activity_at = NOW()
            WHERE telegram_id = $1
            """,
            telegram_id,
        )


async def claim_due_reminder_users() -> list[int]:
    async with _get_pool().acquire() as connection:
        rows = await connection.fetch(
            """
            UPDATE users
            SET reminder_sent_at = NOW()
            WHERE is_blocked = FALSE
              AND last_activity_at <= NOW() - INTERVAL '48 hours'
              AND (
                    reminder_sent_at IS NULL
                    OR reminder_sent_at <= NOW() - INTERVAL '48 hours'
              )
            RETURNING telegram_id
            """
        )

    return [int(row["telegram_id"]) for row in rows]


async def consume_promo_code(
    telegram_id: int,
) -> bool:
    async with _get_pool().acquire() as connection:
        updated = await connection.fetchval(
            """
            UPDATE users
            SET promo_used = TRUE
            WHERE telegram_id = $1
              AND promo_activated = TRUE
              AND promo_used = FALSE
            RETURNING telegram_id
            """,
            telegram_id,
        )

    return updated is not None


# ============================================================
# REFERRALS
# ============================================================

async def get_user_referral_code(
    telegram_id: int,
) -> str | None:
    async with _get_pool().acquire() as connection:
        async with connection.transaction():
            row = await connection.fetchrow(
                """
                SELECT id, referral_code
                FROM users
                WHERE telegram_id = $1
                FOR UPDATE
                """,
                telegram_id,
            )

            if row is None:
                return None

            if row["referral_code"]:
                return str(row["referral_code"])

            return await _ensure_referral_code(
                connection,
                int(row["id"]),
            )


async def count_referrals(
    telegram_id: int,
) -> int:
    async with _get_pool().acquire() as connection:
        count = await connection.fetchval(
            """
            SELECT COUNT(*)
            FROM users AS invited
            JOIN users AS inviter
                ON invited.referred_by = inviter.id
            WHERE inviter.telegram_id = $1
            """,
            telegram_id,
        )

    return int(count or 0)


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
    return await _get_active_plans(
        "single"
    )


async def get_active_family_plans() -> list[dict[str, Any]]:
    return await _get_active_plans(
        "family"
    )


async def get_active_plans_by_device_limit(
    device_limit: int,
) -> list[dict[str, Any]]:
    async with _get_pool().acquire() as connection:
        rows = await connection.fetch(
            f"""
            SELECT {PLAN_COLUMNS}
            FROM plans
            WHERE device_limit = $1
              AND is_active = TRUE
            ORDER BY duration_days
            """,
            device_limit,
        )

    return [
        dict(row)
        for row in rows
    ]


async def get_plan_by_code(
    code: str,
    only_active: bool = True,
) -> dict[str, Any] | None:

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
