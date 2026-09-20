from aiogram import Router
from aiogram.filters import CommandStart
from aiogram.types import Message

from app.db import check_db


router = Router()


@router.message(CommandStart())
async def start_handler(message: Message) -> None:
    db_ok = await check_db()

    if not db_ok:
        await message.answer(
            "Сервис временно недоступен. Попробуйте немного позже."
        )
        return

    await message.answer(
        "👋 Добро пожаловать в NexoVPN\n\n"
        "🌐 Защищённое и стабильное VPN-соединение для ваших устройств.\n\n"
        "Выберите действие ниже 👇"
    )
