import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ["TELEGRAM_BOT_TOKEN"] = "123456:dummy-token-for-testing-only"
os.environ["ANTHROPIC_API_KEY"] = "dummy-anthropic-key"

import pytest
from fastapi.testclient import TestClient
from src import config, main
from unittest.mock import AsyncMock, MagicMock


# Replace the real Telegram application with a lightweight mock so tests do not
# call Telegram servers and do not conflict with the event loop.
_mock_bot = MagicMock()
_mock_bot.set_webhook = AsyncMock(return_value=True)
_mock_bot.username = "skypolbot"

_mock_telegram_app = MagicMock()
_mock_telegram_app.bot = _mock_bot
_mock_telegram_app.initialize = AsyncMock()
_mock_telegram_app.start = AsyncMock()
_mock_telegram_app.stop = AsyncMock()
_mock_telegram_app.shutdown = AsyncMock()
_mock_telegram_app.process_update = AsyncMock()

main.telegram_app = _mock_telegram_app

# Ensure predictable secret state for tests
_original_secret = config.WEBHOOK_SECRET


@pytest.fixture(scope="module")
def client():
    """Module-scoped TestClient to avoid event-loop conflicts."""
    with TestClient(main.app) as c:
        yield c


def test_health_endpoint(client):
    response = client.get("/")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    print("✓ Root health endpoint works")


def test_health_detail_endpoint(client):
    # Simulate a successful startup self-test so health reports "ok".
    main._health_status.update(
        telegram_ok=True, llm_ok=True, bot_username="skypolbot", errors=[]
    )
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert "config_valid" in data
    assert "webhook_configured" in data
    assert "provider" in data
    assert "database_ok" in data
    assert data["telegram_ok"] is True
    assert data["llm_ok"] is True
    print("✓ Detailed health endpoint works")


def test_webhook_wrong_secret(client):
    config.WEBHOOK_SECRET = "expected-secret"
    try:
        response = client.post(
            "/webhook",
            headers={"X-Telegram-Bot-Api-Secret-Token": "wrong-secret"},
            json={"update_id": 1},
        )
        assert response.status_code == 403
    finally:
        config.WEBHOOK_SECRET = _original_secret
    print("✓ Webhook wrong secret rejected")


def test_webhook_invalid_json(client):
    response = client.post("/webhook", content="not-json")
    assert response.status_code == 200
    print("✓ Webhook invalid JSON handled")


def test_webhook_missing_update_id(client):
    response = client.post("/webhook", json={"message": {"text": "hello"}})
    assert response.status_code == 200
    print("✓ Webhook missing update_id handled")


def test_webhook_malformed_update(client):
    response = client.post(
        "/webhook",
        json={"update_id": 1, "message": "not-a-dict"},
    )
    # Update.de_json may raise; endpoint catches it and returns 200
    assert response.status_code == 200
    print("✓ Webhook malformed update handled")


def test_webhook_payload_too_large(client):
    response = client.post(
        "/webhook",
        json={"update_id": 1, "data": "x" * (2 * 1024 * 1024)},
    )
    assert response.status_code == 413
    print("✓ Webhook oversized payload rejected")


def test_metrics_endpoint(client):
    response = client.get("/metrics")
    assert response.status_code == 200
    assert response.headers["content-type"] == "text/plain; charset=utf-8"
    body = response.text
    assert "bot_uptime_seconds" in body
    assert "bot_messages_total" in body
    assert "bot_tickets_open" in body
    assert "bot_users_total" in body
    print("✓ Metrics endpoint returns Prometheus-style text")


if __name__ == "__main__":
    with TestClient(main.app) as client:
        test_health_endpoint(client)
        test_health_detail_endpoint(client)
        test_webhook_wrong_secret(client)
        test_webhook_invalid_json(client)
        test_webhook_missing_update_id(client)
        test_webhook_malformed_update(client)
        test_webhook_payload_too_large(client)
        test_metrics_endpoint(client)
    print("\nWebhook tests passed!")
