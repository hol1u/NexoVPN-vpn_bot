import logging

from aiogram import Bot, F, Router
from aiogram.filters import CommandStart
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from app.config import ADMIN_IDS
from app.db import check_db, count_users, upsert_user

logger = logging.getLogger(__name__)

router = Router()

# callback_data кнопок главного меню
CB_CONNECT = "menu:connect"
CB_SUBSCRIPTION = "menu:subscription"
CB_RENEW = "menu:renew"
CB_BALANCE = "menu:balance"
CB_TOPUP = "menu:topup"
CB_FAMILY = "menu:family"
CB_INVITE = "menu:invite"
CB_HELP = "menu:help"

# callback_data секретной кнопки администратора
CB_ADMIN_STATS = "admin:stats"

WELCOME_TEXT = (
    "👋 Добро пожаловать в NexoVPN\n\n"
    "🌐 Защищённое и стабильное VPN-соединение для ваших устройств.\n\n"
    "Выберите действие ниже 👇"
)


def build_main_menu(is_admin: bool) -> InlineKeyboardMarkup:
    """Главное меню. Кнопка админ-статистики добавляется только для администратора."""
    rows = [
        [InlineKeyboardButton(text="✨ Подключить VPN", callback_data=CB_CONNECT)],
        [InlineKeyboardButton(text="📱 Моя подписка", callback_data=CB_SUBSCRIPTION)],
        [InlineKeyboardButton(text="💳 Продлить подписку", callback_data=CB_RENEW)],
        [
            InlineKeyboardButton(text="💰 Баланс", callback_data=CB_BALANCE),
            InlineKeyboardButton(text="💸 Пополнить баланс", callback_data=CB_TOPUP),
        ],
        [InlineKeyboardButton(text="👨‍👩‍👧 Семейная подписка", callback_data=CB_FAMILY)],
        [InlineKeyboardButton(text="👥 Пригласить друзей", callback_data=CB_INVITE)],
        [InlineKeyboardButton(text="ℹ️ Помощь", callback_data=CB_HELP)],
    ]

    if is_admin:
        rows.append(
            [InlineKeyboardButton(text="📊 Админ-статистика", callback_data=CB_ADMIN_STATS)]
        )

    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.message(CommandStart())
async def start_handler(message: Message) -> None:
    db_ok = await check_db()

    if not db_ok:
        await message.answer(
            "Сервис временно недоступен. Попробуйте немного позже."
        )
        return

    user = message.from_user
    if user is None:
        return

    # Регистрация при каждом /start: нового пользователя добавляем,
    # существующему обновляем username и first_name.
    try:
        await upsert_user(
            telegram_id=user.id,
            username=user.username,
            first_name=user.first_name,
        )
    except Exception:
        logger.exception("Не удалось сохранить пользователя telegram_id=%s", user.id)

    await message.answer(
        WELCOME_TEXT,
        reply_markup=build_main_menu(is_admin=user.id in ADMIN_IDS),
    )


@router.callback_query(F.data == CB_ADMIN_STATS)
async def admin_stats_handler(callback: CallbackQuery, bot: Bot) -> None:
    # Проверка прав выполняется на сервере: одной скрытой кнопки мало,
    # потому что callback_data может отправить кто угодно.
    if callback.from_user.id not in ADMIN_IDS:
        logger.warning(
            "Попытка открыть админ-статистику без прав: telegram_id=%s",
            callback.from_user.id,
        )
        await callback.answer()
        return

    try:
        total_users = await count_users()
    except Exception:
        logger.exception("Не удалось получить статистику пользователей")
        await callback.answer(
            "Не удалось получить статистику. Попробуйте позже.",
            show_alert=True,
        )
        return

    await callback.answer()
    await bot.send_message(
        chat_id=callback.from_user.id,
        text=(
            "📊 Админ-статистика\n\n"
            f"Всего зарегистрировано пользователей: {total_users}"
        ),
    )


@router.callback_query(F.data.startswith("menu:"))
async def menu_placeholder_handler(callback: CallbackQuery) -> None:
    # Заглушка: логика кнопок меню появится на следующих этапах.
    # Ответ на callback нужен, чтобы у кнопки не «крутился» индикатор загрузки.
    await callback.answer("Этот раздел скоро появится.")
