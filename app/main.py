"""Веб-сервис (FastAPI): принимает вебхук Telegram и отвечает на /health."""
import hmac
import asyncio
import logging
from contextlib import asynccontextmanager

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Update
from fastapi import FastAPI, Header, HTTPException

from app.bot import create_bot
from app.config import BASE_URL, TELEGRAM_WEBHOOK_SECRET
from app.db import (
    check_db,
    claim_due_reminder_users,
    close_db,
    init_db,
)
from app.logging_setup import setup_logging

setup_logging()
logger = logging.getLogger("app.main")

bot, dp = create_bot()
webhook_set = False
REMINDER_INTERVAL_SECONDS = 300


def build_balance_reminder_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="💸 Пополнить баланс",
                    callback_data="menu:topup",
                )
            ]
        ]
    )


async def send_balance_reminders() -> None:
    while True:
        try:
            user_ids = await claim_due_reminder_users()
            for user_id in user_ids:
                try:
                    await bot.send_message(
                        chat_id=user_id,
                        text="😢 Мы ждем тебя у нас",
                        reply_markup=build_balance_reminder_menu(),
                    )
                except Exception:
                    logger.exception(
                        "Не удалось отправить напоминание telegram_id=%s",
                        user_id,
                    )
        except Exception:
            logger.exception("Ошибка фоновой отправки напоминаний")

        await asyncio.sleep(REMINDER_INTERVAL_SECONDS)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global webhook_set

    await init_db()
    reminder_task = asyncio.create_task(send_balance_reminders())

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

    reminder_task.cancel()
    try:
        await reminder_task
    except asyncio.CancelledError:
        pass

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
