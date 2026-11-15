-- 003: выдачи книг и отзывы
CREATE TABLE IF NOT EXISTS loans (
    id          SERIAL PRIMARY KEY,
    user_id     INTEGER     NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    book_id     INTEGER     NOT NULL REFERENCES books (id) ON DELETE RESTRICT,
    taken_at    TIMESTAMPTZ DEFAULT NOW(),
    due_at      TIMESTAMPTZ NOT NULL,
    returned_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_loans_user_id ON loans (user_id);
CREATE INDEX IF NOT EXISTS idx_loans_book_id ON loans (book_id);
-- частичный уникальный индекс: одна активная выдача одной книги на читателя
CREATE UNIQUE INDEX IF NOT EXISTS uq_loans_active_user_book ON loans (user_id, book_id) WHERE returned_at IS NULL;

CREATE TABLE IF NOT EXISTS reviews (
    id         SERIAL PRIMARY KEY,
    user_id    INTEGER     NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    book_id    INTEGER     NOT NULL REFERENCES books (id) ON DELETE CASCADE,
    rating     INTEGER     NOT NULL,
    text       TEXT        NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    CONSTRAINT ck_reviews_rating CHECK (rating BETWEEN 1 AND 5)
);
CREATE INDEX IF NOT EXISTS idx_reviews_book_id ON reviews (book_id);
