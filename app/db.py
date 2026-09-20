import asyncio

import asyncpg

from app.config import DATABASE_URL


_pool: asyncpg.Pool | None = None


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
            return
        except Exception as exc:
            last_error = exc

            if attempt < 5:
                await asyncio.sleep(2)

    raise RuntimeError("Could not connect to PostgreSQL") from last_error


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
