import logging

from aiogram import BaseMiddleware, Bot, Dispatcher
from aiogram.types import CallbackQuery, Message
from typing import Any, Awaitable, Callable

from app.bot.handlers import start
from app.config import TELEGRAM_BOT_TOKEN
from app.db import touch_user_activity

logger = logging.getLogger(__name__)


class ActivityMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[Any, dict[str, Any]], Awaitable[Any]],
        event: Any,
        data: dict[str, Any],
    ) -> Any:
        user = None
        if isinstance(event, (Message, CallbackQuery)):
            user = event.from_user

        if user is not None:
            try:
                await touch_user_activity(user.id)
            except Exception:
                logger.exception(
                    "Не удалось обновить активность telegram_id=%s",
                    user.id,
                )

        return await handler(event, data)


def create_bot() -> tuple[Bot, Dispatcher]:
    bot = Bot(token=TELEGRAM_BOT_TOKEN)
    dp = Dispatcher()

    dp.include_router(start.router)
    dp.message.middleware(ActivityMiddleware())
    dp.callback_query.middleware(ActivityMiddleware())

    return bot, dp
