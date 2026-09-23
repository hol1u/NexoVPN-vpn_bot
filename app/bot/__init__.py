import logging

from aiogram import BaseMiddleware, Bot, Dispatcher
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, Message
from typing import Any, Awaitable, Callable

from app.bot.handlers import start
from app.bot.handlers import admin
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

        try:
            return await handler(event, data)

        except TelegramBadRequest as exc:
            if isinstance(event, CallbackQuery) and (
                "query is too old" in str(exc)
                or "query ID is invalid" in str(exc)
            ):
                logger.info("Пропущен просроченный callback query")
                return None
            raise

        finally:
            # Activity tracking must not delay callback acknowledgement.
            if user is not None:
                try:
                    await touch_user_activity(user.id)
                except Exception:
                    logger.exception(
                        "Не удалось обновить активность telegram_id=%s",
                        user.id,
                    )


def create_bot() -> tuple[Bot, Dispatcher]:
    bot = Bot(token=TELEGRAM_BOT_TOKEN)
    dp = Dispatcher()

    dp.include_router(admin.router)
    dp.include_router(start.router)
    dp.message.middleware(ActivityMiddleware())
    dp.callback_query.middleware(ActivityMiddleware())

    return bot, dp
