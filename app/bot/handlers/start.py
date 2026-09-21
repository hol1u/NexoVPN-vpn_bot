import logging

from aiogram import Bot, F, Router
from aiogram.filters import CommandObject, CommandStart
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

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


# ============================================================
# CALLBACKS
# ============================================================

CB_CONNECT = "menu:connect"
CB_SUBSCRIPTION = "menu:subscription"
CB_RENEW = "menu:renew"
CB_BALANCE = "menu:balance"
CB_TOPUP = "menu:topup"
CB_FAMILY = "menu:family"
CB_INVITE = "menu:invite"
CB_HELP = "menu:help"
CB_BACK = "menu:back"
CB_ADMIN_STATS = "admin:stats"


# ============================================================
# TEXT
# ============================================================

WELCOME_TEXT = (
    "🍁 <b>NexoVPN</b>\n\n"
    "Быстрый и стабильный VPN для ваших устройств.\n\n"
    "🔐 Защищённое соединение\n"
    "⚡ Высокая скорость\n"
    "🌍 Доступ к интернету без лишних ограничений\n\n"
    "Выберите нужный раздел:"
)


# ============================================================
# KEYBOARDS
# ============================================================

def build_main_menu(
    is_admin: bool = False,
) -> InlineKeyboardMarkup:
    buttons = [
        [
            InlineKeyboardButton(
                text="🍁 Подключить VPN",
                callback_data=CB_CONNECT,
            )
        ],
        [
            InlineKeyboardButton(
                text="📦 Моя подписка",
                callback_data=CB_SUBSCRIPTION,
            ),
            InlineKeyboardButton(
                text="🔄 Продлить",
                callback_data=CB_RENEW,
            ),
        ],
        [
            InlineKeyboardButton(
                text="💰 Баланс",
                callback_data=CB_BALANCE,
            ),
            InlineKeyboardButton(
                text="💳 Пополнить",
                callback_data=CB_TOPUP,
            ),
        ],
        [
            InlineKeyboardButton(
                text="👨‍👩‍👧‍👦 Семейный VPN",
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
                text="❓ Помощь",
                callback_data=CB_HELP,
            )
        ],
    ]

    if is_admin:
        buttons.append(
            [
                InlineKeyboardButton(
                    text="🛠 Админ",
                    callback_data=CB_ADMIN_STATS,
                )
            ]
        )

    return InlineKeyboardMarkup(
        inline_keyboard=buttons
    )


def build_back_menu(
    callback_data: str = CB_BACK,
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="◀️ Назад",
                    callback_data=callback_data,
                )
            ]
        ]
    )


def build_plan_card_menu(
    back_callback: str = CB_BACK,
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="◀️ Назад",
                    callback_data=back_callback,
                )
            ]
        ]
    )


def build_plans_menu(
    plans: list[dict],
    prefix: str,
) -> InlineKeyboardMarkup:
    buttons = []

    for plan in plans:
        duration_days = int(plan["duration_days"])
        price_kopecks = int(plan["price_kopecks"])

        if duration_days == 30:
            duration_text = "1 месяц"
        elif duration_days == 90:
            duration_text = "3 месяца"
        elif duration_days == 180:
            duration_text = "6 месяцев"
        elif duration_days == 365:
            duration_text = "12 месяцев"
        else:
            duration_text = f"{duration_days} дней"

        price_rubles = price_kopecks / 100

        buttons.append(
            [
                InlineKeyboardButton(
                    text=f"🍁 {duration_text} — {price_rubles:.0f} ₽",
                    callback_data=f"{prefix}:{plan['code']}",
                )
            ]
        )

    buttons.append(
        [
            InlineKeyboardButton(
                text="◀️ Назад",
                callback_data=CB_BACK,
            )
        ]
    )

    return InlineKeyboardMarkup(
        inline_keyboard=buttons
    )


# ============================================================
# HELPERS
# ============================================================

async def edit_menu(
    callback: CallbackQuery,
    text: str,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> None:
    if callback.message is None:
        return

    try:
        await callback.message.edit_text(
            text,
            reply_markup=reply_markup,
        )
    except Exception:
        logger.exception(
            "Не удалось изменить сообщение меню"
        )

        await callback.message.answer(
            text,
            reply_markup=reply_markup,
        )


# ============================================================
# START
# ============================================================

@router.message(CommandStart())
async def start_handler(
    message: Message,
    command: CommandObject,
) -> None:
    user = message.from_user

    if user is None:
        logger.error(
            "START без from_user"
        )
        return

    logger.info(
        "START: telegram_id=%s username=%s args=%r",
        user.id,
        user.username,
        command.args,
    )

    # --------------------------------------------------------
    # Проверяем БД
    # --------------------------------------------------------

    db_ok = await check_db()

    if not db_ok:
        logger.error(
            "START: база данных недоступна: telegram_id=%s",
            user.id,
        )

        await message.answer(
            "⚠️ Сервис временно недоступен.\n\n"
            "Попробуйте ещё раз немного позже."
        )

        return

    # --------------------------------------------------------
    # Получаем referral code
    # --------------------------------------------------------

    referral_code = None

    if command.args:
        referral_code = command.args.strip()

    # --------------------------------------------------------
    # Регистрируем / обновляем пользователя
    # --------------------------------------------------------

    try:
        await register_user(
            telegram_id=user.id,
            username=user.username,
            first_name=user.first_name,
            referral_code=referral_code,
        )

        logger.info(
            "Пользователь успешно зарегистрирован/обновлён: "
            "telegram_id=%s",
            user.id,
        )

    except Exception as exc:
        # ВАЖНО:
        # здесь теперь будет полный traceback в Railway Logs.
        logger.exception(
            "ОШИБКА REGISTER_USER: "
            "telegram_id=%s "
            "username=%s "
            "referral_code=%r "
            "error_type=%s "
            "error=%s",
            user.id,
            user.username,
            referral_code,
            type(exc).__name__,
            str(exc),
        )

        await message.answer(
            "❌ Не удалось открыть профиль.\n\n"
            "Попробуйте ещё раз."
        )

        return

    # --------------------------------------------------------
    # Главное меню
    # --------------------------------------------------------

    await message.answer(
        WELCOME_TEXT,
        reply_markup=build_main_menu(
            is_admin=user.id in ADMIN_IDS
        ),
    )


# ============================================================
# CONNECT
# ============================================================

@router.callback_query(F.data == CB_CONNECT)
async def connect_handler(
    callback: CallbackQuery,
) -> None:
    await callback.answer()

    text = (
        "🍁 <b>Подключить VPN</b>\n\n"
        "Выберите подходящий тариф.\n\n"
        "👤 Обычный тариф — 1 устройство\n"
        "👨‍👩‍👧‍👦 Семейный — до 5 устройств"
    )

    try:
        plans = await get_active_single_plans()

        await edit_menu(
            callback,
            text,
            build_plans_menu(
                plans,
                "plan",
            ),
        )

    except Exception:
        logger.exception(
            "Ошибка открытия раздела подключения"
        )

        if callback.message:
            await callback.message.answer(
                "❌ Не удалось открыть тарифы.\n"
                "Попробуйте ещё раз."
            )


# ============================================================
# SUBSCRIPTION
# ============================================================

@router.callback_query(F.data == CB_SUBSCRIPTION)
async def subscription_handler(
    callback: CallbackQuery,
) -> None:
    await callback.answer()

    text = (
        "📦 <b>Моя подписка</b>\n\n"
        "Пока активной VPN-подписки нет.\n\n"
        "Выберите тариф, чтобы подключить VPN."
    )

    await edit_menu(
        callback,
        text,
        build_plan_card_menu(),
    )


# ============================================================
# RENEW
# ============================================================

@router.callback_query(F.data == CB_RENEW)
async def renew_handler(
    callback: CallbackQuery,
) -> None:
    await callback.answer()

    text = (
        "🔄 <b>Продлить подписку</b>\n\n"
        "Выберите срок продления."
    )

    try:
        plans = await get_active_single_plans()

        await edit_menu(
            callback,
            text,
            build_plans_menu(
                plans,
                "renew",
            ),
        )

    except Exception:
        logger.exception(
            "Ошибка открытия продления"
        )

        if callback.message:
            await callback.message.answer(
                "❌ Не удалось открыть раздел продления."
            )


# ============================================================
# BALANCE
# ============================================================

@router.callback_query(F.data == CB_BALANCE)
async def balance_handler(
    callback: CallbackQuery,
) -> None:
    await callback.answer()

    try:
        balance = await get_user_balance(
            callback.from_user.id
        )

        rubles = balance / 100

        text = (
            "💰 <b>Баланс</b>\n\n"
            f"Ваш баланс: <b>{rubles:.2f} ₽</b>\n\n"
            "Пополните баланс, чтобы оплатить VPN."
        )

        await edit_menu(
            callback,
            text,
            build_back_menu(),
        )

    except Exception:
        logger.exception(
            "Ошибка получения баланса: telegram_id=%s",
            callback.from_user.id,
        )

        if callback.message:
            await callback.message.answer(
                "❌ Не удалось получить баланс."
            )


# ============================================================
# TOP UP
# ============================================================

@router.callback_query(F.data == CB_TOPUP)
async def topup_handler(
    callback: CallbackQuery,
) -> None:
    await callback.answer()

    text = (
        "💳 <b>Пополнение баланса</b>\n\n"
        "Раздел оплаты будет подключён после настройки "
        "платёжной системы."
    )

    await edit_menu(
        callback,
        text,
        build_back_menu(),
    )


# ============================================================
# FAMILY
# ============================================================

@router.callback_query(F.data == CB_FAMILY)
async def family_handler(
    callback: CallbackQuery,
) -> None:
    await callback.answer()

    text = (
        "👨‍👩‍👧‍👦 <b>Семейный VPN</b>\n\n"
        "Один тариф для всей семьи.\n\n"
        "👥 До 5 устройств."
    )

    try:
        plans = await get_active_family_plans()

        await edit_menu(
            callback,
            text,
            build_plans_menu(
                plans,
                "family",
            ),
        )

    except Exception:
        logger.exception(
            "Ошибка открытия семейных тарифов"
        )

        if callback.message:
            await callback.message.answer(
                "❌ Не удалось открыть семейные тарифы."
            )


# ============================================================
# INVITE / REFERRAL
# ============================================================

@router.callback_query(F.data == CB_INVITE)
async def invite_handler(
    callback: CallbackQuery,
    bot: Bot,
) -> None:
    # Отвечаем на callback сразу,
    # чтобы Telegram не показывал бесконечную загрузку.
    await callback.answer()

    user_id = callback.from_user.id

    logger.info(
        "Открытие рефералки: telegram_id=%s",
        user_id,
    )

    try:
        referral_code = await get_user_referral_code(
            user_id
        )

        referrals_count = await count_referrals(
            user_id
        )

    except Exception as exc:
        logger.exception(
            "Ошибка открытия рефералки: "
            "telegram_id=%s "
            "error_type=%s "
            "error=%s",
            user_id,
            type(exc).__name__,
            str(exc),
        )

        if callback.message:
            await callback.message.answer(
                "❌ Не удалось открыть раздел приглашений.\n"
                "Попробуйте ещё раз."
            )

        return

    if not referral_code:
        logger.error(
            "У пользователя отсутствует referral_code: telegram_id=%s",
            user_id,
        )

        if callback.message:
            await callback.message.answer(
                "❌ Не удалось создать реферальную ссылку.\n"
                "Попробуйте ещё раз."
            )

        return

    try:
        me = await bot.get_me()

        if not me.username:
            logger.error(
                "У бота отсутствует username"
            )

            if callback.message:
                await callback.message.answer(
                    "❌ У бота не установлен username."
                )

            return

        referral_link = (
            f"https://t.me/{me.username}"
            f"?start={referral_code}"
        )

        text = (
            "👥 <b>Пригласить друзей</b>\n\n"
            "Приглашайте друзей в NexoVPN "
            "по вашей персональной ссылке.\n\n"
            f"🔗 <b>Ваша ссылка:</b>\n"
            f"{referral_link}\n\n"
            f"👤 <b>Приглашено:</b> {referrals_count}"
        )

        await edit_menu(
            callback,
            text,
            build_back_menu(),
        )

    except Exception as exc:
        logger.exception(
            "Ошибка формирования реферальной ссылки: "
            "telegram_id=%s "
            "error_type=%s "
            "error=%s",
            user_id,
            type(exc).__name__,
            str(exc),
        )

        if callback.message:
            await callback.message.answer(
                "❌ Не удалось открыть раздел приглашений."
            )


# ============================================================
# HELP
# ============================================================

@router.callback_query(F.data == CB_HELP)
async def help_handler(
    callback: CallbackQuery,
) -> None:
    await callback.answer()

    text = (
        "❓ <b>Помощь</b>\n\n"
        "Если у вас возникли проблемы с подключением "
        "или оплатой, обратитесь в поддержку."
    )

    await edit_menu(
        callback,
        text,
        build_back_menu(),
    )


# ============================================================
# ADMIN STATS
# ============================================================

@router.callback_query(F.data == CB_ADMIN_STATS)
async def admin_stats_handler(
    callback: CallbackQuery,
) -> None:
    await callback.answer()

    if callback.from_user.id not in ADMIN_IDS:
        if callback.message:
            await callback.message.answer(
                "⛔ Доступ запрещён."
            )

        return

    try:
        users_count = await count_users()

        text = (
            "🛠 <b>Админ-панель</b>\n\n"
            f"👤 Пользователей: <b>{users_count}</b>"
        )

        await edit_menu(
            callback,
            text,
            build_back_menu(),
        )

    except Exception as exc:
        logger.exception(
            "Ошибка получения статистики: "
            "telegram_id=%s "
            "error_type=%s "
            "error=%s",
            callback.from_user.id,
            type(exc).__name__,
            str(exc),
        )

        if callback.message:
            await callback.message.answer(
                "❌ Не удалось получить статистику."
            )


# ============================================================
# BACK
# ============================================================

@router.callback_query(F.data == CB_BACK)
async def back_handler(
    callback: CallbackQuery,
) -> None:
    await callback.answer()

    user = callback.from_user

    await edit_menu(
        callback,
        WELCOME_TEXT,
        build_main_menu(
            is_admin=user.id in ADMIN_IDS
        ),
    )


# ============================================================
# PLAN CALLBACKS
# ============================================================

@router.callback_query(F.data.startswith("plan:"))
async def plan_handler(
    callback: CallbackQuery,
) -> None:
    await callback.answer()

    plan_code = callback.data.split(
        ":",
        1,
    )[1]

    try:
        plan = await get_plan_by_code(
            plan_code
        )

        if plan is None:
            if callback.message:
                await callback.message.answer(
                    "❌ Тариф не найден."
                )

            return

        price_rubles = (
            int(plan["price_kopecks"]) / 100
        )

        text = (
            "🍁 <b>Выбран тариф</b>\n\n"
            f"📦 {plan['duration_days']} дней\n"
            f"💰 {price_rubles:.0f} ₽\n"
            f"📱 Устройств: {plan['device_limit']}\n\n"
            "Оплата будет подключена после настройки "
            "платёжной системы."
        )

        await edit_menu(
            callback,
            text,
            build_back_menu(),
        )

    except Exception:
        logger.exception(
            "Ошибка открытия тарифа: %s",
            plan_code,
        )

        if callback.message:
            await callback.message.answer(
                "❌ Не удалось открыть тариф."
            )


@router.callback_query(F.data.startswith("renew:"))
async def renew_plan_handler(
    callback: CallbackQuery,
) -> None:
    await callback.answer()

    plan_code = callback.data.split(
        ":",
        1,
    )[1]

    try:
        plan = await get_plan_by_code(
            plan_code
        )

        if plan is None:
            if callback.message:
                await callback.message.answer(
                    "❌ Тариф не найден."
                )

            return

        price_rubles = (
            int(plan["price_kopecks"]) / 100
        )

        text = (
            "🔄 <b>Продление</b>\n\n"
            f"📦 {plan['duration_days']} дней\n"
            f"💰 {price_rubles:.0f} ₽\n\n"
            "Оплата будет подключена после настройки "
            "платёжной системы."
        )

        await edit_menu(
            callback,
            text,
            build_back_menu(),
        )

    except Exception:
        logger.exception(
            "Ошибка открытия продления тарифа: %s",
            plan_code,
        )

        if callback.message:
            await callback.message.answer(
                "❌ Не удалось открыть тариф."
            )


# ============================================================
# FAMILY PLAN CALLBACKS
# ============================================================

@router.callback_query(F.data.startswith("family:"))
async def family_plan_handler(
    callback: CallbackQuery,
) -> None:
    await callback.answer()

    plan_code = callback.data.split(
        ":",
        1,
    )[1]

    try:
        plan = await get_plan_by_code(
            plan_code
        )

        if plan is None:
            if callback.message:
                await callback.message.answer(
                    "❌ Тариф не найден."
                )

            return

        price_rubles = (
            int(plan["price_kopecks"]) / 100
        )

        text = (
            "👨‍👩‍👧‍👦 <b>Семейный VPN</b>\n\n"
            f"📦 {plan['duration_days']} дней\n"
            f"💰 {price_rubles:.0f} ₽\n"
            f"📱 До {plan['device_limit']} устройств\n\n"
            "Оплата будет подключена после настройки "
            "платёжной системы."
        )

        await edit_menu(
            callback,
            text,
            build_back_menu(),
        )

    except Exception:
        logger.exception(
            "Ошибка открытия семейного тарифа: %s",
            plan_code,
        )

        if callback.message:
            await callback.message.answer(
                "❌ Не удалось открыть семейный тариф."
            )


# ============================================================
# FALLBACK
# ============================================================

@router.callback_query(F.data.startswith("menu:"))
async def menu_placeholder_handler(
    callback: CallbackQuery,
) -> None:
    await callback.answer()

    if callback.message:
        await callback.message.answer(
            "Этот раздел скоро появится."
        )
