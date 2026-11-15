-- 002: каталог книг
CREATE TABLE IF NOT EXISTS books (
    id               SERIAL PRIMARY KEY,
    title            VARCHAR(200)   NOT NULL,
    author           VARCHAR(100)   NOT NULL,
    isbn             VARCHAR(13)    NOT NULL UNIQUE,
    year             INTEGER        NOT NULL,
    genre            VARCHAR(50),
    price            NUMERIC(10, 2) NOT NULL DEFAULT 0,
    copies_total     INTEGER        NOT NULL DEFAULT 1,
    copies_available INTEGER        NOT NULL DEFAULT 1,
    created_at       TIMESTAMPTZ    DEFAULT NOW(),
    CONSTRAINT ck_books_copies_nonneg   CHECK (copies_available >= 0),
    CONSTRAINT ck_books_copies_le_total CHECK (copies_available <= copies_total)
);
CREATE INDEX IF NOT EXISTS idx_books_author ON books (author);
