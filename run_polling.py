import asyncio

from src import config
from src.bot import _set_bot_commands, create_application


async def main():
    errors = config.validate()
    if errors:
        raise RuntimeError(f"Invalid configuration: {', '.join(errors)}")

    app = create_application()
    await app.initialize()
    await app.start()
    await _set_bot_commands(app)
    await app.updater.start_polling(drop_pending_updates=True)
    print("Bot is running in polling mode. Press Ctrl+C to stop.")
    print("If you see 'Conflict: terminated by other getUpdates request', stop all other bot instances.")
    await asyncio.Event().wait()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Bot stopped.")
