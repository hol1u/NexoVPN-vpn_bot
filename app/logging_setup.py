"""Настройка логов. Значения секретов автоматически заменяются на *** ."""
import logging
import os
import sys
from urllib.parse import urlparse

# Секретными считаются DATABASE_URL и любые переменные, имя которых
# заканчивается на _TOKEN, _KEY, _SECRET или _PASSWORD.
SECRET_NAMES = {"DATABASE_URL"}
SECRET_ENDINGS = ("_TOKEN", "_KEY", "_SECRET", "_PASSWORD")


def _collect_secrets() -> list[str]:
    secrets = []
    for name, value in os.environ.items():
        value = value.strip()
        if len(value) < 8:
            continue
        upper = name.upper()
        if upper in SECRET_NAMES or upper.endswith(SECRET_ENDINGS):
            secrets.append(value)
    db_url = os.environ.get("DATABASE_URL", "").strip()
    if db_url:
        try:
            password = urlparse(db_url).password
        except ValueError:
            password = None
        if password and len(password) >= 8:
            secrets.append(password)
    return sorted(set(secrets), key=len, reverse=True)


class RedactingFormatter(logging.Formatter):
    def __init__(self, fmt: str, secrets: list[str]) -> None:
        super().__init__(fmt)
        self._secrets = secrets

    def format(self, record: logging.LogRecord) -> str:
        text = super().format(record)
        for secret in self._secrets:
            text = text.replace(secret, "***")
        return text


def setup_logging() -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        RedactingFormatter(
            "%(asctime)s %(levelname)s %(name)s: %(message)s", _collect_secrets()
        )
    )
    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)
