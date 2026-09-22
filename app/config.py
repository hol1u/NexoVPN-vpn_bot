```python
import time

from aiogram.enums import ChatMemberStatus
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

from app.config import REQUIRED_CHANNEL_ID, REQUIRED_CHANNEL_URL

_last_subscription_check: dict[int, float] = {}
SUBSCRIPTION_CHECK_COOLDOWN = 3.0  # секунды


def _subscription_gate_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📢 Подписаться на канал", url=REQUIRED_CHANNEL_URL)],
            [InlineKeyboardButton(text="🟢 Я подписался", callback_data="check_subscription")],
        ]
    )


@router.callback_query(F.data == "check_subscription")
async def check_subscription_handler(callback: CallbackQuery) -> None:
    user_id = callback.from_user.id
    now = time.monotonic()

    last_attempt = _last_subscription_check.get(user_id)
    if last_attempt is not None and now - last_attempt < SUBSCRIPTION_CHECK_COOLDOWN:
        await callback.answer("Слишком много попыток, попробуйте позже.", show_alert=True)
        return

    _last_subscription_check[user_id] = now

    try:
        member = await callback.bot.get_chat_member(REQUIRED_CHANNEL_ID, user_id)
    except Exception:
        await callback.answer("Не удалось проверить подписку. Попробуйте позже.", show_alert=True)
        return

    if member.status in (
        ChatMemberStatus.MEMBER,
        ChatMemberStatus.ADMINISTRATOR,
        ChatMemberStatus.CREATOR,
    ):
        _last_subscription_check.pop(user_id, None)
        await callback.answer("Подписка подтверждена ✅")
        await show_main_menu(callback)  # существующая функция показа главного меню
        return

    await callback.answer("Вы ещё не подписаны на канал.", show_alert=True)
    await show_subscription_gate_after_failed_check(callback)


async def show_subscription_gate_after_failed_check(callback: CallbackQuery) -> None:
    text = (
        "🔒 Чтобы пользоваться ботом, подпишитесь на канал:\n"
        f"{REQUIRED_CHANNEL_URL}\n\n"
        "После подписки нажмите «🟢 Я подписался»."
    )

    try:
        await callback.message.edit_text(
            text,
            reply_markup=_subscription_gate_keyboard(),
        )
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e):
            raise
```
