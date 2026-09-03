import vk_bot as app
import payment_reuse_vk  # noqa: F401
import legal_vk  # noqa: F401
import legal_vk_dedupe  # noqa: F401
from financial_hardening import install_financial_hardening
from student_stats_hardening import install_student_stats_hardening
from student_account_hardening import install_student_account_hardening
from pricing_text_hardening import install_pricing_text_hardening
from tutor_archive_hardening import install_tutor_archive_hardening
from subscription_booking import install_vk_subscription_booking
from tutor_confirmation_hardening import install_vk_tutor_confirmation
from subscription_cancel_hardening import install_subscription_cancel_release
from completion_hardening import install_vk_completion_hardening
from vk_payment_hardening import install_vk_payment_hardening
from vk_email_hardening import install_vk_email_hardening
from payment_poll_hardening import install_vk_payment_poll_hardening
from access_hardening import install_vk_materials_guard
from vk_access_hardening import install_vk_reply_authorization
from support_hardening import install_vk_support_hardening
from delivery_hardening import install_delivery_hardening
from contact_delivery_hardening import install_vk_contact_delivery_hardening
from vk_restart_hardening import install_vk_restart_hardening
from runtime_hardening import install_vk_hardening

install_financial_hardening(app)
install_student_stats_hardening(app)
install_student_account_hardening(app, "vk")
install_pricing_text_hardening(app)
install_tutor_archive_hardening(app)
install_vk_subscription_booking(app)
install_vk_tutor_confirmation(app)
install_subscription_cancel_release(app)
install_vk_completion_hardening(app)
install_vk_payment_hardening(app)
install_vk_email_hardening(app)
install_vk_payment_poll_hardening(app)
install_vk_materials_guard(app)
install_vk_reply_authorization(app)
install_vk_support_hardening(app)
install_delivery_hardening(app)
install_vk_contact_delivery_hardening(app)
install_vk_restart_hardening(app)
install_vk_hardening(app)


async def main():
    return await app.main()


if __name__ == "__main__":
    app.legacy.logging.basicConfig(level=app.legacy.logging.INFO, stream=app.legacy.sys.stdout)
    app.legacy.asyncio.run(main())
