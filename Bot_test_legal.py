import Bot_test as app
import payment_reuse_telegram  # noqa: F401  # один T-Bank платёж на бронь, повторная выдача СБП-ссылки
import legal_telegram  # noqa: F401  # юридический слой оборачивает итоговый платёжный сценарий
from financial_hardening import install_financial_hardening
from student_stats_hardening import install_student_stats_hardening
from subscription_booking import install_telegram_subscription_booking
from subscription_cancel_hardening import install_subscription_cancel_release
from completion_hardening import install_telegram_completion_hardening
from access_hardening import install_telegram_materials_guard
from runtime_hardening import install_telegram_hardening

install_financial_hardening(app)
install_student_stats_hardening(app)
install_telegram_subscription_booking(app)
install_subscription_cancel_release(app)
install_telegram_completion_hardening(app)
install_telegram_materials_guard(app)
install_telegram_hardening(app)


async def main():
    return await app.main()


if __name__ == "__main__":
    app.legacy.logging.basicConfig(level=app.legacy.logging.INFO, stream=app.legacy.sys.stdout)
    app.legacy.asyncio.run(main())
