-- 001: пользователи (лаба 6). Применяется автоматически при первом старте postgres (docker-entrypoint-initdb.d)
CREATE TABLE IF NOT EXISTS users (
    id            SERIAL PRIMARY KEY,
    name          VARCHAR(50)  NOT NULL,
    email         VARCHAR(100) NOT NULL UNIQUE,
    age           INTEGER      NOT NULL,
    password_hash VARCHAR(100) NOT NULL,
    role          VARCHAR(20)  NOT NULL DEFAULT 'user',
    created_at    TIMESTAMPTZ  DEFAULT NOW(),
    CONSTRAINT ck_users_age CHECK (age >= 18 AND age <= 120)
);
