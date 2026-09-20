import asyncio

from fastapi import FastAPI, Header, HTTPException
from aiogram import Bot, Dispatcher
from aiogram.types import Update

from app.config import (
    TELEGRAM_BOT_TOKEN,
    TELEGRAM_WEBHOOK_SECRET,
)
from app.db import check_db, close_db, init_db


app = FastAPI()

bot = Bot(token=TELEGRAM_BOT_TOKEN)
dp = Dispatcher()


@app.on_event("startup")
async def startup() -> None:
    await init_db()

    await bot.set_webhook(
        url=f"{get_base_url()}/telegram/webhook",
        secret_token=TELEGRAM_WEBHOOK_SECRET,
    )


@app.on_event("shutdown")
async def shutdown() -> None:
    await bot.delete_webhook()
    await bot.session.close()
    await close_db()


def get_base_url() -> str:
    import os

    base_url = os.getenv("BASE_URL", "").rstrip("/")

    if not base_url:
        raise RuntimeError("BASE_URL is not configured")

    return base_url


@app.get("/health")
async def health() -> dict:
    return {
        "status": "ok",
        "db": await check_db(),
    }


@app.post("/telegram/webhook")
async def telegram_webhook(
    update: dict,
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
) -> dict:
    if x_telegram_bot_api_secret_token != TELEGRAM_WEBHOOK_SECRET:
        raise HTTPException(status_code=403, detail="Invalid webhook secret")

    telegram_update = Update.model_validate(update)

    await dp.feed_update(
        bot,
        telegram_update,
    )

    return {"ok": True}
