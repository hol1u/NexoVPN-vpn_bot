from aiogram import Bot, Dispatcher

from app.bot.handlers import start
from app.config import TELEGRAM_BOT_TOKEN


def create_bot() -> tuple[Bot, Dispatcher]:
    bot = Bot(token=TELEGRAM_BOT_TOKEN)
    dp = Dispatcher()

    dp.include_router(start.router)

    return bot, dp
