-- Структура базы данных проекта.
-- Файл выполняется при каждом запуске бота. Это безопасно:
-- IF NOT EXISTS не трогает уже созданные таблицы и данные.

-- Пользователи Telegram.
-- telegram_id — уникальный идентификатор пользователя в Telegram (BIGINT: значения больше 2 млрд).
CREATE TABLE IF NOT EXISTS users (
    id              BIGSERIAL PRIMARY KEY,
    telegram_id     BIGINT NOT NULL UNIQUE,
    username        TEXT,
    first_name      TEXT,
    is_blocked      BOOLEAN NOT NULL DEFAULT FALSE,
    balance_kopecks BIGINT NOT NULL DEFAULT 0,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Если таблица users уже была создана раньше,
-- добавляем колонку баланса без потери существующих пользователей.
ALTER TABLE users
ADD COLUMN IF NOT EXISTS balance_kopecks BIGINT NOT NULL DEFAULT 0;
