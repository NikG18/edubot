import vk_bot as app
import legal_vk  # noqa: F401  # подключает юридический слой после vk_bot


async def main():
    return await app.main()


if __name__ == "__main__":
    app.legacy.logging.basicConfig(level=app.legacy.logging.INFO, stream=app.legacy.sys.stdout)
    app.legacy.asyncio.run(main())
