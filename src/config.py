import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env", override=True)

DATA_DIR = BASE_DIR / "data"
KNOWLEDGE_BASE_PATH = DATA_DIR / "knowledge_base.yaml"
DATABASE_PATH = os.getenv("DATABASE_PATH", str(BASE_DIR / ".bot_data.db"))

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")

# LLM configuration – supports Anthropic or any OpenAI-compatible API (e.g. Kimi/Moonshot)
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "openai").lower().strip()
LLM_API_KEY = os.getenv("LLM_API_KEY", "")
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "")
LLM_MODEL = os.getenv("LLM_MODEL", "kimi-k2-0711-preview")
LLM_TIMEOUT = int(os.getenv("LLM_TIMEOUT", "30"))
LLM_MAX_RETRIES = int(os.getenv("LLM_MAX_RETRIES", "1"))
LLM_CACHE_TTL_SECONDS = int(os.getenv("LLM_CACHE_TTL_SECONDS", "300"))
LLM_CACHE_MAX_ENTRIES = int(os.getenv("LLM_CACHE_MAX_ENTRIES", "1000"))

# Backwards compatibility with older env names
if not LLM_API_KEY:
    LLM_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
if not LLM_BASE_URL:
    LLM_BASE_URL = os.getenv("ANTHROPIC_BASE_URL", "")
if LLM_MODEL == "kimi-k2-0711-preview":
    LLM_MODEL = os.getenv("ANTHROPIC_MODEL", LLM_MODEL)

RENDER_EXTERNAL_URL = os.getenv("RENDER_EXTERNAL_URL", "")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")
WEBHOOK_RATE_LIMIT_RPS = float(os.getenv("WEBHOOK_RATE_LIMIT_RPS", "10"))
WEBHOOK_RATE_LIMIT_WINDOW = int(os.getenv("WEBHOOK_RATE_LIMIT_WINDOW", "1"))


def _parse_admin_chat_id(value: str | None) -> int | str | None:
    """Bereinigt eine einzelne ADMIN_CHAT_ID aus .env und wandelt numerische IDs in int um."""
    if not value:
        return None
    value = value.strip()
    # Häufiger Kopierfehler: Werte in Anführungszeichen
    if (value.startswith('"') and value.endswith('"')) or (
        value.startswith("'") and value.endswith("'")
    ):
        value = value[1:-1].strip()
    if not value:
        return None
    try:
        return int(value)
    except ValueError:
        # Kanal-Usernames wie @channelname sind als String erlaubt
        return value


def _parse_admin_chat_ids(value: str | None) -> list[int | str]:
    """Bereinigt ADMIN_CHAT_ID aus .env, unterstützt komma-getrennte IDs."""
    if not value:
        return []
    ids = []
    for part in value.split(","):
        parsed = _parse_admin_chat_id(part)
        if parsed is not None:
            ids.append(parsed)
    return ids


# Supports a single ID or multiple comma-separated IDs (e.g. 123,456 or @channelname)
ADMIN_CHAT_ID = _parse_admin_chat_ids(os.getenv("ADMIN_CHAT_ID", ""))
# Alias for code that already uses the plural form
ADMIN_CHAT_IDS = ADMIN_CHAT_ID
MAX_HISTORY = int(os.getenv("MAX_HISTORY", "10"))
FLOOD_MAX_MESSAGES = int(os.getenv("FLOOD_MAX_MESSAGES", "5"))
FLOOD_WINDOW_SECONDS = int(os.getenv("FLOOD_WINDOW_SECONDS", "10"))
FLOOD_MUTE_SECONDS = int(os.getenv("FLOOD_MUTE_SECONDS", "60"))

PERSIST_MEMORY = os.getenv("PERSIST_MEMORY", "false").lower() in ("true", "1", "yes")
MEMORY_FILE_PATH = os.getenv("MEMORY_FILE_PATH", str(BASE_DIR / ".memory.json"))

# Logging configuration
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
LOG_FORMAT_JSON = os.getenv("LOG_FORMAT", "text").lower() == "json"
LOG_FILE = os.getenv("LOG_FILE", "")
LOG_FILE_MAX_BYTES = int(os.getenv("LOG_FILE_MAX_BYTES", "10485760"))
LOG_FILE_BACKUPS = int(os.getenv("LOG_FILE_BACKUPS", "5"))


def validate() -> list[str]:
    errors = []
    if not TELEGRAM_BOT_TOKEN:
        errors.append("TELEGRAM_BOT_TOKEN is missing")
    if not LLM_API_KEY:
        errors.append("LLM_API_KEY is missing")
    if LLM_PROVIDER not in ("anthropic", "openai"):
        errors.append("LLM_PROVIDER must be 'anthropic' or 'openai'")
    return errors
