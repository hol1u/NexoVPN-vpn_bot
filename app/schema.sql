-- ============================================================
-- NexoVPN — структура базы данных
-- ============================================================
-- Этот файл выполняется при каждом запуске бота.
-- Все операции сделаны через IF NOT EXISTS / безопасные ALTER,
-- поэтому существующие данные не удаляются.
-- ============================================================


-- ============================================================
-- USERS
-- ============================================================

CREATE TABLE IF NOT EXISTS users (
    id              BIGSERIAL PRIMARY KEY,
    telegram_id     BIGINT NOT NULL UNIQUE,
    username        TEXT,
    first_name      TEXT,

    is_blocked      BOOLEAN NOT NULL DEFAULT FALSE,

    balance_kopecks BIGINT NOT NULL DEFAULT 0,
    promo_activated BOOLEAN NOT NULL DEFAULT FALSE,
    promo_used BOOLEAN NOT NULL DEFAULT FALSE,

    -- Реферальная система
    referral_code   TEXT,
    referred_by     BIGINT,
    referred_at     TIMESTAMPTZ,

    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);


-- Добавляем поля в существующую таблицу users,
-- если база была создана старой версией схемы.

ALTER TABLE users
ADD COLUMN IF NOT EXISTS balance_kopecks
BIGINT NOT NULL DEFAULT 0;

ALTER TABLE users
ADD COLUMN IF NOT EXISTS promo_activated
BOOLEAN NOT NULL DEFAULT FALSE;

ALTER TABLE users
ADD COLUMN IF NOT EXISTS promo_used
BOOLEAN NOT NULL DEFAULT FALSE;

ALTER TABLE users
ADD COLUMN IF NOT EXISTS referral_code
TEXT;

ALTER TABLE users
ADD COLUMN IF NOT EXISTS referred_by
BIGINT;

ALTER TABLE users
ADD COLUMN IF NOT EXISTS referred_at
TIMESTAMPTZ;


-- Индекс для поиска реферального кода.

CREATE UNIQUE INDEX IF NOT EXISTS users_referral_code_unique
ON users (referral_code)
WHERE referral_code IS NOT NULL;


-- Индекс для подсчёта приглашённых пользователей.

CREATE INDEX IF NOT EXISTS users_referred_by_idx
ON users (referred_by);


-- ============================================================
-- ВОССТАНОВЛЕНИЕ REFERRAL CODE ДЛЯ СТАРЫХ ПОЛЬЗОВАТЕЛЕЙ
-- ============================================================
--
-- Пользователи, которые зарегистрировались до появления
-- реферальной системы, могли иметь referral_code = NULL.
--
-- Для них создаём стабильный уникальный код на основе
-- Telegram ID.
--
-- Новые пользователи получают случайный код в db.py.
--

UPDATE users
SET referral_code =
    'nexo_' ||
    SUBSTRING(
        MD5(telegram_id::TEXT)
        FROM 1 FOR 10
    )
WHERE referral_code IS NULL;


-- ============================================================
-- PLANS
-- ============================================================

CREATE TABLE IF NOT EXISTS plans (
    id             BIGSERIAL PRIMARY KEY,

    code           TEXT NOT NULL UNIQUE,

    type           TEXT NOT NULL
                   CHECK (type IN ('single', 'family')),

    duration_days  INTEGER NOT NULL
                   CHECK (duration_days > 0),

    price_kopecks  BIGINT NOT NULL
                   CHECK (price_kopecks > 0),

    device_limit   INTEGER NOT NULL
                   CHECK (device_limit > 0),

    is_active      BOOLEAN NOT NULL DEFAULT TRUE,

    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);


-- ============================================================
-- ТАРИФЫ NexoVPN
-- ============================================================

INSERT INTO plans (
    code,
    type,
    duration_days,
    price_kopecks,
    device_limit
)
VALUES

    -- Обычная подписка: 1 устройство

    (
        'single_1m',
        'single',
        30,
        16900,
        1
    ),

    (
        'single_3m',
        'single',
        90,
        42900,
        1
    ),

    (
        'single_6m',
        'single',
        180,
        74900,
        1
    ),

    (
        'single_12m',
        'single',
        365,
        109900,
        1
    ),


    -- Тарифы на 3 устройства

    (
        'single_3dev_1m',
        'single',
        30,
        8900,
        3
    ),

    (
        'single_3dev_3m',
        'single',
        90,
        19900,
        3
    ),

    (
        'single_3dev_6m',
        'single',
        180,
        42900,
        3
    ),

    (
        'single_3dev_12m',
        'single',
        365,
        71900,
        3
    ),


    -- Family: до 6 устройств

    (
        'family_1m',
        'family',
        30,
        36900,
        6
    ),

    (
        'family_3m',
        'family',
        90,
        94900,
        6
    ),

    (
        'family_6m',
        'family',
        180,
        164900,
        6
    ),

    (
        'family_12m',
        'family',
        365,
        259000,
        6
    )

ON CONFLICT (code) DO NOTHING;

-- Обновляем семейные тарифы, созданные предыдущей версией схемы.

UPDATE plans
SET device_limit = 6
WHERE type = 'family'
    AND device_limit = 5;
