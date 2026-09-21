"""Веб-сервис (FastAPI): принимает вебхук Telegram и отвечает на /health."""
import hmac
import logging
from contextlib import asynccontextmanager

from aiogram.types import Update
from fastapi import FastAPI, Header, HTTPException

from app.bot import create_bot
from app.config import BASE_URL, TELEGRAM_WEBHOOK_SECRET
from app.db import check_db, close_db, init_db
from app.logging_setup import setup_logging

setup_logging()
logger = logging.getLogger("app.main")

bot, dp = create_bot()
webhook_set = False


@asynccontextmanager
async def lifespan(app: FastAPI):
    global webhook_set

    await init_db()

    if BASE_URL:
        url = f"{BASE_URL}/telegram/webhook"
        try:
            await bot.set_webhook(
                url=url,
                secret_token=TELEGRAM_WEBHOOK_SECRET,
                allowed_updates=dp.resolve_used_update_types(),
            )
            webhook_set = True
            logger.info("Вебхук Telegram установлен: %s", url)
        except Exception as exc:
            logger.error(
                "Не удалось установить вебхук Telegram: %s: %s",
                type(exc).__name__,
                str(exc)[:200],
            )
    else:
        logger.warning(
            "Публичный адрес сервиса не найден, вебхук Telegram не установлен. "
            "Создайте домен (Settings -> Networking -> Generate Domain) "
            "и перезапустите деплой."
        )

    yield

    await bot.session.close()
    await close_db()


app = FastAPI(
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)


@app.get("/health")
async def health() -> dict:
    db_ok = await check_db()
    return {
        "status": "ok" if db_ok else "degraded",
        "db": db_ok,
        "webhook_set": webhook_set,
    }


@app.post("/telegram/webhook")
async def telegram_webhook(
    update: dict,
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
) -> dict:
    received = (x_telegram_bot_api_secret_token or "").encode()
    expected = TELEGRAM_WEBHOOK_SECRET.encode()

    if not hmac.compare_digest(received, expected):
        raise HTTPException(status_code=403, detail="Invalid webhook secret")

    try:
        telegram_update = Update.model_validate(update, context={"bot": bot})
        await dp.feed_update(bot, telegram_update)
    except Exception as exc:
        logger.exception("Ошибка при обработке обновления Telegram")
        raise HTTPException(
            status_code=500,
            detail="Telegram update processing failed",
        ) from exc

    return {"ok": True}
