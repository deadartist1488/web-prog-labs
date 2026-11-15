"""Лаба 7: индексы, EXPLAIN, правило левого префикса, оптимизация медленного запроса (задания 5, 6, 9, 10)."""
import re
from typing import Dict, List, Optional

import psycopg2

USERS_ROWS = 50_000
ORDERS_ROWS = 200_000

SETUP = f"""
DROP TABLE IF EXISTS ix_orders, ix_users CASCADE;
CREATE TABLE ix_users (
    id SERIAL PRIMARY KEY,
    name VARCHAR(50),
    email VARCHAR(100),
    age INTEGER,
    city VARCHAR(50),
    active BOOLEAN
);
INSERT INTO ix_users (name, email, age, city, active)
SELECT (ARRAY['John','Anna','Ivan','Maria','Petr','Olga','Alex','Kate'])[1 + g % 8] || '_' || (g % 500),
       'user' || g || '@example.com',
       18 + g % 60,
       (ARRAY['Moscow','Kazan','Omsk','Tula','Perm','Sochi','Ufa','Samara','Tver','Kirov'])[1 + g % 10],
       g % 10 <> 0
FROM generate_series(1, {USERS_ROWS}) AS g;
UPDATE ix_users SET name = 'John' WHERE id % 1000 = 0;
CREATE TABLE ix_orders (
    id SERIAL PRIMARY KEY,
    user_id INTEGER,
    product VARCHAR(100),
    total DECIMAL(10, 2),
    status VARCHAR(20),
    created_at TIMESTAMP
);
INSERT INTO ix_orders (user_id, product, total, status, created_at)
SELECT 1 + (g * 7919) % {USERS_ROWS}, 'product ' || g % 300, (g % 5000) / 10.0,
       (ARRAY['new','paid','completed','cancelled'])[1 + g % 4],
       TIMESTAMP '2023-01-01' + (g % 730) * INTERVAL '1 day'
FROM generate_series(1, {ORDERS_ROWS}) AS g;
ANALYZE ix_users; ANALYZE ix_orders;
"""

QUERIES = {
    "q1_email": "SELECT * FROM ix_users WHERE email = 'user4242@example.com'",
    "q2_name_age": "SELECT * FROM ix_users WHERE name = 'John' AND age = 30",
    "q3_age": "SELECT * FROM ix_users WHERE age = 30",
    "q4_active_city": "SELECT * FROM ix_users WHERE active = true AND city = 'Moscow'",
}

INDEXES = [
    "CREATE INDEX idx_users_email ON ix_users (email)",
    "CREATE INDEX idx_users_name_age ON ix_users (name, age)",
    "CREATE INDEX idx_users_active_city ON ix_users (city) WHERE active",  # частичный индекс
    "CREATE INDEX idx_users_age ON ix_users (age)",
]

PREFIX_QUERIES = {
    "name": "SELECT * FROM ix_users WHERE name = 'John'",
    "name_age": "SELECT * FROM ix_users WHERE name = 'John' AND age = 30",
    "age": "SELECT * FROM ix_users WHERE age = 30",
    "city": "SELECT * FROM ix_users WHERE city = 'Moscow'",
    "name_city": "SELECT * FROM ix_users WHERE name = 'John' AND city = 'Moscow'",
}

SLOW_QUERY = """
SELECT u.name, u.email, COUNT(o.id) AS order_count, SUM(o.total) AS total_spent
FROM ix_users u
JOIN ix_orders o ON u.id = o.user_id
WHERE o.status = 'completed' AND o.created_at > '2024-06-01'
GROUP BY u.id, u.name, u.email
ORDER BY total_spent DESC
LIMIT 20
"""

SLOW_INDEXES = [
    # фильтр по статусу и дате + INCLUDE, чтобы агрегат брал данные прямо из индекса
    "CREATE INDEX idx_orders_status_created ON ix_orders (status, created_at) INCLUDE (user_id, total)",
]

_COST = re.compile(r"cost=[\d.]+\.\.([\d.]+)")
_TIME = re.compile(r"Execution Time: ([\d.]+) ms")


def explain(cur, sql: str, analyze: bool = True) -> Dict:
    cur.execute(("EXPLAIN (ANALYZE, BUFFERS OFF) " if analyze else "EXPLAIN ") + sql)
    lines = [r[0] for r in cur.fetchall()]
    text = "\n".join(lines)
    cost = float(_COST.search(lines[0]).group(1))
    t = _TIME.search(text)
    node = lines[0].split("  (")[0].strip()
    return {"plan_top": node, "cost": cost, "time_ms": float(t.group(1)) if t else None,
            "uses_index": "Index" in text or "Bitmap" in text, "plan": text}


def plan_nodes(plan: str) -> List[str]:
    return [ln.strip().lstrip("-> ").split("  (")[0] for ln in plan.splitlines()
            if ("Scan" in ln or "Join" in ln or "Aggregate" in ln or ln.strip().startswith(("Sort ", "-> Sort ")))
            and ":" not in ln.split("  (")[0]]


def run(dsn: str, out: Optional[List[str]] = None) -> Dict:
    out = out if out is not None else []
    report: Dict = {"before": {}, "after": {}, "prefix": {}, "slow": {}}
    conn = psycopg2.connect(dsn)
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute(SETUP)

    for name, sql in QUERIES.items():
        report["before"][name] = explain(cur, sql)
    for ddl in INDEXES:
        cur.execute(ddl)
    cur.execute("ANALYZE ix_users")
    for name, sql in QUERIES.items():
        report["after"][name] = explain(cur, sql)

    # правило левого префикса: оставляем только составной индекс (name, age, city)
    for idx in ("idx_users_name_age", "idx_users_age", "idx_users_active_city", "idx_users_email"):
        cur.execute(f"DROP INDEX {idx}")
    cur.execute("CREATE INDEX idx_users_name_age_city ON ix_users (name, age, city)")
    cur.execute("ANALYZE ix_users")
    for name, sql in PREFIX_QUERIES.items():
        report["prefix"][name] = explain(cur, sql)

    report["slow"]["before"] = explain(cur, SLOW_QUERY)
    for ddl in SLOW_INDEXES:
        cur.execute(ddl)
    cur.execute("ANALYZE ix_orders")
    report["slow"]["after"] = explain(cur, SLOW_QUERY)
    cur.close()
    conn.close()
    return report
