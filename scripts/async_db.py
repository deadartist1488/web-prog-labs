"""Лаба 6 (занятие 8, задание 9): асинхронный CRUD через asyncpg с пулом соединений."""
import asyncio
import sys
from typing import Any, Dict, Optional

import asyncpg

SCHEMA = """
CREATE TABLE IF NOT EXISTS async_users (
    id SERIAL PRIMARY KEY,
    name VARCHAR(50) NOT NULL,
    email VARCHAR(100) UNIQUE NOT NULL,
    age INTEGER CHECK (age >= 18)
)
"""


class AsyncUserRepo:
    def __init__(self, pool: "asyncpg.Pool"):
        self.pool = pool

    async def create(self, name: str, email: str, age: int) -> Dict[str, Any]:
        try:
            row = await self.pool.fetchrow(
                "INSERT INTO async_users (name, email, age) VALUES ($1, $2, $3) RETURNING *", name, email, age)
        except asyncpg.UniqueViolationError:
            raise ValueError(f"email {email} уже занят") from None
        return dict(row)

    async def get(self, user_id: int) -> Optional[Dict[str, Any]]:
        row = await self.pool.fetchrow("SELECT * FROM async_users WHERE id = $1", user_id)
        return dict(row) if row else None

    async def update_age(self, user_id: int, age: int) -> int:
        status = await self.pool.execute("UPDATE async_users SET age = $1 WHERE id = $2", age, user_id)
        return int(status.split()[-1])  # "UPDATE 1" -> 1

    async def delete(self, user_id: int) -> int:
        status = await self.pool.execute("DELETE FROM async_users WHERE id = $1", user_id)
        return int(status.split()[-1])


async def run_demo(dsn: str) -> Dict[str, Any]:
    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=5)
    try:
        await pool.execute(SCHEMA)
        await pool.execute("TRUNCATE async_users RESTART IDENTITY")
        repo = AsyncUserRepo(pool)
        created = await repo.create("Async User", "async@example.com", 40)
        try:
            await repo.create("Dup", "async@example.com", 30)
            duplicate = "no error"
        except ValueError as e:
            duplicate = str(e)
        # 20 параллельных чтений через пул из 5 соединений
        fetched = await asyncio.gather(*(repo.get(created["id"]) for _ in range(20)))
        await repo.update_age(created["id"], 41)
        updated = await repo.get(created["id"])
        deleted = await repo.delete(created["id"])
        missing = await repo.get(created["id"])
        return {"created": created, "duplicate": duplicate, "parallel_reads": len(fetched),
                "updated_age": updated["age"], "deleted": deleted, "after_delete": missing}
    finally:
        await pool.close()


if __name__ == "__main__":
    print(asyncio.run(run_demo(sys.argv[1])))
