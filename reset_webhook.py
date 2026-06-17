"""
Reset Telegram webhook and pending updates.
Run this if you get 'Conflict: terminated by other getUpdates request'.
"""
import asyncio
import os

import httpx
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")

if not TOKEN:
    print("Error: TELEGRAM_BOT_TOKEN not found in .env")
    exit(1)

BASE_URL = f"https://api.telegram.org/bot{TOKEN}"


async def main():
    async with httpx.AsyncClient() as client:
        # Get current webhook info
        print("Checking current webhook info...")
        r = await client.get(f"{BASE_URL}/getWebhookInfo")
        print(r.json())

        # Delete webhook and drop pending updates
        print("\nDeleting webhook and dropping pending updates...")
        r = await client.post(f"{BASE_URL}/deleteWebhook", json={"drop_pending_updates": True})
        print(r.json())

        print("\nDone. You can now run the bot with polling.")


if __name__ == "__main__":
    asyncio.run(main())
