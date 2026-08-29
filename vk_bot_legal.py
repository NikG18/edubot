import vk_bot as app
import payment_reuse_vk  # noqa: F401  # один T-Bank платёж на бронь, повторная выдача СБП-ссылки
import legal_vk  # noqa: F401  # юридический слой оборачивает итоговый платёжный сценарий


async def main():
    return await app.main()


if __name__ == "__main__":
    app.legacy.logging.basicConfig(level=app.legacy.logging.INFO, stream=app.legacy.sys.stdout)
    app.legacy.asyncio.run(main())
