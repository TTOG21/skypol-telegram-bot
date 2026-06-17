import json
import logging
import time
from collections import defaultdict
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, Request, Response, status
from telegram import Update

from src import config
from src.bot import _set_bot_commands, analytics, create_application, tickets
from src.database import Database
from src.knowledge import KnowledgeBase
from src.llm import create_llm_client
from src.logging_config import configure_logging

configure_logging(
    level=config.LOG_LEVEL,
    use_json=config.LOG_FORMAT_JSON,
    bot_token=config.TELEGRAM_BOT_TOKEN,
    log_file=config.LOG_FILE or None,
    max_bytes=config.LOG_FILE_MAX_BYTES,
    backup_count=config.LOG_FILE_BACKUPS,
)
logger = logging.getLogger(__name__)

# Validate configuration
errors = config.validate()
if errors:
    for error in errors:
        logger.error("Config error: %s", error)
    raise RuntimeError(f"Invalid configuration: {', '.join(errors)}")

# Create Telegram application
telegram_app = create_application()

_health_status = {
    "telegram_ok": False,
    "llm_ok": False,
    "bot_username": None,
    "errors": [],
}

MAX_UPDATE_SIZE = 1 * 1024 * 1024  # 1 MB


class SimpleRateLimiter:
    """In-memory sliding-window rate limiter per client key (e.g. IP)."""

    def __init__(self, max_requests: float, window_seconds: int):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._history = defaultdict(list)

    def is_allowed(self, key: str) -> bool:
        now = time.time()
        window_start = now - self.window_seconds
        timestamps = self._history[key]
        timestamps = [t for t in timestamps if t > window_start]
        if len(timestamps) >= self.max_requests:
            self._history[key] = timestamps
            return False
        timestamps.append(now)
        self._history[key] = timestamps
        return True


webhook_rate_limiter = SimpleRateLimiter(
    max_requests=config.WEBHOOK_RATE_LIMIT_RPS,
    window_seconds=config.WEBHOOK_RATE_LIMIT_WINDOW,
)


def _client_ip(request: Request) -> str:
    """Extract the most likely client IP from proxy headers or the request."""
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    real_ip = request.headers.get("X-Real-IP")
    if real_ip:
        return real_ip.strip()
    if request.client:
        return request.client.host
    return "unknown"


async def _run_self_tests() -> list[str]:
    """Verify Telegram token and LLM client before going live."""
    errors = []
    _health_status["telegram_ok"] = False
    _health_status["llm_ok"] = False
    _health_status["bot_username"] = None

    try:
        me = await telegram_app.bot.get_me()
        logger.info("Telegram self-test OK: @%s", me.username)
        _health_status["telegram_ok"] = True
        _health_status["bot_username"] = me.username
    except Exception as e:
        logger.error("Telegram self-test failed: %s", e)
        errors.append(f"Telegram token invalid: {e}")

    try:
        knowledge = KnowledgeBase(config.KNOWLEDGE_BASE_PATH)
        llm = create_llm_client(knowledge)
        logger.info("LLM self-test OK: provider=%s model=%s", config.LLM_PROVIDER, llm.model)
        _health_status["llm_ok"] = True
    except Exception as e:
        logger.error("LLM self-test failed: %s", e)
        errors.append(f"LLM client could not be created: {e}")

    _health_status["errors"] = errors
    return errors


def _check_database() -> bool:
    """Quick connectivity check for the SQLite database."""
    try:
        Database()._execute("SELECT 1")
        return True
    except Exception as e:
        logger.warning("Database health check failed: %s", e)
        return False


@asynccontextmanager
async def lifespan(app: FastAPI):
    await telegram_app.initialize()
    await telegram_app.start()
    await _set_bot_commands(telegram_app)

    try:
        self_test_errors = await _run_self_tests()
        if self_test_errors:
            logger.error("Startup self-test failed; skipping webhook registration")
            yield
            return

        if config.RENDER_EXTERNAL_URL:
            webhook_url = f"{config.RENDER_EXTERNAL_URL.rstrip('/')}/webhook"
            logger.info("Setting webhook to %s", webhook_url)
            await telegram_app.bot.set_webhook(
                url=webhook_url,
                secret_token=config.WEBHOOK_SECRET or None,
            )
        else:
            logger.warning("RENDER_EXTERNAL_URL not set; running without webhook")

        yield
    finally:
        await telegram_app.stop()
        await telegram_app.shutdown()


# Create FastAPI app
app = FastAPI(title="Skypol Telegram Bot", lifespan=lifespan)


@app.get("/")
async def root():
    return {"status": "ok"}


@app.get("/health")
async def health():
    """Return a readiness probe with webhook and config status."""
    db_ok = _check_database()
    overall = (
        _health_status["telegram_ok"]
        and _health_status["llm_ok"]
        and db_ok
        and not bool(config.validate())
    )
    return {
        "status": "ok" if overall else "degraded",
        "config_valid": not bool(config.validate()),
        "webhook_configured": bool(config.RENDER_EXTERNAL_URL),
        "provider": config.LLM_PROVIDER,
        "database_ok": db_ok,
        "telegram_ok": _health_status["telegram_ok"],
        "llm_ok": _health_status["llm_ok"],
        "bot_username": _health_status["bot_username"],
        "self_test_errors": _health_status["errors"],
    }


@app.get("/metrics")
async def metrics():
    """Return Prometheus-style metrics for monitoring."""
    stats = analytics.get_stats()
    ticket_summary = tickets.get_summary()
    db = Database()
    users_total = db.count_users()

    lines = [
        f"# HELP bot_uptime_seconds Bot process uptime",
        f"# TYPE bot_uptime_seconds counter",
        f"bot_uptime_seconds {stats['uptime_seconds']}",
        "",
        f"# HELP bot_messages_total Total text messages received since start",
        f"# TYPE bot_messages_total counter",
        f"bot_messages_total {stats['messages_total']}",
        "",
        f"# HELP bot_commands_total Total commands received since start",
        f"# TYPE bot_commands_total counter",
        f"bot_commands_total {sum(stats['commands_used'].values())}",
        "",
        f"# HELP bot_tickets_open Number of currently open support tickets",
        f"# TYPE bot_tickets_open gauge",
        f"bot_tickets_open {ticket_summary['open']}",
        "",
        f"# HELP bot_users_total Number of known users in the directory",
        f"# TYPE bot_users_total gauge",
        f"bot_users_total {users_total}",
        "",
        f"# HELP bot_flood_events_total Flood events since start",
        f"# TYPE bot_flood_events_total counter",
        f"bot_flood_events_total {stats.get('flood_events', 0)}",
    ]
    return Response(content="\n".join(lines), media_type="text/plain")


@app.post("/webhook")
async def webhook(request: Request):
    client_ip = _client_ip(request)
    if not webhook_rate_limiter.is_allowed(client_ip):
        logger.warning("Webhook rate limit exceeded for %s", client_ip)
        return Response(status_code=status.HTTP_429_TOO_MANY_REQUESTS)

    secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token")
    if config.WEBHOOK_SECRET and secret != config.WEBHOOK_SECRET:
        return Response(status_code=status.HTTP_403_FORBIDDEN)

    try:
        body = await request.body()
        if len(body) > MAX_UPDATE_SIZE:
            logger.warning("Webhook payload too large: %s bytes", len(body))
            return Response(status_code=status.HTTP_413_CONTENT_TOO_LARGE)
        data = json.loads(body)
    except Exception as e:
        logger.warning("Webhook payload is not valid JSON: %s", e)
        return Response(status_code=status.HTTP_200_OK)

    if not isinstance(data, dict) or "update_id" not in data:
        logger.warning("Webhook payload missing update_id: %s", data)
        return Response(status_code=status.HTTP_200_OK)

    try:
        update = Update.de_json(data, telegram_app.bot)
    except Exception as e:
        logger.warning("Could not deserialize Telegram update: %s", e)
        return Response(status_code=status.HTTP_200_OK)

    logger.info(
        "Webhook update_id=%s chat_id=%s from=%s",
        update.update_id,
        update.effective_chat.id if update.effective_chat else None,
        client_ip,
    )

    await telegram_app.process_update(update)
    return Response(status_code=status.HTTP_200_OK)


if __name__ == "__main__":
    uvicorn.run("src.main:app", host="0.0.0.0", port=8000, reload=True)
