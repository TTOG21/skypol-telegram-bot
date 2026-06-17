import json
import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any


class _JsonFormatter(logging.Formatter):
    """Emit log records as single-line JSON objects."""

    def __init__(self, token: str | None = None):
        super().__init__()
        self.token = token or ""

    def _redact(self, text: str) -> str:
        return text.replace(self.token, "<REDACTED>") if self.token else text

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": self.formatTime(record),
            "level": record.levelname,
            "logger": record.name,
            "message": self._redact(record.getMessage()),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
        }
        if record.exc_info:
            payload["exception"] = self._redact(self.formatException(record.exc_info))
        return json.dumps(payload, ensure_ascii=False, default=str)


class _TextFormatter(logging.Formatter):
    """Standard text formatter with optional token redaction."""

    def __init__(self, token: str | None = None):
        super().__init__("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
        self.token = token or ""

    def _redact(self, text: str) -> str:
        return text.replace(self.token, "<REDACTED>") if self.token else text

    def format(self, record: logging.LogRecord) -> str:
        return self._redact(super().format(record))


def configure_logging(
    level: str = "INFO",
    use_json: bool = False,
    bot_token: str | None = None,
    log_file: str | None = None,
    max_bytes: int = 10 * 1024 * 1024,
    backup_count: int = 5,
):
    """Configure root logging for the application."""
    log_level = getattr(logging, level.upper(), logging.INFO)
    formatter: logging.Formatter = (
        _JsonFormatter(token=bot_token) if use_json else _TextFormatter(token=bot_token)
    )

    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    handlers[0].setFormatter(formatter)

    if log_file:
        log_path = Path(log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            log_path, maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8"
        )
        file_handler.setFormatter(formatter)
        handlers.append(file_handler)

    root = logging.getLogger()
    root.setLevel(log_level)
    root.handlers.clear()
    for handler in handlers:
        root.addHandler(handler)
