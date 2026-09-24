import asyncio
import logging
import time
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import quote

from aiogram import BaseMiddleware, Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
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
    get_active_family_plans,
    get_active_plans_by_device_limit,
    get_active_single_plans,
    get_active_subscription,
    get_plan_by_code,
    get_user_balance,
    get_user_promo,
    get_user_referral_code,
    activate_promo_code,
    consume_promo_code,
    is_promo_activated,
    register_user,
)

logger = logging.getLogger(__name__)

router = Router()


class SubscriptionGuardMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Any,
        event: Any,
        data: dict[str, Any],
    ) -> Any:
        if not isinstance(event, CallbackQuery):
            return await handler(event, data)

        if event.data == CB_CHECK_SUBSCRIPTION:
            return await handler(event, data)

        if await is_channel_subscribed(
            event.bot,
            event.from_user.id,
        ):
            return await handler(event, data)

        await event.answer(
            "Сначала подпишитесь на канал.",
            show_alert=True,
        )
        await show_subscription_gate_after_failed_check(event)
        return None


router.callback_query.middleware(
    SubscriptionGuardMiddleware()
)


async def safe_callback_answer(
    callback: CallbackQuery,
    *args: Any,
    **kwargs: Any,
) -> None:
    try:
        await callback.answer(*args, **kwargs)
    except TelegramBadRequest as exc:
        if "query is too old" in str(exc) or "query ID is invalid" in str(exc):
            logger.info("Пропущен просроченный callback query")
            return
        raise


# ============================================================
# CALLBACK DATA
# ============================================================

CB_CHECK_SUBSCRIPTION = "subscription:check"

CB_CONNECT = "menu:connect"
CB_DEVICE_3 = "menu:devices:3"
CB_DEVICE_6 = "menu:devices:6"
CB_TRIAL = "menu:trial"
CB_SUBSCRIPTION = "menu:subscription"
CB_RENEW = "menu:renew"
CB_BALANCE = "menu:balance"
CB_TOPUP = "menu:topup"
CB_FAMILY = "menu:family"
CB_INVITE = "menu:invite"
CB_HELP = "menu:help"
CB_PROMO = "menu:promo"
CB_DOCUMENTS = "menu:documents"
CB_BOT_PRIVACY = "documents:bot-privacy"
CB_BACK = "menu:back"
CB_MENU = "menu:main"
CB_CANCEL = "balance:cancel"
CB_ADMIN_STATS = "admin:stats"

CB_SHARE_REFERRAL = "referral:share"
CB_SUPPORT = "help:support"

CB_PLAN_PREFIX = "plan:"
CB_RENEW_PLAN_PREFIX = "renew:"

CB_TOPUP_METHOD_PREFIX = "topup:method:"
CB_TOPUP_METHOD_SBP = f"{CB_TOPUP_METHOD_PREFIX}sbp"
CB_TOPUP_METHOD_CARD = f"{CB_TOPUP_METHOD_PREFIX}card"

class PromoStates(StatesGroup):
    waiting_code = State()


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
    "🔹 Как купить подписку?\n"
    "Выбери «💎 Купить подписку», затем подходящий тариф.\n\n"
    "🔹 Сколько устройств можно подключить?\n"
    "Доступны варианты на 3 и 6 устройств.\n"
    "Подписка работает одновременно на выбранном числе устройств.\n\n"
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
                text="💎 Купить подписку",
                callback_data=CB_CONNECT,
            )
        ],
        [
            InlineKeyboardButton(
                text="📱 Моя подписка",
                callback_data=CB_SUBSCRIPTION,
            )
        ],
        [
            InlineKeyboardButton(
                text="💰 Баланс",
                callback_data=CB_BALANCE,
            ),
            InlineKeyboardButton(
                text="🎫 Промокод",
                callback_data=CB_PROMO,
            ),
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
        [
            InlineKeyboardButton(
                text="📄 Документы",
                callback_data=CB_DOCUMENTS,
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
        "💎 Подключайся к NexoVPN — стабильный "
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
                    text="👨‍💻 Написать в поддержку",
                    url="https://t.me/nexo_proxy_help",
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
                    text="💎 Купить подписку",
                    callback_data=CB_CONNECT,
                )
            ],
            [
                InlineKeyboardButton(
                    text="💳 Продлить подписку",
                    callback_data=CB_RENEW,
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


def build_topup_method_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🏦 СБП",
                    callback_data=CB_TOPUP_METHOD_SBP,
                )
            ],
            [
                InlineKeyboardButton(
                    text="💳 Банковская карта",
                    callback_data=CB_TOPUP_METHOD_CARD,
                )
            ],
            [
                InlineKeyboardButton(
                    text="◀️ Назад",
                    callback_data=CB_TOPUP,
                    style="danger",
                )
            ],
        ]
    )


def build_topup_method_text(
    amount_kopecks: int,
) -> str:
    return (
        "💳 Пополнение баланса\n\n"
        f"Сумма: {format_price(amount_kopecks)}\n\n"
        "Выберите способ оплаты:"
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


def discounted_price(
    price_kopecks: int,
    discount_percent: int,
) -> int:
    return round(
        price_kopecks * (100 - discount_percent) / 100
    )


def format_plan_line(
    plan: dict[str, Any],
    discount_percent: int = 0,
) -> str:

    months = plan_months(plan)
    price_kopecks = plan["price_kopecks"]
    display_price = discounted_price(
        price_kopecks,
        discount_percent,
    )

    line = (
        f"{plan_emoji(months)} "
        f"{months_label(months)} — "
        f"{format_price(display_price)}"
    )

    if discount_percent:
        line += f" (-{discount_percent}%)"

    if months > 1:
        per_month = (
            display_price + months * 50
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


def build_purchase_plans_text(
    plans: list[dict[str, Any]],
    device_limit: int,
    balance_kopecks: int,
    discount_percent: int = 0,
) -> str:
    return (
        "🗓 Выберите период подписки\n\n"
        f"💰 Ваш баланс: {format_balance(balance_kopecks)} ₽\n"
        f"📱 В стоимость входит: до {device_limit} устройств\n"
        "📊 Трафик: 150 ГБ/мес (сбрасывается ежемесячно)"
    )


def build_device_choice_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📱 3 устройства",
                    callback_data=CB_DEVICE_3,
                )
            ],
            [
                InlineKeyboardButton(
                    text="💻📱 6 устройств",
                    callback_data=CB_DEVICE_6,
                )
            ],
            [build_back_button(CB_BACK)],
        ]
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
    discount_percent: int = 0,
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

    display_price = discounted_price(
        plan["price_kopecks"],
        discount_percent,
    )
    discount_text = (
        f" (-{discount_percent}%)"
        if discount_percent
        else ""
    )

    return (
        f"{title}\n\n"
        f"{plan_emoji(months)} "
        f"Тариф: {months_label(months)}\n"
        f"⏳ Срок: {plan['duration_days']} дн.\n"
        f"📱 Устройства: {devices}\n"
        f"💰 Стоимость: "
        f"{format_price(display_price)}{discount_text}\n\n"
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
    discount_percent: int = 0,
    promo_plan_id: int | None = None,
) -> InlineKeyboardMarkup:

    buttons = []

    for plan in plans:
        months = plan_months(plan)
        plan_discount = (
            discount_percent
            if promo_plan_id is None or plan["id"] == promo_plan_id
            else 0
        )

        buttons.append(
            InlineKeyboardButton(
                text=(
                    f"{plan_emoji(months)} "
                    f"{months_label(months)} — "
                    f"{format_price(discounted_price(plan['price_kopecks'], plan_discount))}"
                    + (f" (-{plan_discount}%)" if plan_discount else "")
                ),
                callback_data=(
                    f"{prefix}{plan['code']}"
                ),
            )
        )

    rows = [
        [
            InlineKeyboardButton(
                text="🎁 3 дня бесплатно",
                callback_data=CB_TRIAL,
            )
        ]
    ] + [[button] for button in buttons]

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
                    text="💎 Купить подписку",
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


async def route_to_previous_screen(
    callback: CallbackQuery,
    state: FSMContext,
    target: str | None,
) -> None:
    if target == CB_SUBSCRIPTION:
        await subscription_handler(callback, state)
        return
    if target == CB_CONNECT:
        await connect_handler(callback, state)
        return
    if target == CB_DEVICE_3:
        await device_3_handler(callback, state)
        return
    if target == CB_DEVICE_6:
        await device_6_handler(callback, state)
        return
    if target == CB_RENEW:
        await renew_handler(callback, state)
        return
    if target == CB_BALANCE:
        await balance_handler(callback, state)
        return
    if target == CB_HELP:
        await help_handler(callback, state)
        return
    if target == CB_INVITE:
        await invite_handler(callback, state)
        return
    if target == CB_DOCUMENTS:
        await documents_handler(callback, state)
        return
    if target == CB_FAMILY:
        await family_handler(callback, state)
        return
    await menu_back_handler(callback, state)


async def get_effective_promo_state(
    telegram_id: int,
) -> tuple[int, int | None]:
    promo = await get_user_promo(telegram_id)

    if promo is None:
        return 0, None

    if promo.get("promo_reward_type") != "discount":
        return 0, None

    discount_percent = int(promo.get("promo_reward_value") or 0)
    promo_plan_id = (
        int(promo["promo_plan_id"])
        if promo.get("promo_plan_id") is not None
        else None
    )

    return max(0, discount_percent), promo_plan_id


def build_documents_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📄 Пользовательское соглашение",
                    url="https://telegra.ph/POLITIKA-KONFIDENCIALNOSTI-08-12-99",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🔒 Политика конфиденциальности",
                    url="https://telegra.ph/PUBLICHNAYA-OFERTA-08-12-15",
                )
            ],
            [
                InlineKeyboardButton(
                    text="📇 Политика бота",
                    callback_data=CB_BOT_PRIVACY,
                )
            ],
            [
                InlineKeyboardButton(
                    text="◀️ Назад в меню",
                    callback_data=CB_MENU,
                    style="danger",
                )
            ],
        ]
    )


# ============================================================
# SUBSCRIPTION PLACEHOLDER
# ============================================================

async def has_active_subscription(
    telegram_id: int,
) -> bool:
    return await get_active_subscription(telegram_id) is not None


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
    state: FSMContext,
) -> None:

    await state.update_data(back_target=CB_MENU)

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
    state: FSMContext,
) -> None:

    await callback.answer()
    await state.update_data(back_target=CB_MENU)

    await edit_menu(
        callback,
        HELP_TEXT,
        build_help_menu(),
    )


@router.callback_query(
    F.data == CB_PROMO
)
async def promo_handler(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:

    await callback.answer()
    await state.set_state(PromoStates.waiting_code)
    await state.update_data(prompt_message_id=callback.message.message_id)

    await edit_menu(
        callback,
        "🎫 Промокод\n\n"
        "Введите промокод сообщением в чат:",
        build_plan_card_menu(CB_BACK),
    )


@router.message(PromoStates.waiting_code)
async def promo_code_handler(
    message: Message,
    state: FSMContext,
) -> None:
    promo_code = (message.text or "").strip().upper()
    data = await state.get_data()
    prompt_message_id = data.get("prompt_message_id")

    try:
        if await is_promo_activated(message.from_user.id):
            await state.clear()
            if prompt_message_id is not None:
                try:
                    await message.bot.edit_message_text(
                        "❌ Промокод уже был использован.",
                        chat_id=message.chat.id,
                        message_id=prompt_message_id,
                        reply_markup=build_plan_card_menu(CB_BACK),
                    )
                    return
                except TelegramBadRequest:
                    pass
            await message.answer("Промокод уже был использован.")
            return

        activated = await activate_promo_code(
            message.from_user.id,
            promo_code,
        )
    except Exception:
        logger.exception(
            "Не удалось активировать промокод telegram_id=%s",
            message.from_user.id,
        )
        if prompt_message_id is not None:
            try:
                await message.bot.edit_message_text(
                    "Не удалось активировать промокод. Попробуйте позже.",
                    chat_id=message.chat.id,
                    message_id=prompt_message_id,
                    reply_markup=build_plan_card_menu(CB_BACK),
                )
                await state.clear()
                return
            except TelegramBadRequest:
                pass
        await message.answer(
            "Не удалось активировать промокод. Попробуйте позже."
        )
        await state.clear()
        return

    await state.clear()

    if activated is not None:
        if activated["promo_reward_type"] == "discount":
            reward_text = (
                f"Промокод даёт скидку {activated['promo_reward_value']}% "
                "на одну покупку подписки."
            )
        else:
            reward_text = (
                f"Промокод даёт {activated['promo_reward_value']} "
                "бесплатных дней подписки."
            )

        result_text = (
            "🎁 Промокод успешно активирован!\n\n"
            f"{reward_text}"
        )
        if prompt_message_id is not None:
            try:
                await message.bot.edit_message_text(
                    result_text,
                    chat_id=message.chat.id,
                    message_id=prompt_message_id,
                    reply_markup=build_plan_card_menu(CB_BACK),
                )
                return
            except TelegramBadRequest:
                pass

        await message.answer(result_text)
        return

    invalid_text = (
        "❌ Промокод недействителен, истёк, закончился или уже использован."
    )
    if prompt_message_id is not None:
        try:
            await message.bot.edit_message_text(
                invalid_text,
                chat_id=message.chat.id,
                message_id=prompt_message_id,
                reply_markup=build_plan_card_menu(CB_BACK),
            )
            return
        except TelegramBadRequest:
            pass

    await message.answer(invalid_text)


@router.callback_query(
    F.data == CB_DOCUMENTS
)
async def documents_handler(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:

    await callback.answer()
    await state.update_data(back_target=CB_MENU)

    await edit_menu(
        callback,
        "📄 Документы Nexo VPN\n\n"
        "Ниже — все документы сервиса:",
        build_documents_menu(),
    )


@router.callback_query(
    F.data == CB_BOT_PRIVACY
)
async def bot_privacy_handler(
    callback: CallbackQuery,
) -> None:

    await callback.answer()

    await edit_menu(
        callback,
        "📇 Политика бота\n\n"
        "1. Мы храним только данные, необходимые для работы сервиса: "
        "ваш Telegram ID, username, баланс и историю покупок подписок.\n\n"
        "2. Мы не ведём логи посещённых сайтов и не анализируем содержимое "
        "VPN-трафика.\n\n"
        "3. Данные об оплате обрабатываются платёжным партнёром RollyPay "
        "согласно его правилам.\n\n"
        "4. Данные не передаются третьим лицам, кроме случаев, "
        "предусмотренных законом.\n\n"
        "5. Вы можете запросить удаление своих данных, обратившись в "
        "поддержку: https://t.me/nexo_proxy_help",
        build_plan_card_menu(CB_DOCUMENTS),
    )


# ============================================================
# SUBSCRIPTION
# ============================================================

@router.callback_query(
    F.data == CB_SUBSCRIPTION
)
async def subscription_handler(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:

    try:
        subscription = await get_active_subscription(
            callback.from_user.id
        )
    except Exception:
        logger.exception(
            "Не удалось загрузить подписку telegram_id=%s",
            callback.from_user.id,
        )
        await callback.answer(
            "Не удалось загрузить подписку. Попробуйте позже.",
            show_alert=True,
        )
        return

    await callback.answer()
    await state.update_data(back_target=CB_MENU)

    if subscription is None:
        text = "📱 Моя подписка\n\nСтатус: Нет активной подписки"
    else:
        expires_at = subscription["expires_at"].strftime("%d.%m.%Y")
        text = (
            "📱 Моя подписка\n\n"
            "Статус: Активна\n"
            f"Тариф: {subscription['plan_code'] or 'не указан'}\n"
            f"Устройства: до {subscription['device_limit']}\n"
            f"Действует до: {expires_at}"
        )

    await edit_menu(
        callback,
        text,
        build_subscription_menu(),
    )


# ============================================================
# BALANCE
# ============================================================

@router.callback_query(
    F.data == CB_BALANCE
)
async def balance_handler(
    callback: CallbackQuery,
    state: FSMContext,
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
    await state.update_data(back_target=CB_MENU)

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

class TopupStates(StatesGroup):
    waiting_amount = State()
    choosing_method = State()


@router.callback_query(
    F.data == CB_TOPUP
)
async def topup_handler(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:

    await callback.answer()

    if callback.message is None:
        return

    await state.set_state(
        TopupStates.waiting_amount
    )
    await state.update_data(
        topup_prompt_message_id=(
            callback.message.message_id
        )
    )

    await callback.message.edit_text(
        "💳 Введите сумму пополнения "
        "в рублях (например: 100):",
        reply_markup=build_topup_cancel_menu(),
    )


MAX_TOPUP_RUB = Decimal("1000000")


@router.message(
    TopupStates.waiting_amount
)
async def topup_amount_handler(
    message: Message,
    state: FSMContext,
) -> None:

    raw_amount = (
        (message.text or "")
        .strip()
        .replace(",", ".")
    )

    data = await state.get_data()
    prompt_message_id = data.get(
        "topup_prompt_message_id"
    )

    try:
        amount_rub = Decimal(raw_amount)
        amount_kopecks_decimal = amount_rub * 100
        amount_kopecks = int(amount_kopecks_decimal)
    except (InvalidOperation, OverflowError, ValueError):
        error_text = (
            "Не удалось распознать сумму. "
            "Введите сумму в рублях с точностью до копеек, "
            "например: 100 или 100,50"
        )
        if prompt_message_id is not None:
            try:
                await message.bot.edit_message_text(
                    error_text,
                    chat_id=message.chat.id,
                    message_id=prompt_message_id,
                    reply_markup=build_topup_cancel_menu(),
                )
                return
            except TelegramBadRequest:
                pass
        await message.answer(error_text)
        return

    if (
        amount_rub is None
        or not amount_rub.is_finite()
        or amount_rub <= 0
        or amount_rub > MAX_TOPUP_RUB
        or amount_kopecks_decimal != amount_kopecks
    ):
        error_text = (
            "Не удалось распознать сумму. "
            "Введите сумму в рублях с точностью до копеек, "
            "например: 100 или 100,50; максимум — 1 000 000 ₽"
        )
        if prompt_message_id is not None:
            try:
                await message.bot.edit_message_text(
                    error_text,
                    chat_id=message.chat.id,
                    message_id=prompt_message_id,
                    reply_markup=build_topup_cancel_menu(),
                )
                return
            except TelegramBadRequest:
                pass
        await message.answer(error_text)
        return

    await state.set_state(
        TopupStates.choosing_method
    )
    await state.update_data(
        topup_amount_kopecks=amount_kopecks
    )

    text = build_topup_method_text(
        amount_kopecks
    )
    markup = build_topup_method_menu()

    if prompt_message_id:
        try:
            await message.bot.edit_message_text(
                text,
                chat_id=message.chat.id,
                message_id=prompt_message_id,
                reply_markup=markup,
            )
            return

        except TelegramBadRequest:
            pass

    await message.answer(
        text,
        reply_markup=markup,
    )


@router.callback_query(
    F.data == CB_CANCEL
)
async def cancel_topup_handler(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:

    await state.clear()
    await callback.answer()

    if callback.message is None:
        return

    await balance_handler(callback, state)


@router.callback_query(
    F.data.startswith(
        CB_TOPUP_METHOD_PREFIX
    )
)
async def topup_method_handler(
    callback: CallbackQuery,
) -> None:

    await callback.answer(
        "🔜 Этот способ оплаты "
        "скоро будет доступен.",
        show_alert=True,
    )


# ============================================================
# BACK
# ============================================================

@router.callback_query(
    F.data == CB_MENU
)
async def menu_back_handler(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:

    await state.clear()
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


@router.callback_query(
    F.data == CB_BACK
)
async def back_handler(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:

    await callback.answer()
    data = await state.get_data()
    target = data.get("back_target")
    if target is None:
        target = CB_MENU
    await route_to_previous_screen(callback, state, target)


# ============================================================
# CONNECT
# ============================================================

@router.callback_query(
    F.data == CB_CONNECT
)
async def connect_handler(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:

    await safe_callback_answer(callback)
    await state.update_data(back_target=CB_MENU)

    await edit_menu(
        callback,
        "📱 Выберите количество устройств\n\n"
        "Подписка будет работать одновременно "
        "на выбранном числе устройств.",
        build_device_choice_menu(),
    )


async def show_device_plans(
    callback: CallbackQuery,
    device_limit: int,
    state: FSMContext,
) -> None:

    try:
        plans = await get_active_plans_by_device_limit(
            device_limit
        )
        balance_kopecks = await get_user_balance(
            callback.from_user.id
        )
        discount_percent, promo_plan_id = await get_effective_promo_state(
            callback.from_user.id,
        )

    except Exception:
        logger.exception(
            "Не удалось загрузить тарифы для %s устройств",
            device_limit,
        )

        await callback.answer(
            "Не удалось загрузить тарифы. "
            "Попробуйте позже.",
            show_alert=True,
        )
        return

    await callback.answer()
    await state.update_data(back_target=CB_CONNECT)

    if not plans:
        await edit_menu(
            callback,
            f"Тарифы на {device_limit} устройств "
            "временно недоступны. Попробуйте позже.",
            build_plan_card_menu(
                CB_CONNECT
            ),
        )
        return

    await edit_menu(
        callback,
        build_purchase_plans_text(
            plans,
            device_limit=device_limit,
            balance_kopecks=balance_kopecks,
            discount_percent=discount_percent,
        ),
        build_plans_menu(
            plans,
            CB_PLAN_PREFIX,
            CB_CONNECT,
            discount_percent=discount_percent,
            promo_plan_id=promo_plan_id,
        ),
    )


@router.callback_query(
    F.data == CB_DEVICE_3
)
async def device_3_handler(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    await show_device_plans(callback, 3, state)


@router.callback_query(
    F.data == CB_DEVICE_6
)
async def device_6_handler(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    await show_device_plans(callback, 6, state)


@router.callback_query(
    F.data == CB_TRIAL
)
async def trial_handler(
    callback: CallbackQuery,
) -> None:
    await callback.answer()

    await edit_menu(
        callback,
        "🎁 Пробные 3 дня\n\n"
        "Активация пробного периода пока недоступна.",
        build_plan_card_menu(CB_CONNECT),
    )


# ============================================================
# FAMILY
# ============================================================

@router.callback_query(
    F.data == CB_FAMILY
)
async def family_handler(
    callback: CallbackQuery,
    state: FSMContext,
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
    await state.update_data(back_target=CB_MENU)

    if not plans:
        await edit_menu(
            callback,
            "Семейные тарифы временно "
            "недоступны. Попробуйте позже.",
            build_plan_card_menu(
                CB_MENU
            ),
        )
        return

    discount_percent, promo_plan_id = await get_effective_promo_state(
        callback.from_user.id,
    )

    await edit_menu(
        callback,
        build_family_plans_text(
            plans
        ),
        build_plans_menu(
            plans,
            CB_PLAN_PREFIX,
            CB_CONNECT,
            discount_percent=discount_percent,
            promo_plan_id=promo_plan_id,
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
    state: FSMContext,
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
    await state.update_data(back_target=CB_SUBSCRIPTION)

    if not plans:
        await edit_menu(
            callback,
            "Тарифы временно недоступны. "
            "Попробуйте позже.",
            build_plan_card_menu(
                CB_SUBSCRIPTION
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
            CB_SUBSCRIPTION,
        ),
    )


# ============================================================
# PLAN CARD
# ============================================================

async def show_plan_card(
    callback: CallbackQuery,
    code: str,
    renewal: bool,
    state: FSMContext,
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

    promo = None if renewal else await get_user_promo(
        callback.from_user.id,
    )

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

    promo_applies = (
        promo is not None
        and (
            promo["promo_plan_id"] is None
            or promo["promo_plan_id"] == plan["id"]
        )
    )
    discount_percent = (
        int(promo["promo_reward_value"])
        if promo_applies
        and promo["promo_reward_type"] == "discount"
        else 0
    )

    await callback.answer()

    if renewal:
        back_callback = CB_RENEW
    elif plan["type"] == "family":
        back_callback = CB_FAMILY
    else:
        back_callback = (
            CB_DEVICE_3
            if int(plan["device_limit"]) == 3
            else CB_DEVICE_6
        )

    await state.update_data(back_target=back_callback)

    await edit_menu(
        callback,
        format_plan_card(
            plan,
            renewal=renewal,
            discount_percent=discount_percent,
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
    state: FSMContext,
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
        state=state,
    )


@router.callback_query(
    F.data.startswith(
        CB_RENEW_PLAN_PREFIX
    )
)
async def renew_plan_selected_handler(
    callback: CallbackQuery,
    state: FSMContext,
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
        state=state,
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
