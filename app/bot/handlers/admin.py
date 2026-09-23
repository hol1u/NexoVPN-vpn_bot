import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware, Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.config import ADMIN_IDS
from app.db import (
    create_broadcast,
    create_promo_code,
    finish_broadcast,
    get_active_family_plans,
    get_active_single_plans,
    get_admin_logs,
    get_admin_payments,
    get_admin_stats,
    get_broadcast_recipients,
    get_promo_summary,
    get_referral_summary,
    get_referral_top,
    get_subscription_summary,
    write_admin_log,
)

logger = logging.getLogger(__name__)
router = Router()

class AdminErrorMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[Any, dict[str, Any]], Awaitable[Any]],
        event: Any,
        data: dict[str, Any],
    ) -> Any:
        try:
            return await handler(event, data)
        except Exception:
            logger.exception("Ошибка в административном обработчике")
            if isinstance(event, CallbackQuery) and event.message:
                try:
                    await event.message.edit_text(
                        "⚠️ Не удалось открыть раздел админ-панели.\n\n"
                        "Проверьте состояние базы данных и логи сервиса.",
                        reply_markup=back(),
                    )
                except Exception:
                    logger.exception("Не удалось показать ошибку администратору")
            return None


router.callback_query.middleware(AdminErrorMiddleware())


class PromoStates(StatesGroup):
    code = State()
    reward_type = State()
    reward_value = State()
    total_limit = State()
    per_user_limit = State()
    starts_at = State()
    ends_at = State()
    plan_code = State()


class BroadcastStates(StatesGroup):
    audience = State()
    message = State()
    confirm = State()


def is_admin(user_id: int | None) -> bool:
    return user_id is not None and user_id in ADMIN_IDS


def kb(rows: list[list[tuple[str, str]]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text=text,
                callback_data=data,
                style=(
                    "danger"
                    if text.startswith(("⬅️", "❌", "Отмена"))
                    else None
                ),
            )
            for text, data in row
        ]
        for row in rows
    ])


def back(target: str = "admin:menu") -> InlineKeyboardMarkup:
    return kb([[('⬅️ Назад', target)]])


def money(kopecks: int) -> str:
    return f"{kopecks / 100:.2f} ₽"


def short_dt(value: Any) -> str:
    return value.astimezone(timezone.utc).strftime("%d.%m %H:%M") if value else "-"


async def safe_callback_answer(callback: CallbackQuery, *args: Any, **kwargs: Any) -> None:
    try:
        await callback.answer(*args, **kwargs)
    except TelegramBadRequest as exc:
        if "query is too old" in str(exc) or "query ID is invalid" in str(exc):
            logger.info("Пропущен просроченный callback query")
            return
        raise


async def guarded(callback: CallbackQuery) -> bool:
    if not is_admin(callback.from_user.id):
        await safe_callback_answer(callback, "Доступ запрещён", show_alert=True)
        return False
    await safe_callback_answer(callback)
    return True


async def show(callback: CallbackQuery, text: str, markup: InlineKeyboardMarkup) -> None:
    if callback.message:
        await callback.message.edit_text(text, reply_markup=markup)


def main_menu() -> InlineKeyboardMarkup:
    return kb([
        [('📊 Статистика', 'admin:stats:today'), ('📋 Логи', 'admin:logs')],
        [('💳 Подписки', 'admin:subscriptions'), ('💰 Платежи', 'admin:payments')],
        [('🎫 Промокоды', 'admin:promos'), ('🎁 Реферальная система', 'admin:referrals')],
        [('📢 Рассылки', 'admin:broadcasts'), ('⚙️ Настройки', 'admin:settings')],
        [('⬅️ Назад в меню', 'menu:back')],
    ])


@router.callback_query(F.data.in_({'admin:menu', 'admin:stats'}))
async def admin_menu(callback: CallbackQuery) -> None:
    if not await guarded(callback):
        return
    await show(callback, '🛠 АДМИН-ПАНЕЛЬ', main_menu())


@router.callback_query(F.data.startswith('admin:stats:'))
async def admin_stats(callback: CallbackQuery) -> None:
    if not await guarded(callback):
        return
    period = (callback.data or '').rsplit(':', 1)[-1]
    data = await get_admin_stats(period)
    text = (
        '📊 Статистика\n\n'
        f"👥 Всего пользователей: {data['total_users']}\n"
        f"🟢 Сейчас онлайн: {data['online_users']} (по активности за 15 мин)\n"
        f"⚪ Не в сети: {max(0, data['total_users'] - data['online_users'])}\n\n"
        f"💳 Активных подписок: {data['active_subscriptions']}\n"
        f"⏳ Истекают в ближайшие 3 дня: {data['expiring_subscriptions']}\n\n"
        f"💰 Выручка за сегодня: {money(data['revenue_today'])}\n"
        f"💰 Выручка за месяц: {money(data['revenue_month'])}\n\n"
        f"👥 Новых пользователей сегодня: {data['new_users_today']}\n"
        f"🎁 Пришло по рефералам: {data['referrals_period']}"
    )
    await show(callback, text, kb([
        [('📈 За сегодня', 'admin:stats:today'), ('📊 За неделю', 'admin:stats:week')],
        [('📅 За месяц', 'admin:stats:month')], [('⬅️ Назад', 'admin:menu')],
    ]))


@router.callback_query(F.data == 'admin:logs')
async def admin_logs(callback: CallbackQuery) -> None:
    if not await guarded(callback):
        return
    logs = await get_admin_logs()
    lines = ['📋 Логи', '']
    if not logs:
        lines.append('Событий пока нет.')
    for item in logs:
        icon = {'error': '🔴', 'warning': '🟡', 'event': '🟢'}.get(item['level'], '⚪')
        user = f" · ID {item['telegram_id']}" if item['telegram_id'] else ''
        lines.append(f"{icon} {short_dt(item['created_at'])} · {item['category']}{user}\n{item['message']}")
    await show(callback, '\n'.join(lines)[:3900], kb([
        [('🔴 Ошибки', 'admin:logs:error'), ('🟡 Предупреждения', 'admin:logs:warning')],
        [('🟢 События', 'admin:logs:event'), ('🔄 Обновить', 'admin:logs')],
        [('💳 Платежи', 'admin:logs:category:payments'), ('👤 Пользователи', 'admin:logs:category:users')],
        [('🤖 Telegram', 'admin:logs:category:telegram'), ('🗄 База данных', 'admin:logs:category:database')],
        [('⬅️ Назад', 'admin:menu')],
    ]))


@router.callback_query(F.data.startswith('admin:logs:'))
async def admin_logs_filtered(callback: CallbackQuery) -> None:
    if not await guarded(callback):
        return
    parts = (callback.data or '').split(':')
    value = parts[-1]
    is_category = len(parts) == 4 and parts[-2] == 'category'
    logs = await get_admin_logs(category=value if is_category else None, level=None if is_category else value)
    lines = [f"📋 Логи: {value}", '']
    lines.extend(f"{short_dt(x['created_at'])} · {x['category']}\n{x['message']}" for x in logs)
    await show(callback, '\n'.join(lines)[:3900] or 'Событий нет.', back('admin:logs'))


@router.callback_query(F.data == 'admin:subscriptions')
async def admin_subscriptions(callback: CallbackQuery) -> None:
    if not await guarded(callback):
        return
    data = await get_subscription_summary()
    await show(callback, f"💳 Подписки\n\n🟢 Активные: {data['active']}\n⏳ Скоро истекают: {data['expiring']}\n🔴 Истекшие: {data['expired']}", kb([
        [('👥 Все подписки', 'admin:subscriptions:all')], [('🟢 Активные', 'admin:subscriptions:active'), ('⏳ Истекают', 'admin:subscriptions:expiring')],
        [('🔴 Истекшие', 'admin:subscriptions:expired'), ('🔍 Найти пользователя', 'admin:subscriptions:search')], [('⬅️ Назад', 'admin:menu')],
    ]))


@router.callback_query(F.data.startswith('admin:subscriptions:'))
async def admin_subscription_filter(callback: CallbackQuery) -> None:
    if not await guarded(callback):
        return
    await show(callback, '💳 Подписки\n\nДетали появятся здесь после создания первой подписки. VPN-доступ не создаётся на этом этапе.', back('admin:subscriptions'))


@router.callback_query(F.data == 'admin:payments')
async def admin_payments(callback: CallbackQuery) -> None:
    if not await guarded(callback):
        return
    data = await get_admin_payments()
    s = data['summary']
    lines = [f"💰 Платежи\n\n💵 Сегодня: {money(s['today'])}\n📅 За месяц: {money(s['month'])}\n\n✅ Успешных: {s['succeeded']}\n⏳ Ожидающих: {s['pending']}\n❌ Неуспешных: {s['failed']}\n↩️ Возвратов: {s['refunded']}", '', 'Последние операции:']
    for row in data['rows']:
        lines.append(f"#{row['id']} · {money(row['amount_kopecks'])} · {row['status']} · {short_dt(row['created_at'])}")
    await show(callback, '\n'.join(lines)[:3900], kb([
        [('📅 Сегодня', 'admin:payments:today'), ('📅 Неделя', 'admin:payments:week'), ('📅 Месяц', 'admin:payments:month')],
        [('✅ Успешные', 'admin:payments:succeeded'), ('⏳ Ожидающие', 'admin:payments:pending')],
        [('❌ Неуспешные', 'admin:payments:failed'), ('↩️ Возвраты', 'admin:payments:refunded')], [('⬅️ Назад', 'admin:menu')],
    ]))


@router.callback_query(F.data.startswith('admin:payments:'))
async def admin_payment_filter(callback: CallbackQuery) -> None:
    if not await guarded(callback):
        return
    await show(callback, '💰 Фильтр платежей\n\nРеальные платежные записи появятся после интеграции банка. RollyPay не подключён.', back('admin:payments'))


@router.callback_query(F.data == 'admin:promos')
async def admin_promos(callback: CallbackQuery) -> None:
    if not await guarded(callback):
        return
    data = await get_promo_summary()
    await show(callback, f"🎫 Промокоды\n\n🟢 Активных: {data['active']}\n⏸ Приостановлено: {data['paused']}\n🔴 Истекло: {data['expired']}", kb([
        [('➕ Создать промокод', 'admin:promo:create')], [('📋 Активные', 'admin:promo:active'), ('⏸ Приостановленные', 'admin:promo:paused')],
        [('🔴 Истёкшие', 'admin:promo:expired'), ('📊 Статистика', 'admin:promo:stats')], [('⬅️ Назад', 'admin:menu')],
    ]))


@router.callback_query(F.data.startswith('admin:promo:'))
async def admin_promo_actions(callback: CallbackQuery, state: FSMContext) -> None:
    if not await guarded(callback):
        return
    action = (callback.data or '').split(':')[-1]
    if action == 'create':
        await state.set_state(PromoStates.code)
        await callback.message.answer('Введите код промокода:')
        return
    if action == 'stats':
        await show(callback, '📊 Статистика промокодов\n\nИспользования будут считаться в promo_usages после подключения промокодов к оплате.', back('admin:promos'))
        return
    await show(callback, f'🎫 Раздел: {action}\n\nПромокоды хранятся в PostgreSQL. Список появится после добавления записей.', back('admin:promos'))


@router.message(PromoStates.code)
async def promo_code(message: Message, state: FSMContext) -> None:
    if not is_admin(message.from_user.id):
        return
    await state.update_data(code=message.text.strip().upper())
    await state.set_state(PromoStates.reward_type)
    await message.answer('Тип награды: напишите «скидка» или «дни».')


@router.message(PromoStates.reward_type)
async def promo_type(message: Message, state: FSMContext) -> None:
    value = message.text.strip().lower()
    if value not in {'скидка', 'дни'}:
        await message.answer('Введите «скидка» или «дни».')
        return
    await state.update_data(reward_type='discount' if value == 'скидка' else 'free_days')
    await state.set_state(PromoStates.reward_value)
    await message.answer('Введите размер награды числом: процент или количество дней.')


@router.message(PromoStates.reward_value)
async def promo_value(message: Message, state: FSMContext) -> None:
    if not message.text.isdigit() or int(message.text) <= 0:
        await message.answer('Нужно положительное целое число.')
        return
    await state.update_data(reward_value=int(message.text))
    await state.set_state(PromoStates.total_limit)
    await message.answer('Общий лимит использований, 0 если без лимита:')


@router.message(PromoStates.total_limit)
async def promo_total(message: Message, state: FSMContext) -> None:
    if not message.text.isdigit():
        await message.answer('Введите число.')
        return
    await state.update_data(total_limit=int(message.text) or None)
    await state.set_state(PromoStates.per_user_limit)
    await message.answer('Лимит на пользователя:')


@router.message(PromoStates.per_user_limit)
async def promo_per_user(message: Message, state: FSMContext) -> None:
    if not message.text.isdigit() or int(message.text) < 1:
        await message.answer('Введите положительное число.')
        return
    await state.update_data(per_user_limit=int(message.text))
    await state.set_state(PromoStates.starts_at)
    await message.answer('Дата начала ISO 8601 или «сейчас»:')


@router.message(PromoStates.starts_at)
async def promo_start(message: Message, state: FSMContext) -> None:
    value = datetime.now(timezone.utc).isoformat() if message.text.strip().lower() == 'сейчас' else message.text.strip()
    await state.update_data(starts_at=value)
    await state.set_state(PromoStates.ends_at)
    await message.answer('Дата окончания ISO 8601 или «нет»:')


@router.message(PromoStates.ends_at)
async def promo_end(message: Message, state: FSMContext) -> None:
    await state.update_data(ends_at=None if message.text.strip().lower() == 'нет' else message.text.strip())
    await state.set_state(PromoStates.plan_code)
    await message.answer('Код тарифа из plans или «все»:')


@router.message(PromoStates.plan_code)
async def promo_plan(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    plan_code = None if message.text.strip().lower() == 'все' else message.text.strip()
    try:
        await create_promo_code(code=data['code'], reward_type=data['reward_type'], reward_value=data['reward_value'], total_limit=data['total_limit'], per_user_limit=data['per_user_limit'], starts_at=data['starts_at'], ends_at=data['ends_at'], plan_code=plan_code)
    except Exception:
        logger.exception('Не удалось создать промокод')
        await message.answer('Не удалось создать промокод. Проверьте код и даты.')
        return
    await state.clear()
    await write_admin_log('event', 'admin', f"Создан промокод {data['code']}", telegram_id=message.from_user.id)
    await message.answer('✅ Промокод создан.', reply_markup=back('admin:promos'))


@router.callback_query(F.data == 'admin:referrals')
async def admin_referrals(callback: CallbackQuery) -> None:
    if not await guarded(callback):
        return
    data = await get_referral_summary()
    await show(callback, f"🎁 Реферальная система\n\n👥 Всего приглашено: {data['total']}\n🆕 За сегодня: {data['today']}\n📅 За месяц: {data['month']}\n💰 Начислено бонусов: {money(data['bonuses'])}", kb([
        [('🏆 Топ рефералов', 'admin:referrals:top'), ('👥 Все приглашения', 'admin:referrals:all')], [('💰 Начисления', 'admin:referrals:rewards'), ('⚙️ Настройки', 'admin:referrals:settings')], [('⬅️ Назад', 'admin:menu')],
    ]))


@router.callback_query(F.data.startswith('admin:referrals:'))
async def admin_referral_actions(callback: CallbackQuery) -> None:
    if not await guarded(callback):
        return
    action = (callback.data or '').split(':')[-1]
    if action == 'top':
        rows = await get_referral_top()
        text = '🏆 Топ рефералов\n\n' + '\n'.join(f"{i}. {row['username'] or row['telegram_id']}: {row['invited']}" for i, row in enumerate(rows, 1))
    elif action == 'settings':
        text = '⚙️ Настройки рефералов\n\n💰 Бонус за приглашение: 0 ₽\n🎁 Бонус пригласившему: 0 ₽\n👤 Условие: подтверждённое действие/оплата\n\nИзменение бонусов будет включено после платёжной интеграции.'
    else:
        text = '🎁 Данные будут отображаться из referral_rewards после подтверждённого действия. Простая регистрация бонус не начисляет.'
    await show(callback, text[:3900], back('admin:referrals'))


@router.callback_query(F.data == 'admin:broadcasts')
async def admin_broadcasts(callback: CallbackQuery) -> None:
    if not await guarded(callback):
        return
    await show(callback, '📢 Рассылки', kb([[('📨 Создать рассылку', 'admin:broadcast:create')], [('📋 История рассылок', 'admin:broadcast:history'), ('📊 Статистика', 'admin:broadcast:stats')], [('⬅️ Назад', 'admin:menu')]]))


@router.callback_query(F.data == 'admin:broadcast:create')
async def broadcast_start(callback: CallbackQuery, state: FSMContext) -> None:
    if not await guarded(callback):
        return
    await state.set_state(BroadcastStates.audience)
    await callback.message.answer('Выберите группу: all, active, expiring, none или referrals')


@router.callback_query(F.data.in_({'admin:broadcast:history', 'admin:broadcast:stats'}))
async def broadcast_history(callback: CallbackQuery) -> None:
    if not await guarded(callback):
        return
    await show(callback, '📋 История рассылок\n\nИстория сохраняется в PostgreSQL и будет отображаться здесь после первой рассылки.', back('admin:broadcasts'))


@router.message(BroadcastStates.audience)
async def broadcast_audience(message: Message, state: FSMContext) -> None:
    if not is_admin(message.from_user.id) or message.text.strip() not in {'all', 'active', 'expiring', 'none', 'referrals'}:
        await message.answer('Введите: all, active, expiring, none или referrals.')
        return
    audience = message.text.strip()
    recipients = await get_broadcast_recipients(audience)
    await state.update_data(audience=audience, recipients=recipients)
    await state.set_state(BroadcastStates.message)
    await message.answer(f'Получателей: {len(recipients)}\nВведите текст рассылки:')


@router.message(BroadcastStates.message)
async def broadcast_message(message: Message, state: FSMContext) -> None:
    await state.update_data(message=message.text or '')
    data = await state.get_data()
    await state.set_state(BroadcastStates.confirm)
    await message.answer(f"📢 Предпросмотр\n\nСообщение:\n{data['message']}\n\nПолучателей: {len(data['recipients'])}", reply_markup=kb([[('✅ Отправить', 'admin:broadcast:send'), ('❌ Отмена', 'admin:broadcast:cancel')]]))


@router.callback_query(BroadcastStates.confirm, F.data == 'admin:broadcast:cancel')
async def broadcast_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    if not await guarded(callback):
        return
    await state.clear()
    await callback.message.edit_text('Рассылка отменена.', reply_markup=back('admin:broadcasts'))


@router.callback_query(BroadcastStates.confirm, F.data == 'admin:broadcast:send')
async def broadcast_send(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    if not await guarded(callback):
        return
    data = await state.get_data()
    recipients = data['recipients']
    broadcast_id = await create_broadcast(callback.from_user.id, data['audience'], data['message'], len(recipients))
    sent = failed = 0
    for index in range(0, len(recipients), 20):
        for user_id in recipients[index:index + 20]:
            try:
                await bot.send_message(user_id, data['message'])
                sent += 1
            except Exception:
                failed += 1
        if index + 20 < len(recipients):
            await asyncio.sleep(1)
    await finish_broadcast(broadcast_id, sent, failed)
    await state.clear()
    await write_admin_log('event', 'telegram', f'Рассылка завершена: {sent} отправлено, {failed} ошибок', telegram_id=callback.from_user.id)
    await callback.message.edit_text(f'✅ Рассылка завершена\n\nОтправлено: {sent}\nОшибок: {failed}', reply_markup=back('admin:broadcasts'))


@router.callback_query(F.data == 'admin:settings')
async def admin_settings(callback: CallbackQuery) -> None:
    if not await guarded(callback):
        return
    await show(callback, '⚙️ Настройки', kb([[('💳 Тарифы', 'admin:settings:plans'), ('🎁 Реферальная система', 'admin:referrals:settings')], [('🎫 Промокоды', 'admin:promos'), ('📢 Уведомления', 'admin:settings:notifications')], [('👨‍💼 Администраторы', 'admin:settings:admins'), ('📄 Документы', 'admin:settings:documents')], [('⬅️ Назад', 'admin:menu')]]))


@router.callback_query(F.data.startswith('admin:settings:'))
async def admin_settings_page(callback: CallbackQuery) -> None:
    if not await guarded(callback):
        return
    section = (callback.data or '').split(':')[-1]
    if section == 'plans':
        plans = await get_active_single_plans() + await get_active_family_plans()
        text = '💳 Тарифы из plans\n\n' + '\n'.join(f"{p['code']} · {p['duration_days']} дн. · {money(p['price_kopecks'])} · {p['type']} · {p['device_limit']} уст." for p in plans)
    elif section == 'admins':
        text = '👨‍💼 Администраторы\n\nДоступ определяется ADMIN_IDS из окружения. Изменение через UI не добавляется, чтобы не ослаблять текущую защиту.'
    else:
        text = f'⚙️ Раздел: {section}\n\nРаздел подготовлен для следующего этапа.'
    await show(callback, text[:3900], back('admin:settings'))
