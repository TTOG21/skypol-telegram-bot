"""
Get your Telegram chat ID to use as ADMIN_CHAT_ID.
Run this, then send /start to your bot. It will print your chat ID.
"""
import asyncio
import os

from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

load_dotenv()

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
if not TOKEN:
    print("Error: TELEGRAM_BOT_TOKEN not found in .env")
    exit(1)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user = update.effective_user
    print(f"\n=== CHAT ID ===")
    print(f"Chat ID: {chat_id}")
    print(f"User: {user.full_name} (@{user.username or 'n/a'})")
    print(f"===============\n")
    await update.message.reply_text(
        f"Your chat ID is: {chat_id}\n\nAdd this to your .env as:\nADMIN_CHAT_ID={chat_id}"
    )


async def main():
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    await app.initialize()
    await app.start()
    await app.updater.start_polling()
    print("Send /start to your bot to get your chat ID. Press Ctrl+C to stop.")
    await asyncio.Event().wait()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nStopped.")
