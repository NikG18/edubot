"""Единые бизнес-правила для пакетов занятий и скидок."""

from decimal import Decimal, ROUND_HALF_UP

SUBSCRIPTION_PACKAGES = (
    (12, 5),
    (24, 10),
    (36, 15),
)

SUBSCRIPTION_DISCOUNTS = dict(SUBSCRIPTION_PACKAGES)
FAMILY_DISCOUNT_PERCENT = 10


def get_subscription_discount(lessons_count: int) -> int:
    """Возвращает скидку пакета или отклоняет неизвестное число занятий."""
    try:
        return SUBSCRIPTION_DISCOUNTS[int(lessons_count)]
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("unsupported subscription package") from exc


def subscription_total_rub(unit_price_rub, lessons_count: int) -> Decimal:
    """Точная стоимость поддерживаемого пакета в рублях с округлением до копейки."""
    count = int(lessons_count)
    discount = get_subscription_discount(count)
    price = Decimal(str(unit_price_rub))
    if price <= 0:
        raise ValueError("unit price must be positive")
    total = price * count * Decimal(100 - discount) / Decimal(100)
    return total.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def subscription_total_kop(unit_price_rub, lessons_count: int) -> int:
    """Точная стоимость поддерживаемого пакета в копейках."""
    return int(subscription_total_rub(unit_price_rub, lessons_count) * 100)
