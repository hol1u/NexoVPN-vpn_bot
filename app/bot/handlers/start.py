1
2
3
4
5
6
7
8
9
10
11
12
13
14
15
16
17
18
19
20
21
22
23
24
25
26
27
28
29
30
31
32
33
34
35
36
37
38
39
40
41
42
43
44
45
46
47
48
49
50
51
52
53
54
55
56
57
58
59
60
61
62
63
64
65
66
67
68
69
70
71
72
73
74
75
76
77
78
79
80
81
82
83
84
import logging

from aiogram import Bot, F, Router
from aiogram.filters import CommandObject, CommandStart
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.config import ADMIN_IDS, REQUIRED_CHANNEL_ID, REQUIRED_CHANNEL_URL
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
CB_CHECK_SUBSCRIPTION = "subscription:check"


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

def build_subscription_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📰 Подписаться на NexoVPN News",
                    url=REQUIRED_CHANNEL_URL,
                )
            ],
            [
                InlineKeyboardButton(
                    text="✅ Я подписался",
                    callback_data=CB_CHECK_SUBSCRIPTION,
                )
            ],
        ]
    )


def build_main_menu(
    is_admin: bool = False,
) -> InlineKeyboardMarkup:
    buttons = [
        [
            InlineKeyboardButton(
        )
