"""Единые бизнес-правила для пакетов занятий и скидок."""

SUBSCRIPTION_PACKAGES = (
    (4, 0),
    (8, 3),
    (12, 5),
    (24, 10),
)

SUBSCRIPTION_DISCOUNTS = dict(SUBSCRIPTION_PACKAGES)
FAMILY_DISCOUNT_PERCENT = 10


def get_subscription_discount(lessons_count: int) -> int:
    """Возвращает скидку пакета или отклоняет неизвестное число занятий."""
    try:
        return SUBSCRIPTION_DISCOUNTS[int(lessons_count)]
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("unsupported subscription package") from exc
