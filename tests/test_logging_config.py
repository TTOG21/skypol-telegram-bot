import json
import logging

from src.logging_config import configure_logging


class _FakeUrl:
    def __init__(self, value: str):
        self._value = value

    def __str__(self):
        return self._value


def _last_json_line(captured_out: str) -> dict:
    lines = [line for line in captured_out.strip().splitlines() if line.strip()]
    return json.loads(lines[-1])


def test_text_logging_format(capsys):
    configure_logging(level="INFO", use_json=False)
    logger = logging.getLogger("test_text")
    logger.info("hello text")
    captured = capsys.readouterr()
    assert "hello text" in captured.out
    assert "test_text" in captured.out


def test_json_logging_format(capsys):
    configure_logging(level="INFO", use_json=True)
    logger = logging.getLogger("test_json")
    logger.info("hello json")
    captured = capsys.readouterr()
    record = _last_json_line(captured.out)
    assert record["message"] == "hello json"
    assert record["logger"] == "test_json"
    assert record["level"] == "INFO"


def test_configure_logging_level():
    configure_logging(level="WARNING", use_json=False)
    logger = logging.getLogger("test_level")
    assert logger.isEnabledFor(logging.WARNING)
    assert not logger.isEnabledFor(logging.INFO)


def test_token_redaction_text(capsys):
    token = "1234567890:ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    configure_logging(level="INFO", use_json=False, bot_token=token)
    logger = logging.getLogger("test_redact_text")
    logger.info("https://api.telegram.org/bot%s/getMe", token)
    captured = capsys.readouterr()
    assert token not in captured.out
    assert "<REDACTED>" in captured.out


def test_token_redaction_json(capsys):
    token = "1234567890:ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    configure_logging(level="INFO", use_json=True, bot_token=token)
    logger = logging.getLogger("test_redact_json")
    logger.info("https://api.telegram.org/bot%s/getMe", token)
    captured = capsys.readouterr()
    record = _last_json_line(captured.out)
    assert token not in captured.out
    assert token not in json.dumps(record)
    assert "<REDACTED>" in record["message"]


def test_token_redaction_with_non_string_args(capsys):
    token = "1234567890:ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    configure_logging(level="INFO", use_json=True, bot_token=token)
    logger = logging.getLogger("test_redact_url_obj")
    url = _FakeUrl(f"https://api.telegram.org/bot{token}/getMe")
    logger.info('HTTP Request: %s %s "%s"', "POST", url, "HTTP/1.1 200 OK")
    captured = capsys.readouterr()
    record = _last_json_line(captured.out)
    assert token not in json.dumps(record)
    assert "bot<REDACTED>/getMe" in record["message"]
