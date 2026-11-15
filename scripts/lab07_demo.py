"""Прогон всех демонстраций лабы 7 на PostgreSQL и запись отчёта в docs/outputs/lab07_demo.txt.

    python scripts/lab07_demo.py                       # поднимет локальный PostgreSQL через pgserver
    DATABASE_URL=postgresql://... python scripts/lab07_demo.py
"""
import os
import sys
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from lab07 import indexes, nplus1  # noqa: E402
from lab07 import transactions as tx  # noqa: E402


def main() -> None:
    dsn = os.getenv("DATABASE_URL")
    if not dsn:
        from scripts.local_postgres import get_dsn

        dsn = get_dsn()
    out = []
    p = out.append

    p("# Лаба 7: вывод демонстраций (PostgreSQL)\n")
    tx.setup(dsn)
    p("## Задание 1. Перевод между счетами")
    p(f"до:                {tx.balances(dsn)}")
    tx.transfer(dsn, 1, 2, Decimal("300"))
    p(f"перевод 300 1->2:  {tx.balances(dsn)}")
    for args, label in (((2, 1, Decimal("5000")), "перевод 5000 2->1"), ((1, 99, Decimal("10")), "перевод на счёт 99")):
        try:
            tx.transfer(dsn, *args)
        except (tx.InsufficientFunds, tx.AccountNotFound) as e:
            p(f"{label}: {type(e).__name__}: {e}; ROLLBACK, балансы {tx.balances(dsn)}")

    p("\n## Задания 2 и 4. Гонка за последнее место (5 потоков, 1 место)")
    for fn in (tx.register_naive, tx.register_for_update, tx.register_atomic):
        p(str(tx.race(dsn, fn, threads=5, seats=1)))
    p("с CHECK (seats >= 0), наивный вариант: " + str(tx.race(dsn, tx.register_naive, threads=5, seats=1,
                                                              table="courses")))

    p("\n## Задание 3. UNIQUE(user_id, course_id)")
    p(tx.register_once(dsn, 42, 1))
    try:
        tx.register_once(dsn, 42, 1)
    except tx.AlreadyRegistered as e:
        p(f"повтор: AlreadyRegistered: {e}")

    p("\n## Задания 5, 6. Индексы и EXPLAIN (ix_users, 50 000 строк)")
    rep = indexes.run(dsn)
    p(f"{'запрос':<16}{'план до':<28}{'cost до':>10}{'мс до':>9} | "
      f"{'план после':<38}{'cost после':>11}{'мс после':>10}")
    for name in indexes.QUERIES:
        b, a = rep["before"][name], rep["after"][name]
        p(f"{name:<16}{b['plan_top'][:27]:<28}{b['cost']:>10.1f}{b['time_ms']:>9.2f} | "
          f"{a['plan_top'][:37]:<38}{a['cost']:>11.1f}{a['time_ms']:>10.2f}")

    p("\n## Задание 9. Левый префикс, индекс (name, age, city)")
    for name, r in rep["prefix"].items():
        p(f"WHERE {name:<10} -> {r['plan_top'][:60]:<60} index={r['uses_index']} cost={r['cost']:.1f}")

    p("\n## Задание 10. Медленный отчёт по заказам (ix_orders, 200 000 строк)")
    for k in ("before", "after"):
        r = rep["slow"][k]
        p(f"{k}: cost={r['cost']:.1f} time={r['time_ms']:.2f} ms")
        p("  узлы: " + " | ".join(indexes.plan_nodes(r["plan"])))

    p("\n## Задание 7. N+1, сырой SQL (100 читателей x 5 выдач)")
    nplus1.setup(dsn)
    for name, r in nplus1.run_raw(dsn).items():
        p(f"{name:<14} queries={r['queries']:<4} orders={r['orders']:<4} time={r['time_ms']} ms")
    p("\n## Задание 8. SQLAlchemy: lazy / joinedload / subqueryload / selectinload")
    for name, r in nplus1.run_orm(dsn).items():
        p(f"{name:<30} queries={r['queries']:<4} orders={r['orders']:<4} time={r['time_ms']} ms")

    text = "\n".join(out) + "\n"
    target = ROOT / "docs" / "outputs" / "lab07_demo.txt"
    target.write_text(text, encoding="utf-8")
    sys.stdout.buffer.write(text.encode("utf-8"))


if __name__ == "__main__":
    main()
