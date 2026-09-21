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

-- Тарифы.
-- code — стабильный уникальный идентификатор тарифа (на него ссылаются кнопки Telegram).
-- type — single (обычная подписка) или family (семейная).
-- price_kopecks — цена в копейках (целое число: 169 ₽ = 16900).
CREATE TABLE IF NOT EXISTS plans (
    id             BIGSERIAL PRIMARY KEY,
    code           TEXT NOT NULL UNIQUE,
    type           TEXT NOT NULL CHECK (type IN ('single', 'family')),
    duration_days  INTEGER NOT NULL CHECK (duration_days > 0),
    price_kopecks  BIGINT NOT NULL CHECK (price_kopecks > 0),
    device_limit   INTEGER NOT NULL CHECK (device_limit > 0),
    is_active      BOOLEAN NOT NULL DEFAULT TRUE,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Начальный набор тарифов. ON CONFLICT DO NOTHING: при повторном запуске
-- дубликаты не создаются, а уже изменённые цены не перезаписываются.
INSERT INTO plans (code, type, duration_days, price_kopecks, device_limit)
VALUES
    ('single_1m',  'single',  30,  16900, 1),
    ('single_3m',  'single',  90,  42900, 1),
    ('single_6m',  'single', 180,  74900, 1),
    ('single_12m', 'single', 365, 109900, 1),
    ('family_1m',  'family',  30,  36900, 5),
    ('family_3m',  'family',  90,  94900, 5),
    ('family_6m',  'family', 180, 164900, 5),
    ('family_12m', 'family', 365, 259000, 5)
ON CONFLICT (code) DO NOTHING;
