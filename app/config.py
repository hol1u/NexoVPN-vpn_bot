import hashlib
import hmac
import os
import re


def get_required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Required environment variable is missing: {name}")
    return value


TELEGRAM_BOT_TOKEN = get_required_env("TELEGRAM_BOT_TOKEN")
DATABASE_URL = get_required_env("DATABASE_URL")

ADMIN_IDS = [
    int(value.strip())
    for value in os.getenv("ADMIN_IDS", "").split(",")
    if value.strip()
]


def _get_base_url() -> str:
    """Публичный адрес сервиса. Пусто, если домен в Railway ещё не создан."""
    explicit = os.getenv("BASE_URL", "").strip().rstrip("/")
    if explicit:
        return explicit

    railway_domain = os.getenv("RAILWAY_PUBLIC_DOMAIN", "").strip()
    if railway_domain:
        return f"https://{railway_domain}"

    return ""


def _get_webhook_secret() -> str:
    """Секрет вебхука Telegram: из переменной или вычисляется из токена бота.

    Telegram принимает только латинские буквы, цифры, _ и -, поэтому сам токен
    (в нём есть двоеточие) использовать нельзя.
    """
    explicit = os.getenv("TELEGRAM_WEBHOOK_SECRET", "").strip()
    if explicit:
        if not re.fullmatch(r"[A-Za-z0-9_-]{16,256}", explicit):
            raise RuntimeError(
                "TELEGRAM_WEBHOOK_SECRET: допустимы только латинские буквы, цифры, "
                "_ и -, длина от 16 до 256 символов."
            )
        return explicit

    return hmac.new(
        TELEGRAM_BOT_TOKEN.encode(),
        b"telegram-webhook-secret",
        hashlib.sha256,
    ).hexdigest()


BASE_URL = _get_base_url()
TELEGRAM_WEBHOOK_SECRET = _get_webhook_secret()
