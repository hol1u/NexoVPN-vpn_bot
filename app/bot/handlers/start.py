import logging
import time
from typing import Any
from urllib.parse import quote

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import CommandStart
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from aiogram.filters.command import CommandObject

from app.config import (
    ADMIN_IDS,
    REQUIRED_CHANNEL_ID,
    REQUIRED_CHANNEL_URL,
    SUBSCRIPTION_CHECK_COOLDOWN,
)
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

CB_SHARE_REFERRAL = "referral:share"
CB_SUPPORT = "help:support"

CB_PLAN_PREFIX = "plan:"
CB_RENEW_PLAN_PREFIX = "renew:"


# ============================================================
# ANTI-SPAM STATE
# ============================================================

# telegram_id -> время последней проверки подписки (time.monotonic())
_last_subscription_check: dict[int, float] = {}

# кэш username бота (запрашивается через get_me() один раз за жизнь процесса)
_cached_bot_username: str | None = None


# ============================================================
# TEXTS
# ============================================================

WELCOME_TEXT = (
    "👋 Добро пожаловать в NexoVPN\n\n"
    "🌐 Защищённое и стабильное VPN-соединение "
    "для ваших устройств.\n\n"
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


HELP_TEXT = (
    "ℹ️ Помощь\n\n"
    "Здесь ты найдёшь ответы на основные вопросы "
    "по NexoVPN.\n\n"
    "🔹 Как подключить VPN?\n"
    "Выбери «🍁 Подключить VPN», затем подходящий тариф.\n\n"
    "🔹 Сколько устройств можно подключить?\n"
    "Обычная подписка — 1 устройство.\n"
    "Семейная подписка — до 5 устройств.\n\n"
    "🔹 Как пополнить баланс?\n"
    "Открой «💰 Баланс» → «💸 Пополнить баланс» "
    "и следуй инструкции.\n\n"
    "🔹 Не получается подключиться?\n"
    "Проверь настройки VPN и убедись, что подписка активна.\n\n"
    "Если самостоятельно решить проблему не получается — "
    "обратись в службу поддержки."
)


# ============================================================
# SUBSCRIPTION GATE
# ============================================================

def build_subscription_gate_menu() -> InlineKeyboardMarkup:
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

    if member.status in {
        "creator",
        "administrator",
        "member",
    }:
        return True

    if member.status == "restricted":
        return bool(
            getattr(
                member,
                "is_member",
                False,
            )
        )

    return False


async def show_subscription_gate(
    message: Message,
) -> None:
    await message.answer(
        SUBSCRIPTION_REQUIRED_TEXT,
        reply_markup=build_subscription_gate_menu(),
    )


async def show_subscription_gate_after_failed_check(
    callback: CallbackQuery,
) -> None:
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

def build_main_menu(
    is_admin: bool,
) -> InlineKeyboardMarkup:

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

    return InlineKeyboardMarkup(
        inline_keyboard=rows
    )


# ============================================================
# REFERRAL MENU
# ============================================================

def build_referral_menu(
    referral_url: str,
) -> InlineKeyboardMarkup:

    share_text = (
        "🍁 Подключайся к NexoVPN — стабильный "
        "и защищённый интернет без лишних ограничений."
    )

    share_url = (
        "https://t.me/share/url"
        f"?url={quote(referral_url, safe='')}"
        f"&text={quote(share_text, safe='')}"
    )

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🔗 Поделиться ссылкой",
                    url=share_url,
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


# ============================================================
# HELP MENU
# ============================================================

def build_help_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📞 Написать в поддержку",
                    url="https://t.me/nexovpn_proxy_help",
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


# ============================================================
# OTHER MENUS
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

def format_balance(
    balance_kopecks: int,
) -> str:
    rubles = balance_kopecks / 100
    return (
        f"{rubles:.2f}"
        .rstrip("0")
        .rstrip(".")
    )


NBSP = "\u00a0"

FAMILY_TITLE_EMOJI = (
    "\U0001F468\u200D"
    "\U0001F469\u200D"
    "\U0001F467\u200D"
    "\U0001F466"
)


def format_price(
    price_kopecks: int,
) -> str:

    rubles, kopecks = divmod(
        price_kopecks,
        100,
    )

    text = f"{rubles:,}".replace(
        ",",
        NBSP,
    )

    if kopecks:
        text += f",{kopecks:02d}"

    return f"{text}{NBSP}₽"


def plan_months(
    plan: dict[str, Any],
) -> int:
    return max(
        1,
        round(
            plan["duration_days"] / 30
        ),
    )


def months_label(
    months: int,
) -> str:

    if (
        months % 10 == 1
        and months % 100 != 11
    ):
        word = "месяц"

    elif (
        months % 10 in (2, 3, 4)
        and months % 100 not in (12, 13, 14)
    ):
        word = "месяца"

    else:
        word = "месяцев"

    return f"{months} {word}"


def plan_emoji(
    months: int,
) -> str:

    if months <= 1:
        return "⚡"

    if months <= 3:
        return "🔥"

    if months <= 6:
        return "🚀"

    return "👑"


def devices_up_to(
    device_limit: int,
) -> str:

    if (
        device_limit % 10 == 1
        and device_limit % 100 != 11
    ):
        word = "устройства"

    else:
        word = "устройств"

    return f"До {device_limit} {word}"


def format_plan_line(
    plan: dict[str, Any],
) -> str:

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
            f"{per_month:,}"
            .replace(",", NBSP)
        )

        line += (
            f" · ~{per_month_text}"
            f"{NBSP}₽/мес"
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

    return (
        f"{FAMILY_TITLE_EMOJI} "
        "NexoVPN Family\n\n"
        f"🏠 {devices_up_to(plans[0]['device_limit'])}"
        f"\n\n{lines}"
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
    is_family = (
        plan["type"] == "family"
    )

    if renewal:
        title = "🔄 Продление подписки"

    elif is_family:
        title = (
            f"{FAMILY_TITLE_EMOJI} "
            "NexoVPN Family"
        )

    else:
        title = "🌐 NexoVPN"

    if is_family:
        devices = f"до {plan['device_limit']}"
    else:
        devices = str(
            plan["device_limit"]
        )

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
        for i in range(
            0,
            len(buttons),
            2,
        )
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


# ============================================================
# SUBSCRIPTION PLACEHOLDER
# ============================================================

async def has_active_subscription(
    telegram_id: int,
) -> bool:
    return False


# ============================================================
# COMMON EDIT
# ============================================================

async def edit_menu(
    callback: CallbackQuery,
    text: str,
    reply_markup: InlineKeyboardMarkup,
) -> None:

    message = callback.message

    if not isinstance(
        message,
        Message,
    ):
        return

    try:
        await message.edit_text(
            text,
            reply_markup=reply_markup,
        )

    except TelegramBadRequest as exc:
        if (
            "message is not modified"
            not in str(exc)
        ):
            raise


# ============================================================
# BOT USERNAME (кэшируется через get_me())
# ============================================================

async def get_bot_username(bot: Bot) -> str | None:
    """
    Возвращает username бота через поддерживаемый метод API — get_me().
    У объекта Bot в aiogram 3 нет готового атрибута .username,
    поэтому результат кэшируется в памяти процесса после первого запроса.
    """
    global _cached_bot_username

    if _cached_bot_username:
        return _cached_bot_username

    try:
        me = await bot.get_me()
    except Exception:
        logger.exception(
            "Не удалось получить данные бота через get_me()"
        )
        return None

    _cached_bot_username = me.username
    return _cached_bot_username


# ============================================================
# /START
# ============================================================

@router.message(CommandStart())
async def start_handler(
    message: Message,
    bot: Bot,
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
        args = command.args.strip()

        if args.startswith("ref_"):
            referral_code = (
                args.removeprefix("ref_")
                .strip()
            )

    try:
        await register_user(
            telegram_id=user.id,
            username=user.username,
            first_name=user.first_name,
            referral_code=referral_code,
        )

    except Exception:
        logger.exception(
            "Не удалось зарегистрировать пользователя "
            "telegram_id=%s",
            user.id,
        )

        await message.answer(
            "Не удалось открыть профиль. "
            "Попробуйте ещё раз."
        )
        return

    subscribed = await is_channel_subscribed(
        bot=bot,
        user_id=user.id,
    )

    if not subscribed:
        await show_subscription_gate(
            message
        )
        return

    await message.answer(
        WELCOME_TEXT,
        reply_markup=build_main_menu(
            is_admin=(
                user.id in ADMIN_IDS
            )
        ),
    )


# ============================================================
# SUBSCRIPTION CHECK
# ============================================================

@router.callback_query(
    F.data == CB_CHECK_SUBSCRIPTION
)
async def check_subscription_handler(
    callback: CallbackQuery,
    bot: Bot,
) -> None:

    user_id = callback.from_user.id
    now = time.monotonic()
    last_check = _last_subscription_check.get(user_id)

    if (
        last_check is not None
        and now - last_check < SUBSCRIPTION_CHECK_COOLDOWN
    ):
        await callback.answer(
            "Слишком много попыток, попробуйте позже.",
            show_alert=True,
        )
        return

    _last_subscription_check[user_id] = now

    subscribed = await is_channel_subscribed(
        bot=bot,
        user_id=user_id,
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

    _last_subscription_check.pop(user_id, None)

    await callback.answer(
        "✅ Подписка подтверждена!"
    )

    if callback.message is None:
        return

    try:
        await callback.message.edit_text(
            WELCOME_TEXT,
            reply_markup=build_main_menu(
                is_admin=(
                    callback.from_user.id
                    in ADMIN_IDS
                )
            ),
        )

    except TelegramBadRequest as exc:
        if (
            "message is not modified"
            not in str(exc)
        ):
            raise


# ============================================================
# REFERRALS
# ============================================================

@router.callback_query(
    F.data == CB_INVITE
)
async def invite_handler(
    callback: CallbackQuery,
) -> None:

    try:
        referral_code = (
            await get_user_referral_code(
                callback.from_user.id
            )
        )

        referral_count = (
            await count_referrals(
                callback.from_user.id
            )
        )

    except Exception:
        logger.exception(
            "Не удалось загрузить referral-данные "
            "telegram_id=%s",
            callback.from_user.id,
        )

        await callback.answer(
            "Не удалось загрузить данные. "
            "Попробуйте позже.",
            show_alert=True,
        )
        return

    if not referral_code:
        await callback.answer(
            "Реферальная ссылка пока недоступна.",
            show_alert=True,
        )
        return

    bot_username = await get_bot_username(callback.bot)

    if not bot_username:
        await callback.answer(
            "Не удалось определить ссылку бота.",
            show_alert=True,
        )
        return

    referral_url = (
        f"https://t.me/{bot_username}"
        f"?start=ref_{referral_code}"
    )

    text = (
        "👥 Пригласить друзей\n\n"
        "Приглашай друзей в NexoVPN "
        "по своей персональной ссылке.\n\n"
        "🔗 Твоя ссылка:\n"
        f"{referral_url}\n\n"
        f"📊 Приглашено друзей: "
        f"{referral_count}\n\n"
        "Отправь ссылку друзьям — "
        "пусть запускают бота по ней."
    )

    await edit_menu(
        callback,
        text,
        build_referral_menu(
            referral_url
        ),
    )


# ============================================================
# HELP
# ============================================================

@router.callback_query(
    F.data == CB_HELP
)
async def help_handler(
    callback: CallbackQuery,
) -> None:

    await callback.answer()

    await edit_menu(
        callback,
        HELP_TEXT,
        build_help_menu(),
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

    try:
        balance_kopecks = (
            await get_user_balance(
                callback.from_user.id
            )
        )

    except Exception:
        logger.exception(
            "Не удалось получить баланс "
            "telegram_id=%s",
            callback.from_user.id,
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

    try:
        balance_kopecks = (
            await get_user_balance(
                callback.from_user.id
            )
        )

    except Exception:
        logger.exception(
            "Не удалось получить баланс "
            "после отмены пополнения"
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
# CONNECT
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
            "Не удалось загрузить обычные тарифы"
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
            build_plan_card_menu(
                CB_BACK
            ),
        )
        return

    await edit_menu(
        callback,
        build_single_plans_text(
            plans
        ),
        build_plans_menu(
            plans,
            CB_PLAN_PREFIX,
            CB_BACK,
        ),
    )


# ============================================================
# FAMILY
# ============================================================

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
            "Не удалось загрузить семейные тарифы"
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
            build_plan_card_menu(
                CB_BACK
            ),
        )
        return

    await edit_menu(
        callback,
        build_family_plans_text(
            plans
        ),
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
            "Не удалось загрузить тарифы "
            "для продления"
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
            build_plan_card_menu(
                CB_BACK
            ),
        )
        return

    await edit_menu(
        callback,
        build_renew_plans_text(
            plans
        ),
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
            "Не удалось загрузить тариф code=%s",
            code,
        )

        await callback.answer(
            "Не удалось загрузить тариф. "
            "Попробуйте позже.",
            show_alert=True,
        )
        return

    if plan is None:
        await callback.answer(
            "Этот тариф недоступен.",
            show_alert=True,
        )
        return

    if (
        renewal
        and plan["type"] != "single"
    ):
        await callback.answer(
            "Этот тариф недоступен "
            "для продления.",
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
        await callback.answer()
        return

    try:
        total_users = await count_users()

    except Exception:
        logger.exception(
            "Не удалось получить статистику"
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
            "Всего зарегистрировано пользователей: "
            f"{total_users}"
        ),
    )


# ============================================================
# PLACEHOLDER
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
