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
) -> dict[str, Any] | None:
    async with _get_pool().acquire() as connection:
        async with connection.transaction():
            code = promo_code.strip().upper()
            user = await connection.fetchrow(
                """
                SELECT id, promo_activated, promo_used
                FROM users
                WHERE telegram_id = $1
                FOR UPDATE
                """,
                telegram_id,
            )
            if user is None or user["promo_activated"]:
                return None

            promo = await connection.fetchrow(
                """
                  SELECT id, reward_type, reward_value, plan_id,
                      total_limit, per_user_limit
                FROM promo_codes
                WHERE code = $1
                  AND is_active = TRUE
                  AND starts_at <= NOW()
                  AND (ends_at IS NULL OR ends_at > NOW())
                FOR UPDATE
                """,
                code,
            )

            if promo is not None:
                used_by_user = await connection.fetchval(
                    "SELECT COUNT(*) FROM promo_usages WHERE promo_id = $1 AND user_id = $2",
                    promo["id"], user["id"],
                )
                used_total = await connection.fetchval(
                    "SELECT COUNT(*) FROM promo_usages WHERE promo_id = $1",
                    promo["id"],
                )
                if int(used_by_user) >= promo["per_user_limit"]:
                    return None
                if promo["total_limit"] is not None and int(used_total) >= promo["total_limit"]:
                    return None
                await connection.execute(
                    "INSERT INTO promo_usages (promo_id, user_id) VALUES ($1, $2)",
                    promo["id"], user["id"],
                )
            else:
                return None

            updated = await connection.fetchval(
                """
                UPDATE users
                SET promo_activated = TRUE,
                    promo_used = FALSE,
                    promo_reward_type = $2,
                    promo_reward_value = $3,
                    promo_plan_id = $4
                WHERE telegram_id = $1 AND promo_activated = FALSE
                RETURNING telegram_id, promo_reward_type,
                          promo_reward_value, promo_plan_id
                """,
                telegram_id,
                promo["reward_type"],
                promo["reward_value"],
                promo["plan_id"],
            )

    return dict(updated) if updated is not None else None


async def get_user_promo(
    telegram_id: int,
) -> dict[str, Any] | None:
    async with _get_pool().acquire() as connection:
        row = await connection.fetchrow(
            """
            SELECT promo_reward_type, promo_reward_value, promo_plan_id
            FROM users
            WHERE telegram_id = $1
              AND promo_activated = TRUE
              AND promo_used = FALSE
            """,
            telegram_id,
        )

    return dict(row) if row is not None else None


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
            SET promo_used = TRUE,
                promo_reward_type = NULL,
                promo_reward_value = NULL,
                promo_plan_id = NULL
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


async def get_active_subscription(
    telegram_id: int,
) -> dict[str, Any] | None:
    async with _get_pool().acquire() as connection:
        row = await connection.fetchrow(
            """
            SELECT s.id, s.started_at, s.expires_at, s.device_limit,
                   p.code AS plan_code, p.type AS plan_type
            FROM subscriptions AS s
            JOIN users AS u ON u.id = s.user_id
            LEFT JOIN plans AS p ON p.id = s.plan_id
            WHERE u.telegram_id = $1
              AND s.status = 'active'
              AND s.expires_at > NOW()
            ORDER BY s.expires_at DESC
            LIMIT 1
            """,
            telegram_id,
        )

    return dict(row) if row is not None else None


# ============================================================
# ADMINISTRATION
# ============================================================

async def get_admin_stats(period: str = "today") -> dict[str, Any]:
    intervals = {
        "today": "1 day",
        "week": "7 days",
        "month": "1 month",
    }
    interval = intervals.get(period, intervals["today"])
    async with _get_pool().acquire() as connection:
        row = await connection.fetchrow(
            f"""
            SELECT
                (SELECT COUNT(*) FROM users) AS total_users,
                (SELECT COUNT(*) FROM users WHERE last_activity_at >= NOW() - INTERVAL '15 minutes') AS online_users,
                (SELECT COUNT(*) FROM subscriptions WHERE status = 'active' AND expires_at > NOW()) AS active_subscriptions,
                (SELECT COUNT(*) FROM subscriptions) AS total_subscriptions,
                (SELECT COUNT(*) FROM subscriptions WHERE status = 'active' AND expires_at > NOW() AND expires_at <= NOW() + INTERVAL '3 days') AS expiring_subscriptions,
                (SELECT COALESCE(SUM(amount_kopecks), 0) FROM payments WHERE status = 'succeeded' AND created_at >= CURRENT_DATE) AS revenue_today,
                (SELECT COALESCE(SUM(amount_kopecks), 0) FROM payments WHERE status = 'succeeded' AND created_at >= DATE_TRUNC('month', NOW())) AS revenue_month,
                (SELECT COUNT(*) FROM users WHERE created_at >= CURRENT_DATE) AS new_users_today,
                (SELECT COUNT(*) FROM users WHERE referred_by IS NOT NULL AND created_at >= NOW() - INTERVAL '{interval}') AS referrals_period,
                (SELECT COUNT(*) FROM subscriptions WHERE status = 'active' AND expires_at <= NOW()) AS expired_subscriptions
            """,
        )
    return dict(row)


async def get_admin_logs(level: str | None = None, category: str | None = None, telegram_id: int | None = None) -> list[dict[str, Any]]:
    async with _get_pool().acquire() as connection:
        rows = await connection.fetch(
            """
            SELECT id, level, category, message, details, telegram_id, created_at
            FROM admin_logs
            WHERE ($1::TEXT IS NULL OR level = $1)
              AND ($2::TEXT IS NULL OR category = $2)
              AND ($3::BIGINT IS NULL OR telegram_id = $3)
            ORDER BY created_at DESC LIMIT 20
            """,
            level, category, telegram_id,
        )
    return [dict(row) for row in rows]


async def get_subscription_summary() -> dict[str, int]:
    async with _get_pool().acquire() as connection:
        row = await connection.fetchrow(
            """
            SELECT
                COUNT(*) FILTER (WHERE status = 'active' AND expires_at > NOW()) AS active,
                COUNT(*) FILTER (WHERE status = 'active' AND expires_at > NOW() AND expires_at <= NOW() + INTERVAL '3 days') AS expiring,
                COUNT(*) FILTER (WHERE status = 'expired' OR expires_at <= NOW()) AS expired
            FROM subscriptions
            """
        )
    return {key: int(row[key] or 0) for key in ("active", "expiring", "expired")}


async def get_admin_payments(filter_key: str | None = None) -> dict[str, Any]:
    periods = {
        "today": "p.created_at >= CURRENT_DATE",
        "week": "p.created_at >= NOW() - INTERVAL '7 days'",
        "month": "p.created_at >= DATE_TRUNC('month', NOW())",
    }
    statuses = {value: f"p.status = '{value}'" for value in ("succeeded", "pending", "failed", "refunded")}
    condition = periods.get(filter_key, statuses.get(filter_key, "TRUE"))
    async with _get_pool().acquire() as connection:
        summary = await connection.fetchrow(
            f"""
            SELECT
                COALESCE(SUM(p.amount_kopecks) FILTER (WHERE p.status = 'succeeded' AND p.created_at >= CURRENT_DATE), 0) AS today,
                COALESCE(SUM(p.amount_kopecks) FILTER (WHERE p.status = 'succeeded' AND p.created_at >= DATE_TRUNC('month', NOW())), 0) AS month,
                COUNT(*) FILTER (WHERE p.status = 'succeeded') AS succeeded,
                COUNT(*) FILTER (WHERE p.status = 'pending') AS pending,
                COUNT(*) FILTER (WHERE p.status = 'failed') AS failed,
                COUNT(*) FILTER (WHERE p.status = 'refunded') AS refunded
            FROM payments p WHERE {condition}
            """
        )
        rows = await connection.fetch(
            f"""
            SELECT p.id, p.amount_kopecks, p.method, p.status, p.created_at,
                   u.telegram_id, u.username, pl.code AS plan_code
            FROM payments p LEFT JOIN users u ON u.id = p.user_id
                            LEFT JOIN plans pl ON pl.id = p.plan_id
            WHERE {condition}
            ORDER BY p.created_at DESC LIMIT 15
            """
        )
    return {"summary": dict(summary), "rows": [dict(row) for row in rows]}


async def set_plan_active(plan_id: int, active: bool) -> None:
    async with _get_pool().acquire() as connection:
        await connection.execute("UPDATE plans SET is_active = $1 WHERE id = $2", active, plan_id)


async def update_plan(plan_id: int, duration_days: int, price_kopecks: int, device_limit: int) -> None:
    async with _get_pool().acquire() as connection:
        await connection.execute(
            "UPDATE plans SET duration_days = $1, price_kopecks = $2, device_limit = $3 WHERE id = $4",
            duration_days,
            price_kopecks,
            device_limit,
            plan_id,
        )


async def get_all_plans() -> list[dict[str, Any]]:
    async with _get_pool().acquire() as connection:
        rows = await connection.fetch(
            "SELECT id, code, type, duration_days, price_kopecks, device_limit, is_active FROM plans ORDER BY type, duration_days, device_limit"
        )
    return [dict(row) for row in rows]


async def get_promo_summary() -> dict[str, int]:
    async with _get_pool().acquire() as connection:
        row = await connection.fetchrow(
            """
            SELECT COUNT(*) FILTER (WHERE is_active AND (ends_at IS NULL OR ends_at > NOW())) AS active,
                   COUNT(*) FILTER (WHERE NOT is_active) AS paused,
                   COUNT(*) FILTER (WHERE ends_at <= NOW()) AS expired
            FROM promo_codes
            """
        )
    return {key: int(row[key] or 0) for key in ("active", "paused", "expired")}


async def get_promo_codes(mode: str = "active") -> list[dict[str, Any]]:
    conditions = {
        "active": "is_active AND (ends_at IS NULL OR ends_at > NOW())",
        "paused": "NOT is_active",
        "expired": "ends_at IS NOT NULL AND ends_at <= NOW()",
    }
    condition = conditions.get(mode, "TRUE")
    async with _get_pool().acquire() as connection:
        rows = await connection.fetch(
            f"""
            SELECT p.id, p.code, p.reward_type, p.reward_value,
                   p.total_limit, p.per_user_limit, p.starts_at, p.ends_at,
                   p.is_active, pl.code AS plan_code, COUNT(u.id) AS uses
            FROM promo_codes p
            LEFT JOIN plans pl ON pl.id = p.plan_id
            LEFT JOIN promo_usages u ON u.promo_id = p.id
            WHERE {condition}
            GROUP BY p.id, pl.code
            ORDER BY p.created_at DESC
            LIMIT 30
            """
        )
    return [dict(row) for row in rows]


async def set_promo_active(promo_id: int, active: bool) -> None:
    async with _get_pool().acquire() as connection:
        await connection.execute(
            "UPDATE promo_codes SET is_active = $1 WHERE id = $2",
            active,
            promo_id,
        )


async def delete_promo_code(promo_id: int) -> None:
    async with _get_pool().acquire() as connection:
        await connection.execute("DELETE FROM promo_codes WHERE id = $1", promo_id)


async def update_promo_code(
    promo_id: int,
    code: str,
    reward_type: str,
    reward_value: int,
    total_limit: int | None,
    per_user_limit: int,
    starts_at: str,
    ends_at: str | None,
    plan_code: str | None,
) -> None:
    async with _get_pool().acquire() as connection:
        await connection.execute(
            """
            UPDATE promo_codes
            SET code = $1,
                reward_type = $2,
                reward_value = $3,
                total_limit = $4,
                per_user_limit = $5,
                starts_at = ($6::TEXT)::timestamptz,
                ends_at = NULLIF($7::TEXT, '')::timestamptz,
                plan_id = (SELECT id FROM plans WHERE code = NULLIF($8::TEXT, ''))
            WHERE id = $9
            """,
            code.upper(),
            reward_type,
            reward_value,
            total_limit,
            per_user_limit,
            starts_at,
            ends_at or "",
            plan_code or "",
            promo_id,
        )


async def get_subscription_list(mode: str = "all") -> list[dict[str, Any]]:
    conditions = {
        "active": "s.status = 'active' AND s.expires_at > NOW()",
        "expiring": "s.status = 'active' AND s.expires_at BETWEEN NOW() AND NOW() + INTERVAL '3 days'",
        "expired": "s.status <> 'active' OR s.expires_at <= NOW()",
    }
    condition = conditions.get(mode, "TRUE")
    async with _get_pool().acquire() as connection:
        rows = await connection.fetch(
            f"""
            SELECT s.id, u.telegram_id, u.username, p.code AS plan_code,
                   p.type AS plan_type, s.started_at, s.expires_at,
                   s.status, s.device_limit
            FROM subscriptions s
            JOIN users u ON u.id = s.user_id
            LEFT JOIN plans p ON p.id = s.plan_id
            WHERE {condition}
            ORDER BY s.expires_at DESC
            LIMIT 30
            """
        )
    return [dict(row) for row in rows]


async def cancel_subscription(subscription_id: int) -> None:
    async with _get_pool().acquire() as connection:
        await connection.execute(
            "UPDATE subscriptions SET status = 'cancelled' WHERE id = $1",
            subscription_id,
        )


async def get_broadcast_history() -> list[dict[str, Any]]:
    async with _get_pool().acquire() as connection:
        rows = await connection.fetch(
            """
            SELECT id, audience, total_count, sent_count, failed_count,
                   status, created_at, completed_at
            FROM broadcasts ORDER BY created_at DESC LIMIT 20
            """
        )
    return [dict(row) for row in rows]


async def create_promo_code(code: str, reward_type: str, reward_value: int, total_limit: int | None, per_user_limit: int, starts_at: str, ends_at: str | None, plan_code: str | None) -> None:
    async with _get_pool().acquire() as connection:
        await connection.execute(
            """
            INSERT INTO promo_codes (code, reward_type, reward_value, total_limit, per_user_limit, starts_at, ends_at, plan_id)
            VALUES ($1, $2, $3, $4, $5, ($6::TEXT)::timestamptz, NULLIF($7::TEXT, '')::timestamptz,
                    (SELECT id FROM plans WHERE code = NULLIF($8, '')))
            """,
            code.upper(), reward_type, reward_value, total_limit, per_user_limit, starts_at, ends_at or "", plan_code or "",
        )


async def get_referral_summary() -> dict[str, int]:
    async with _get_pool().acquire() as connection:
        row = await connection.fetchrow(
            """
            SELECT
                COUNT(*) FILTER (WHERE referred_by IS NOT NULL) AS total,
                COUNT(*) FILTER (WHERE referred_by IS NOT NULL AND created_at >= CURRENT_DATE) AS today,
                COUNT(*) FILTER (WHERE referred_by IS NOT NULL AND created_at >= DATE_TRUNC('month', NOW())) AS month,
                (SELECT COALESCE(SUM(amount_kopecks), 0) FROM referral_rewards WHERE status = 'accrued') AS bonuses
            FROM users
            """
        )
    return {key: int(row[key] or 0) for key in ("total", "today", "month", "bonuses")}


async def get_referral_top() -> list[dict[str, Any]]:
    async with _get_pool().acquire() as connection:
        rows = await connection.fetch(
            """
            SELECT u.telegram_id, u.username, COUNT(invited.id) AS invited
            FROM users u JOIN users invited ON invited.referred_by = u.id
            GROUP BY u.id ORDER BY invited DESC LIMIT 10
            """
        )
    return [dict(row) for row in rows]


async def get_referral_invites() -> list[dict[str, Any]]:
    async with _get_pool().acquire() as connection:
        rows = await connection.fetch(
            """
            SELECT invited.telegram_id AS invited_id, invited.username AS invited_username,
                   inviter.telegram_id AS inviter_id, inviter.username AS inviter_username,
                   invited.referred_at
            FROM users invited JOIN users inviter ON inviter.id = invited.referred_by
            ORDER BY invited.referred_at DESC LIMIT 30
            """
        )
    return [dict(row) for row in rows]


async def get_referral_rewards() -> list[dict[str, Any]]:
    async with _get_pool().acquire() as connection:
        rows = await connection.fetch(
            """
            SELECT r.id, r.amount_kopecks, r.status, r.created_at,
                   u.telegram_id AS referrer_id
            FROM referral_rewards r JOIN users u ON u.id = r.referrer_id
            ORDER BY r.created_at DESC LIMIT 30
            """
        )
    return [dict(row) for row in rows]


async def get_broadcast_recipients(audience: str) -> list[int]:
    conditions = {
        "all": "TRUE",
        "active": "EXISTS (SELECT 1 FROM subscriptions s WHERE s.user_id = u.id AND s.status = 'active' AND s.expires_at > NOW())",
        "expiring": "EXISTS (SELECT 1 FROM subscriptions s WHERE s.user_id = u.id AND s.status = 'active' AND s.expires_at BETWEEN NOW() AND NOW() + INTERVAL '3 days')",
        "none": "NOT EXISTS (SELECT 1 FROM subscriptions s WHERE s.user_id = u.id AND s.status = 'active' AND s.expires_at > NOW())",
        "referrals": "u.referred_by IS NOT NULL",
    }
    condition = conditions.get(audience, conditions["all"])
    async with _get_pool().acquire() as connection:
        rows = await connection.fetch(f"SELECT telegram_id FROM users u WHERE is_blocked = FALSE AND {condition}")
    return [int(row["telegram_id"]) for row in rows]


async def create_broadcast(admin_id: int, audience: str, message: str, total_count: int) -> int:
    async with _get_pool().acquire() as connection:
        return int(await connection.fetchval(
            "INSERT INTO broadcasts (admin_telegram_id, audience, message, total_count, status) VALUES ($1, $2, $3, $4, 'running') RETURNING id",
            admin_id, audience, message, total_count,
        ))


async def finish_broadcast(broadcast_id: int, sent: int, failed: int) -> None:
    async with _get_pool().acquire() as connection:
        await connection.execute(
            "UPDATE broadcasts SET sent_count = $1, failed_count = $2, status = 'completed', completed_at = NOW() WHERE id = $3",
            sent, failed, broadcast_id,
        )


async def write_admin_log(level: str, category: str, message: str, details: str | None = None, telegram_id: int | None = None) -> None:
    async with _get_pool().acquire() as connection:
        await connection.execute(
            "INSERT INTO admin_logs (level, category, message, details, telegram_id) VALUES ($1, $2, $3, $4, $5)",
            level, category, message, details, telegram_id,
        )
