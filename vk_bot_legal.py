import vk_bot as app
import payment_reuse_vk  # noqa: F401  # один T-Bank платёж на бронь, повторная выдача СБП-ссылки
import legal_vk  # noqa: F401  # юридический слой оборачивает итоговый платёжный сценарий
import legal_vk_dedupe  # noqa: F401  # последний слой убирает повторную отправку документов
from financial_hardening import install_financial_hardening
from subscription_booking import install_vk_subscription_booking
from subscription_cancel_hardening import install_subscription_cancel_release
from completion_hardening import install_vk_completion_hardening
from vk_payment_hardening import install_vk_payment_hardening
from runtime_hardening import install_vk_hardening

# В VK остаются пользовательские/преподавательские сценарии и статистика. Факт проведения
# подтверждается только в Telegram-админке, а оплата использует тот же booking-linked T-Bank flow.
install_financial_hardening(app)
install_vk_subscription_booking(app)
install_subscription_cancel_release(app)
install_vk_completion_hardening(app)
install_vk_payment_hardening(app)
install_vk_hardening(app)


async def main():
    return await app.main()


if __name__ == "__main__":
    app.legacy.logging.basicConfig(level=app.legacy.logging.INFO, stream=app.legacy.sys.stdout)
    app.legacy.asyncio.run(main())
