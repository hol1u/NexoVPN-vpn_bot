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
from app.db import (
    check_db,
    count_users,
    get_user_balance,
    upsert_user,
)

logger = logging.getLogger(__name__)

router = Router()

CB_CONNECT = "menu:connect"
CB_SUBSCRIPTION = "menu:subscription"
CB_RENEW = "menu:renew"
CB_BALANCE = "menu:balance"
CB_TOPUP = "menu:topup"
CB_FAMILY = "menu:family"
CB_INVITE = "menu:invite"
CB_HELP = "menu:help"
CB_BACK = "menu:back"
CB_CANCEL = "balance:cancel"
CB_ADMIN_STATS = "admin:stats"

WELCOME_TEXT = (
    "👋 Добро пожаловать в NexoVPN\n\n"
    "🌐 Защищённое и стабильное VPN-соединение для ваших устройств.\n\n"
    "Выберите действие ниже 👇"
)


def build_main_menu(is_admin: bool) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text="✨ Подключить VPN",
                callback_data=CB_CONNECT,
            )
        ],
        [
            InlineKeyboardButton(
                text="📱 Моя подписка",
                callback_data=CB_SUBSCRIPTION,
            ),
            InlineKeyboardButton(
                text="💳 Продлить подписку",
                callback_data=CB_RENEW,
            ),
        ],
        [
            InlineKeyboardButton(
                text="💰 Баланс",
                callback_data=CB_BALANCE,
            ),
            InlineKeyboardButton(
                text="💸 Пополнить баланс",
                callback_data=CB_TOPUP,
            ),
        ],
        [
            InlineKeyboardButton(
                text="👨‍👩‍👧 Семейная подписка",
                callback_data=CB_FAMILY,
            )
        ],
        [
            InlineKeyboardButton(
                text="👥 Пригласить друзей",
                callback_data=CB_INVITE,
            )
        ],
        [
            InlineKeyboardButton(
                text="ℹ️ Помощь",
                callback_data=CB_HELP,
            )
        ],
    ]

    if is_admin:
        rows.append(
            [
                InlineKeyboardButton(
                    text="📊 Админ-статистика",
                    callback_data=CB_ADMIN_STATS,
                )
            ]
        )

    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_subscription_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🛒 Купить VPN",
                    callback_data=CB_CONNECT,
                )
            ],
            [
                InlineKeyboardButton(
                    text="◀️ Назад",
                    callback_data=CB_BACK,
                    style="danger",
                )
            ],
        ]
    )


def build_balance_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="💳 Пополнить баланс",
                    callback_data=CB_TOPUP,
                )
            ],
            [
                InlineKeyboardButton(
                    text="◀️ Назад",
                    callback_data=CB_BACK,
                    style="danger",
                )
            ],
        ]
    )


def build_topup_cancel_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Отмена",
                    callback_data=CB_CANCEL,
                    style="danger",
                )
            ]
        ]
    )


def format_balance(balance_kopecks: int) -> str:
    rubles = balance_kopecks / 100
    return f"{rubles:.2f}".rstrip("0").rstrip(".")


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

    try:
        await upsert_user(
            telegram_id=user.id,
            username=user.username,
            first_name=user.first_name,
        )
    except Exception:
        logger.exception(
            "Не удалось сохранить пользователя telegram_id=%s",
            user.id,
        )

    await message.answer(
        WELCOME_TEXT,
        reply_markup=build_main_menu(
            is_admin=user.id in ADMIN_IDS
        ),
    )


@router.callback_query(F.data == CB_SUBSCRIPTION)
async def subscription_handler(callback: CallbackQuery) -> None:
    await callback.answer()

    if callback.message is None:
        return

    await callback.message.edit_text(
        "📱 Моя подписка\n\n"
        "Статус: Нет подписки",
        reply_markup=build_subscription_menu(),
    )


@router.callback_query(F.data == CB_BALANCE)
async def balance_handler(callback: CallbackQuery) -> None:
    user_id = callback.from_user.id

    try:
        balance_kopecks = await get_user_balance(user_id)
    except Exception:
        logger.exception(
            "Не удалось получить баланс пользователя telegram_id=%s",
            user_id,
        )
        await callback.answer(
            "Не удалось получить баланс. Попробуйте позже.",
            show_alert=True,
        )
        return

    await callback.answer()

    if callback.message is None:
        return

    balance = format_balance(balance_kopecks)

    await callback.message.edit_text(
        f"💰 Ваш баланс: {balance} ₽",
        reply_markup=build_balance_menu(),
    )


@router.callback_query(F.data == CB_TOPUP)
async def topup_handler(callback: CallbackQuery) -> None:
    await callback.answer()

    if callback.message is None:
        return

    await callback.message.edit_text(
        "💳 Введите сумму пополнения в рублях (например: 100):",
        reply_markup=build_topup_cancel_menu(),
    )


@router.callback_query(F.data == CB_CANCEL)
async def cancel_topup_handler(callback: CallbackQuery) -> None:
    user_id = callback.from_user.id

    try:
        balance_kopecks = await get_user_balance(user_id)
    except Exception:
        logger.exception(
            "Не удалось получить баланс после отмены пополнения: telegram_id=%s",
            user_id,
        )
        await callback.answer(
            "Не удалось получить баланс. Попробуйте позже.",
            show_alert=True,
        )
        return

    await callback.answer()

    if callback.message is None:
        return

    balance = format_balance(balance_kopecks)

    await callback.message.edit_text(
        f"💰 Ваш баланс: {balance} ₽",
        reply_markup=build_balance_menu(),
    )


@router.callback_query(F.data == CB_BACK)
async def back_handler(callback: CallbackQuery) -> None:
    await callback.answer()

    if callback.message is None:
        return

    await callback.message.edit_text(
        WELCOME_TEXT,
        reply_markup=build_main_menu(
            is_admin=callback.from_user.id in ADMIN_IDS
        ),
    )


@router.callback_query(F.data == CB_ADMIN_STATS)
async def admin_stats_handler(
    callback: CallbackQuery,
    bot: Bot,
) -> None:
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
        logger.exception(
            "Не удалось получить статистику пользователей"
        )
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
async def menu_placeholder_handler(
    callback: CallbackQuery,
) -> None:
    await callback.answer(
        "Этот раздел скоро появится."
    )
