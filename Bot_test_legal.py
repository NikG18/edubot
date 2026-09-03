import Bot_test as app
import payment_reuse_telegram  # noqa: F401  # один T-Bank платёж на бронь, повторная выдача СБП-ссылки
import legal_telegram  # noqa: F401  # юридический слой оборачивает итоговый платёжный сценарий
from runtime_hardening import install_telegram_hardening

# Ставим защитный слой последним, после payment/legal monkey-patch, чтобы не менять
# уже работающий порядок обёрток и только добавить межпроцессные гарантии.
install_telegram_hardening(app)


async def main():
    return await app.main()


if __name__ == "__main__":
    app.legacy.logging.basicConfig(level=app.legacy.logging.INFO, stream=app.legacy.sys.stdout)
    app.legacy.asyncio.run(main())
