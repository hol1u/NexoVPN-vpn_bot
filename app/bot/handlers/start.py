import logging
from typing import Any

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import CommandStart
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from app.config import (
    ADMIN_IDS,
    REQUIRED_CHANNEL_ID,
    REQUIRED_CHANNEL_URL,
)
from app.db import (
    check_db,
    count_users,
    get_active_family_plans,
    get_active_single_plans,
    get_plan_by_code,
    get_user_balance,
    upsert_user,
)

logger = logging.getLogger(__name__)

router = Router()


# ============================================================
# CALLBACK DATA
# ============================================================

CB_CHECK_SUBSCRIPTION = "subscription:check"

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

CB_PLAN_PREFIX = "plan:"
CB_RENEW_PLAN_PREFIX = "renew:"


# ============================================================
# TEXTS
# ============================================================

WELCOME_TEXT = (
    "👋 Добро пожаловать в NexoVPN\n\n"
    "🌐 Защищённое и стабильное VPN-соединение для ваших устройств.\n\n"
    "Выберите действие ниже 👇"
)


SUBSCRIPTION_REQUIRED_TEXT = (
    "👋 Добро пожаловать в Nexo VPN\n\n"
    "🌐 Для начала работы подпишитесь на наш канал.\n"
    "Это займёт всего пару секунд!\n\n"
    "После подписки нажмите «🟢 Я подписался»."
)


SUBSCRIPTION_NOT_CONFIRMED_TEXT = (
    "❌ Вы ещё не подписались на канал.\n\n"
    "Подпишитесь на канал и нажмите\n"
    "«🟢 Я подписался» ещё раз."
)


# ============================================================
# SUBSCRIPTION CHECK
# ============================================================

def build_subscription_gate_menu() -> InlineKeyboardMarkup:
    """
    Кнопки обязательной подписки.

    Первая кнопка открывает канал.
    Вторая запускает реальную проверку подписки через Telegram API.
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⚡ Подписаться на канал",
                    url=REQUIRED_CHANNEL_URL,
                )
            ],
            [
                InlineKeyboardButton(
                    text="🟢 Я подписался",
                    callback_data=CB_CHECK_SUBSCRIPTION,
                    style="success",
                )
            ],
        ]
    )


async def is_channel_subscribed(
    bot: Bot,
    user_id: int,
) -> bool:
    """
    Проверяет, состоит ли пользователь в обязательном канале.

    Считаем подпиской:
    - creator
    - administrator
    - member
    - restricted + is_member=True
    """
    try:
        member = await bot.get_chat_member(
            chat_id=REQUIRED_CHANNEL_ID,
            user_id=user_id,
        )
    except Exception:
        logger.exception(
            "Не удалось проверить подписку: telegram_id=%s",
            user_id,
        )
        return False

    if member.status in {"creator", "administrator", "member"}:
        return True

    if member.status == "restricted":
        return bool(getattr(member, "is_member", False))

    return False


async def show_subscription_gate(
    message: Message,
) -> None:
    """Показывает пользователю экран обязательной подписки."""
    await message.answer(
        SUBSCRIPTION_REQUIRED_TEXT,
        reply_markup=build_subscription_gate_menu(),
    )


async def show_subscription_gate_after_failed_check(
    callback: CallbackQuery,
) -> None:
    """Повторно показывает экран подписки после неудачной проверки."""
    if callback.message is None:
        return

    try:
        await callback.message.edit_text(
            SUBSCRIPTION_NOT_CONFIRMED_TEXT,
            reply_markup=build_subscription_gate_menu(),
        )
    except TelegramBadRequest as exc:
        if "message is not modified" not in str(exc):
            raise


# ============================================================
# MAIN MENU
# ============================================================

def build_main_menu(is_admin: bool) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text="🍁 Подключить VPN",
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


# ============================================================
# SUBSCRIPTION / BALANCE MENUS
# ============================================================

def build_subscription_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🍁 Купить VPN",
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


# ============================================================
# PRICE / PLAN HELPERS
# ============================================================

def format_balance(balance_kopecks: int) -> str:
    rubles = balance_kopecks / 100
    return f"{rubles:.2f}".rstrip("0").rstrip(".")


NBSP = "\u00a0"

FAMILY_TITLE_EMOJI = "\U0001F468\u200D\U0001F469\u200D\U0001F467\u200D\U0001F466"


def format_price(price_kopecks: int) -> str:
    rubles, kopecks = divmod(price_kopecks, 100)
    text = f"{rubles:,}".replace(",", NBSP)

    if kopecks:
        text += f",{kopecks:02d}"

    return f"{text}{NBSP}₽"


def plan_months(plan: dict[str, Any]) -> int:
    return max(1, round(plan["duration_days"] / 30))


def months_label(months: int) -> str:
    if months % 10 == 1 and months % 100 != 11:
        word = "месяц"
    elif months % 10 in (2, 3, 4) and months % 100 not in (12, 13, 14):
        word = "месяца"
    else:
        word = "месяцев"

    return f"{months} {word}"


def plan_emoji(months: int) -> str:
    if months <= 1:
        return "⚡"
    if months <= 3:
        return "🔥"
    if months <= 6:
        return "🚀"
    return "👑"


def devices_up_to(device_limit: int) -> str:
    if device_limit % 10 == 1 and device_limit % 100 != 11:
        word = "устройства"
    else:
        word = "устройств"

    return f"До {device_limit} {word}"


def format_plan_line(plan: dict[str, Any]) -> str:
    months = plan_months(plan)
    price_kopecks = plan["price_kopecks"]

    line = (
        f"{plan_emoji(months)} {months_label(months)} — "
        f"{format_price(price_kopecks)}"
    )

    if months > 1:
        per_month = (price_kopecks + months * 50) // (months * 100)
        per_month_text = f"{per_month:,}".replace(",", NBSP)
        line += f" · ~{per_month_text}{NBSP}₽/мес"

    return line


def build_single_plans_text(
    plans: list[dict[str, Any]],
) -> str:
    lines = "\n".join(
        format_plan_line(plan)
        for plan in plans
    )

    return f"🌐 NexoVPN\n\n{lines}"


def build_family_plans_text(
    plans: list[dict[str, Any]],
) -> str:
    lines = "\n".join(
        format_plan_line(plan)
        for plan in plans
    )

    return (
        f"{FAMILY_TITLE_EMOJI} NexoVPN Family\n\n"
        f"🏠 {devices_up_to(plans[0]['device_limit'])}\n\n"
        f"{lines}"
    )


def build_renew_plans_text(
    plans: list[dict[str, Any]],
) -> str:
    lines = "\n".join(
        format_plan_line(plan)
        for plan in plans
    )

    return f"🔄 Продление подписки\n\n{lines}"


def format_plan_card(
    plan: dict[str, Any],
    renewal: bool,
) -> str:
    months = plan_months(plan)
    is_family = plan["type"] == "family"

    if renewal:
        title = "🔄 Продление подписки"
    elif is_family:
        title = f"{FAMILY_TITLE_EMOJI} NexoVPN Family"
    else:
        title = "🌐 NexoVPN"

    if is_family:
        devices = f"до {plan['device_limit']}"
    else:
        devices = str(plan["device_limit"])

    return (
        f"{title}\n\n"
        f"{plan_emoji(months)} Тариф: {months_label(months)}\n"
        f"⏳ Срок: {plan['duration_days']} дн.\n"
        f"📱 Устройства: {devices}\n"
        f"💰 Стоимость: {format_price(plan['price_kopecks'])}\n\n"
        "🔜 Оплата скоро появится."
    )


def build_back_button(
    callback_data: str,
) -> InlineKeyboardButton:
    return InlineKeyboardButton(
        text="◀️ Назад",
        callback_data=callback_data,
        style="danger",
    )


def build_plans_menu(
    plans: list[dict[str, Any]],
    prefix: str,
    back_callback: str,
) -> InlineKeyboardMarkup:
    buttons = []

    for plan in plans:
        months = plan_months(plan)

        buttons.append(
            InlineKeyboardButton(
                text=f"{plan_emoji(months)} {months_label(months)}",
                callback_data=f"{prefix}{plan['code']}",
            )
        )

    rows = [
        buttons[i:i + 2]
        for i in range(0, len(buttons), 2)
    ]

    rows.append(
        [build_back_button(back_callback)]
    )

    return InlineKeyboardMarkup(
        inline_keyboard=rows
    )


def build_no_subscription_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🍁 Купить VPN",
                    callback_data=CB_CONNECT,
                )
            ],
            [
                build_back_button(CB_BACK)
            ],
        ]
    )


def build_plan_card_menu(
    back_callback: str,
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                build_back_button(back_callback)
            ]
        ]
    )


# ============================================================
# SUBSCRIPTION PLACEHOLDER
# ============================================================

async def has_active_subscription(
    telegram_id: int,
) -> bool:
    """
    Таблица подписок пока не подключена.
    На этапе оплаты/VPN здесь появится реальная проверка.
    """
    return False


# ============================================================
# COMMON EDIT HELPER
# ============================================================

async def edit_menu(
    callback: CallbackQuery,
    text: str,
    reply_markup: InlineKeyboardMarkup,
) -> None:
    message = callback.message

    if not isinstance(message, Message):
        return

    try:
        await message.edit_text(
            text,
            reply_markup=reply_markup,
        )
    except TelegramBadRequest as exc:
        if "message is not modified" not in str(exc):
            raise


# ============================================================
# /START
# ============================================================

@router.message(CommandStart())
async def start_handler(
    message: Message,
    bot: Bot,
) -> None:
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

    # Обязательная проверка подписки.
    subscribed = await is_channel_subscribed(
        bot=bot,
        user_id=user.id,
    )

    if not subscribed:
        await show_subscription_gate(message)
        return

    # Подписка подтверждена — показываем обычное меню.
    await message.answer(
        WELCOME_TEXT,
        reply_markup=build_main_menu(
            is_admin=user.id in ADMIN_IDS
        ),
    )


# ============================================================
# SUBSCRIPTION CHECK BUTTON
# ============================================================

@router.callback_query(
    F.data == CB_CHECK_SUBSCRIPTION
)
async def check_subscription_handler(
    callback: CallbackQuery,
    bot: Bot,
) -> None:
    subscribed = await is_channel_subscribed(
        bot=bot,
        user_id=callback.from_user.id,
    )

    if not subscribed:
        await callback.answer(
            "Подписка пока не найдена.",
            show_alert=True,
        )

        await show_subscription_gate_after_failed_check(
            callback
        )
        return

    await callback.answer(
        "✅ Подписка подтверждена!"
    )

    if callback.message is None:
        return

    try:
        await callback.message.edit_text(
            WELCOME_TEXT,
            reply_markup=build_main_menu(
                is_admin=callback.from_user.id in ADMIN_IDS
            ),
        )
    except TelegramBadRequest as exc:
        if "message is not modified" not in str(exc):
            raise


# ============================================================
# MY SUBSCRIPTION
# ============================================================

@router.callback_query(
    F.data == CB_SUBSCRIPTION
)
async def subscription_handler(
    callback: CallbackQuery,
) -> None:
    await callback.answer()

    if callback.message is None:
        return

    await callback.message.edit_text(
        "📱 Моя подписка\n\n"
        "Статус: Нет подписки",
        reply_markup=build_subscription_menu(),
    )


# ============================================================
# BALANCE
# ============================================================

@router.callback_query(
    F.data == CB_BALANCE
)
async def balance_handler(
    callback: CallbackQuery,
) -> None:
    user_id = callback.from_user.id

    try:
        balance_kopecks = await get_user_balance(
            user_id
        )
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

    balance = format_balance(
        balance_kopecks
    )

    await callback.message.edit_text(
        f"💰 Ваш баланс: {balance} ₽",
        reply_markup=build_balance_menu(),
    )


# ============================================================
# TOP UP
# ============================================================

@router.callback_query(
    F.data == CB_TOPUP
)
async def topup_handler(
    callback: CallbackQuery,
) -> None:
    await callback.answer()

    if callback.message is None:
        return

    await callback.message.edit_text(
        "💳 Введите сумму пополнения в рублях "
        "(например: 100):",
        reply_markup=build_topup_cancel_menu(),
    )


@router.callback_query(
    F.data == CB_CANCEL
)
async def cancel_topup_handler(
    callback: CallbackQuery,
) -> None:
    user_id = callback.from_user.id

    try:
        balance_kopecks = await get_user_balance(
            user_id
        )
    except Exception:
        logger.exception(
            "Не удалось получить баланс после отмены "
            "пополнения: telegram_id=%s",
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

    balance = format_balance(
        balance_kopecks
    )

    await callback.message.edit_text(
        f"💰 Ваш баланс: {balance} ₽",
        reply_markup=build_balance_menu(),
    )


# ============================================================
# BACK
# ============================================================

@router.callback_query(
    F.data == CB_BACK
)
async def back_handler(
    callback: CallbackQuery,
) -> None:
    await callback.answer()

    if callback.message is None:
        return

    await callback.message.edit_text(
        WELCOME_TEXT,
        reply_markup=build_main_menu(
            is_admin=callback.from_user.id in ADMIN_IDS
        ),
    )


# ============================================================
# CONNECT / NORMAL PLANS
# ============================================================

@router.callback_query(
    F.data == CB_CONNECT
)
async def connect_handler(
    callback: CallbackQuery,
) -> None:
    try:
        plans = await get_active_single_plans()
    except Exception:
        logger.exception(
            "Не удалось загрузить обычные тарифы"
        )

        await callback.answer(
            "Не удалось загрузить тарифы. Попробуйте позже.",
            show_alert=True,
        )
        return

    await callback.answer()

    if not plans:
        await edit_menu(
            callback,
            "Тарифы временно недоступны. Попробуйте позже.",
            build_plan_card_menu(CB_BACK),
        )
        return

    await edit_menu(
        callback,
        build_single_plans_text(plans),
        build_plans_menu(
            plans,
            CB_PLAN_PREFIX,
            CB_BACK,
        ),
    )


# ============================================================
# FAMILY PLANS
# ============================================================

@router.callback_query(
    F.data == CB_FAMILY
)
async def family_handler(
    callback: CallbackQuery,
) -> None:
    try:
        plans = await get_active_family_plans()
    except Exception:
        logger.exception(
            "Не удалось загрузить семейные тарифы"
        )

        await callback.answer(
            "Не удалось загрузить тарифы. Попробуйте позже.",
            show_alert=True,
        )
        return

    await callback.answer()

    if not plans:
        await edit_menu(
            callback,
            "Семейные тарифы временно недоступны. "
            "Попробуйте позже.",
            build_plan_card_menu(CB_BACK),
        )
        return

    await edit_menu(
        callback,
        build_family_plans_text(plans),
        build_plans_menu(
            plans,
            CB_PLAN_PREFIX,
            CB_BACK,
        ),
    )


# ============================================================
# RENEW
# ============================================================

@router.callback_query(
    F.data == CB_RENEW
)
async def renew_handler(
    callback: CallbackQuery,
) -> None:
    if not await has_active_subscription(
        callback.from_user.id
    ):
        await callback.answer()

        await edit_menu(
            callback,
            "У вас нет активной подписки",
            build_no_subscription_menu(),
        )
        return

    try:
        plans = await get_active_single_plans()
    except Exception:
        logger.exception(
            "Не удалось загрузить тарифы для продления"
        )

        await callback.answer(
            "Не удалось загрузить тарифы. Попробуйте позже.",
            show_alert=True,
        )
        return

    await callback.answer()

    if not plans:
        await edit_menu(
            callback,
            "Тарифы временно недоступны. Попробуйте позже.",
            build_plan_card_menu(CB_BACK),
        )
        return

    await edit_menu(
        callback,
        build_renew_plans_text(plans),
        build_plans_menu(
            plans,
            CB_RENEW_PLAN_PREFIX,
            CB_BACK,
        ),
    )


# ============================================================
# PLAN CARD
# ============================================================

async def show_plan_card(
    callback: CallbackQuery,
    code: str,
    renewal: bool,
) -> None:
    try:
        plan = await get_plan_by_code(code)
    except Exception:
        logger.exception(
            "Не удалось загрузить тариф code=%s",
            code,
        )

        await callback.answer(
            "Не удалось загрузить тариф. Попробуйте позже.",
            show_alert=True,
        )
        return

    if plan is None:
        await callback.answer(
            "Этот тариф недоступен.",
            show_alert=True,
        )
        return

    if renewal and plan["type"] != "single":
        await callback.answer(
            "Этот тариф недоступен для продления.",
            show_alert=True,
        )
        return

    await callback.answer()

    if renewal:
        back_callback = CB_RENEW
    elif plan["type"] == "family":
        back_callback = CB_FAMILY
    else:
        back_callback = CB_CONNECT

    await edit_menu(
        callback,
        format_plan_card(
            plan,
            renewal=renewal,
        ),
        build_plan_card_menu(
            back_callback
        ),
    )


# ============================================================
# PLAN SELECTED
# ============================================================

@router.callback_query(
    F.data.startswith(CB_PLAN_PREFIX)
)
async def plan_selected_handler(
    callback: CallbackQuery,
) -> None:
    code = (
        callback.data or ""
    ).removeprefix(
        CB_PLAN_PREFIX
    )

    await show_plan_card(
        callback,
        code=code,
        renewal=False,
    )


@router.callback_query(
    F.data.startswith(CB_RENEW_PLAN_PREFIX)
)
async def renew_plan_selected_handler(
    callback: CallbackQuery,
) -> None:
    code = (
        callback.data or ""
    ).removeprefix(
        CB_RENEW_PLAN_PREFIX
    )

    await show_plan_card(
        callback,
        code=code,
        renewal=True,
    )


# ============================================================
# ADMIN STATS
# ============================================================

@router.callback_query(
    F.data == CB_ADMIN_STATS
)
async def admin_stats_handler(
    callback: CallbackQuery,
    bot: Bot,
) -> None:
    if callback.from_user.id not in ADMIN_IDS:
        logger.warning(
            "Попытка открыть админ-статистику "
            "без прав: telegram_id=%s",
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
            "Не удалось получить статистику. "
            "Попробуйте позже.",
            show_alert=True,
        )
        return

    await callback.answer()

    await bot.send_message(
        chat_id=callback.from_user.id,
        text=(
            "📊 Админ-статистика\n\n"
            f"Всего зарегистрировано пользователей: "
            f"{total_users}"
        ),
    )


# ============================================================
# PLACEHOLDER MENU HANDLER
# ============================================================

@router.callback_query(
    F.data.startswith("menu:")
)
async def menu_placeholder_handler(
    callback: CallbackQuery,
) -> None:
    await callback.answer(
        "Этот раздел скоро появится."
    )
