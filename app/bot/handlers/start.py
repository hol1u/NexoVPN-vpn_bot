import logging
from typing import Any

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import CommandObject, CommandStart
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from app.config import ADMIN_IDS
from app.db import (
    check_db,
    count_referrals,
    count_users,
    get_active_family_plans,
    get_active_single_plans,
    get_plan_by_code,
    get_user_balance,
    get_user_referral_code,
    register_user,
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

CB_PLAN_PREFIX = "plan:"
CB_RENEW_PLAN_PREFIX = "renew:"

WELCOME_TEXT = (
    "👋 Добро пожаловать в NexoVPN\n\n"
    "🌐 Защищённое и стабильное VPN-соединение для ваших устройств.\n\n"
    "Выберите действие ниже 👇"
)


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


def format_balance(balance_kopecks: int) -> str:
    rubles = balance_kopecks / 100
    return f"{rubles:.2f}".rstrip("0").rstrip(".")


NBSP = "\u00a0"

FAMILY_TITLE_EMOJI = (
    "\U0001F468\u200D"
    "\U0001F469\u200D"
    "\U0001F467\u200D"
    "\U0001F466"
)


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
    elif (
        months % 10 in (2, 3, 4)
        and months % 100 not in (12, 13, 14)
    ):
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
        f"{plan_emoji(months)} "
        f"{months_label(months)} — "
        f"{format_price(price_kopecks)}"
    )

    if months > 1:
        per_month = (
            price_kopecks + months * 50
        ) // (months * 100)

        per_month_text = (
            f"{per_month:,}".replace(",", NBSP)
        )

        line += (
            f" · ~{per_month_text}{NBSP}₽/мес"
        )

    return line


def build_single_plans_text(
    plans: list[dict[str, Any]],
) -> str:
    lines = "\n".join(
        format_plan_line(plan)
        for plan in plans
    )

    return (
        "🌐 NexoVPN\n\n"
        f"{lines}"
    )


def build_family_plans_text(
    plans: list[dict[str, Any]],
) -> str:
    lines = "\n".join(
        format_plan_line(plan)
        for plan in plans
    )

    device_limit = (
        plans[0]["device_limit"]
        if plans
        else 5
    )

    return (
        f"{FAMILY_TITLE_EMOJI} NexoVPN Family\n\n"
        f"🏠 {devices_up_to(device_limit)}\n\n"
        f"{lines}"
    )


def build_renew_plans_text(
    plans: list[dict[str, Any]],
) -> str:
    lines = "\n".join(
        format_plan_line(plan)
        for plan in plans
    )

    return (
        "🔄 Продление подписки\n\n"
        f"{lines}"
    )


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
        f"{plan_emoji(months)} "
        f"Тариф: {months_label(months)}\n"
        f"⏳ Срок: {plan['duration_days']} дн.\n"
        f"📱 Устройства: {devices}\n"
        f"💰 Стоимость: "
        f"{format_price(plan['price_kopecks'])}\n\n"
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
                text=(
                    f"{plan_emoji(months)} "
                    f"{months_label(months)}"
                ),
                callback_data=(
                    f"{prefix}{plan['code']}"
                ),
            )
        )

    rows = [
        buttons[i:i + 2]
        for i in range(0, len(buttons), 2)
    ]

    rows.append(
        [
            build_back_button(
                back_callback
            )
        ]
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
                build_back_button(
                    back_callback
                )
            ]
        ]
    )


async def has_active_subscription(
    telegram_id: int,
) -> bool:
    # Таблица подписок будет подключена
    # на следующем этапе.
    return False


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
# START
# ============================================================

@router.message(CommandStart())
async def start_handler(
    message: Message,
    command: CommandObject,
) -> None:
    db_ok = await check_db()

    if not db_ok:
        await message.answer(
            "Сервис временно недоступен. "
            "Попробуйте немного позже."
        )
        return

    user = message.from_user

    if user is None:
        return

    referral_code = None

    if command.args:
        referral_code = command.args.strip()

    try:
        await register_user(
            telegram_id=user.id,
            username=user.username,
            first_name=user.first_name,
            referral_code=referral_code,
        )

    except Exception:
        logger.exception(
            "Не удалось зарегистрировать "
            "пользователя telegram_id=%s",
            user.id,
        )

        await message.answer(
            "Не удалось открыть профиль. "
            "Попробуйте ещё раз."
        )
        return

    await message.answer(
        WELCOME_TEXT,
        reply_markup=build_main_menu(
            is_admin=user.id in ADMIN_IDS
        ),
    )


# ============================================================
# INVITE / REFERRALS
# ============================================================

@router.callback_query(
    F.data == CB_INVITE
)
async def invite_handler(
    callback: CallbackQuery,
    bot: Bot,
) -> None:
    # Сразу закрываем Telegram loading.
    await callback.answer()

    user_id = callback.from_user.id

    try:
        referral_code = (
            await get_user_referral_code(
                user_id
            )
        )

        referrals_count = (
            await count_referrals(
                user_id
            )
        )

    except Exception:
        logger.exception(
            "Не удалось открыть "
            "реферальный раздел: telegram_id=%s",
            user_id,
        )

        await callback.answer(
            "Не удалось открыть раздел. "
            "Попробуйте ещё раз.",
            show_alert=True,
        )
        return

    if not referral_code:
        await callback.answer(
            "Не удалось создать "
            "реферальную ссылку.",
            show_alert=True,
        )
        return

    try:
        me = await bot.get_me()

        if not me.username:
            await callback.answer(
                "У бота не установлен username.",
                show_alert=True,
            )
            return

        referral_link = (
            f"https://t.me/{me.username}"
            f"?start={referral_code}"
        )

        text = (
            "👥 Пригласить друзей\n\n"
            "Приглашайте друзей в NexoVPN "
            "по вашей персональной ссылке.\n\n"
            f"🔗 Ваша ссылка:\n"
            f"{referral_link}\n\n"
            f"👤 Приглашено: "
            f"{referrals_count}"
        )

        await edit_menu(
            callback,
            text,
            build_plan_card_menu(CB_BACK),
        )

    except Exception:
        logger.exception(
            "Не удалось сформировать "
            "реферальную ссылку: telegram_id=%s",
            user_id,
        )

        if callback.message is not None:
            await callback.message.answer(
                "Не удалось открыть раздел "
                "приглашений. Попробуйте ещё раз."
            )


# ============================================================
# SUBSCRIPTION
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
        balance_kopecks = (
            await get_user_balance(
                user_id
            )
        )

    except Exception:
        logger.exception(
            "Не удалось получить баланс "
            "telegram_id=%s",
            user_id,
        )

        await callback.answer(
            "Не удалось получить баланс. "
            "Попробуйте позже.",
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
        "💳 Введите сумму пополнения "
        "в рублях (например: 100):",
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
        balance_kopecks = (
            await get_user_balance(
                user_id
            )
        )

    except Exception:
        logger.exception(
            "Не удалось получить баланс "
            "после отмены пополнения: "
            "telegram_id=%s",
            user_id,
        )

        await callback.answer(
            "Не удалось получить баланс. "
            "Попробуйте позже.",
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
            is_admin=(
                callback.from_user.id
                in ADMIN_IDS
            )
        ),
    )


# ============================================================
# VPN PLANS
# ============================================================

@router.callback_query(
    F.data == CB_CONNECT
)
async def connect_handler(
    callback: CallbackQuery,
) -> None:
    try:
        plans = (
            await get_active_single_plans()
        )

    except Exception:
        logger.exception(
            "Не удалось загрузить "
            "обычные тарифы"
        )

        await callback.answer(
            "Не удалось загрузить тарифы. "
            "Попробуйте позже.",
            show_alert=True,
        )
        return

    await callback.answer()

    if not plans:
        await edit_menu(
            callback,
            "Тарифы временно недоступны. "
            "Попробуйте позже.",
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


@router.callback_query(
    F.data == CB_FAMILY
)
async def family_handler(
    callback: CallbackQuery,
) -> None:
    try:
        plans = (
            await get_active_family_plans()
        )

    except Exception:
        logger.exception(
            "Не удалось загрузить "
            "семейные тарифы"
        )

        await callback.answer(
            "Не удалось загрузить тарифы. "
            "Попробуйте позже.",
            show_alert=True,
        )
        return

    await callback.answer()

    if not plans:
        await edit_menu(
            callback,
            "Семейные тарифы временно "
            "недоступны. Попробуйте позже.",
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
        plans = (
            await get_active_single_plans()
        )

    except Exception:
        logger.exception(
            "Не удалось загрузить "
            "тарифы для продления"
        )

        await callback.answer(
            "Не удалось загрузить тарифы. "
            "Попробуйте позже.",
            show_alert=True,
        )
        return

    await callback.answer()

    if not plans:
        await edit_menu(
            callback,
            "Тарифы временно недоступны. "
            "Попробуйте позже.",
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
        plan = await get_plan_by_code(
            code
        )

    except Exception:
        logger.exception(
            "Не удалось загрузить тариф "
            "code=%s",
            code,
        )

        await callback.answer(
            "Не удалось загрузить тариф. "
            "Попробуйте позже.",
            show_alert=True,
        )
        return

    if (
        plan is None
        or (
            renewal
            and plan["type"] != "single"
        )
    ):
        await callback.answer(
            "Этот тариф недоступен.",
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
    F.data.startswith(
        CB_RENEW_PLAN_PREFIX
    )
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
# ADMIN
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
            "Попытка открыть "
            "админ-статистику без прав: "
            "telegram_id=%s",
            callback.from_user.id,
        )

        await callback.answer()
        return

    try:
        total_users = await count_users()

    except Exception:
        logger.exception(
            "Не удалось получить "
            "статистику пользователей"
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
            "Всего зарегистрировано "
            f"пользователей: {total_users}"
        ),
    )


# ============================================================
# OTHER MENU BUTTONS
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
