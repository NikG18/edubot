import Bot_test as app
import payment_reuse_telegram  # noqa: F401  # один T-Bank платёж на бронь, повторная выдача СБП-ссылки
import legal_telegram  # noqa: F401  # юридический слой оборачивает итоговый платёжный сценарий
from financial_hardening import install_financial_hardening
from subscription_booking import install_telegram_subscription_booking
from runtime_hardening import install_telegram_hardening

# Сначала ставим бизнес-правила, затем абонементный слой поверх уже зарегистрированных
# handlers, и последним — межпроцессные runtime-guards.
install_financial_hardening(app)
install_telegram_subscription_booking(app)
install_telegram_hardening(app)


async def main():
    return await app.main()


if __name__ == "__main__":
    app.legacy.logging.basicConfig(level=app.legacy.logging.INFO, stream=app.legacy.sys.stdout)
    app.legacy.asyncio.run(main())
