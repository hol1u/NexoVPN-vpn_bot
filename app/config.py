import os


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

BASE_URL = os.getenv("BASE_URL", "").rstrip("/")

TELEGRAM_WEBHOOK_SECRET = os.getenv("TELEGRAM_WEBHOOK_SECRET")

if not TELEGRAM_WEBHOOK_SECRET:
    TELEGRAM_WEBHOOK_SECRET = TELEGRAM_BOT_TOKEN
