import Bot_test as app
import legal_telegram  # noqa: F401  # подключает юридический слой после Bot_test


async def main():
    return await app.main()


if __name__ == "__main__":
    app.legacy.logging.basicConfig(level=app.legacy.logging.INFO, stream=app.legacy.sys.stdout)
    app.legacy.asyncio.run(main())
