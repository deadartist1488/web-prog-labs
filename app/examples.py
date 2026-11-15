"""Функции из практических заданий, которые покрываются тестами (лабы 5, 9)."""
from dataclasses import dataclass
from decimal import Decimal


def validate_email(email: str) -> bool:
    """Занятие 6, задание 1 (исходная функция из задания)."""
    if "@" not in email:
        return False
    parts = email.split("@")
    if len(parts) != 2:
        return False
    local, domain = parts
    if not local or not domain:
        return False
    if "." not in domain:
        return False
    return True


def calculate_discount(price: float, discount_percent: float) -> float:
    """Занятие 6, задание 6. Был баг: возвращалась сумма скидки (price * d / 100), а не цена со скидкой."""
    if not 0 <= discount_percent <= 100:
        raise ValueError("скидка должна быть от 0 до 100%")
    return round(price - price * discount_percent / 100, 2)


def divide(a: float, b: float) -> float:
    """Занятие 6, задание 7."""
    if b == 0:
        raise ValueError("Division by zero")
    return a / b


@dataclass
class Product:
    """Занятие 12, задание 5: регрессия расчёта итоговой цены."""

    price: Decimal
    discount: Decimal  # проценты

    def calculate_final_price(self) -> Decimal:
        # баг был: return self.price * self.discount / 100  -> для (100, 10) возвращал 10
        return (self.price - self.price * self.discount / Decimal(100)).quantize(Decimal("0.01"))


def late_fee(days_late: int, per_day: Decimal = Decimal("10.00"), cap: Decimal = Decimal("300.00")) -> Decimal:
    """Пеня за просрочку возврата книги. Регрессия: раньше пеня начислялась и за день возврата (days_late=0)."""
    if days_late <= 0:
        return Decimal("0.00")
    return min(per_day * days_late, cap)
