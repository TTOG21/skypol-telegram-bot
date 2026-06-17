import os
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ["TELEGRAM_BOT_TOKEN"] = "123456:dummy-token-for-testing-only"
os.environ["ANTHROPIC_API_KEY"] = "dummy-anthropic-key"
os.environ["ADMIN_CHAT_ID"] = "12345"

from src import config
from src.analytics import Analytics
from src.database import Database
from src.moderation import FloodProtection, is_admin_user
from src.tickets import TicketSystem, extract_ticket_id

# Ensure predictable admin ID for tests (overrides real .env)
config.ADMIN_CHAT_ID = [12345]
config.ADMIN_CHAT_IDS = [12345]


def _fresh_db() -> Database:
    """Return a Database backed by a temporary file for test isolation."""
    unique_path = f"/tmp/test_bot_{uuid.uuid4().hex}.db"
    return Database(db_path=unique_path)


def test_analytics():
    db = _fresh_db()
    analytics = Analytics(db=db)
    analytics.track_message(1, "de")
    analytics.track_message(2, "en")
    analytics.track_message(1, "de")
    analytics.track_command("start")
    analytics.track_command("help")
    analytics.track_support_ticket()
    analytics.track_flood_event()

    stats = analytics.get_stats()
    assert stats["messages_total"] == 3
    assert stats["messages_by_language"]["de"] == 2
    assert stats["messages_by_language"]["en"] == 1
    assert stats["commands_used"]["start"] == 1
    assert stats["commands_used"]["help"] == 1
    assert stats["support_tickets_created"] == 1
    assert stats["flood_events"] == 1
    assert stats["active_users_count"] == 2
    assert stats["uptime_seconds"] >= 0
    print("✓ Analytics tests passed")


def test_tickets():
    db = _fresh_db()
    ts = TicketSystem(db=db)
    ticket_id = ts.create(100, 200)
    assert ticket_id == 1

    ticket = ts.get(ticket_id)
    assert ticket["user_id"] == 100
    assert ticket["chat_id"] == 200
    assert ticket["status"] == "open"

    open_ticket = ts.get_open_by_user(100)
    assert open_ticket is not None
    assert open_ticket["id"] == ticket_id

    ts.add_message(ticket_id, "user", "Hilfe bitte")
    ts.add_message(ticket_id, "admin", "Gerne!")
    assert len(ts.get(ticket_id)["messages"]) == 2

    assert ts.close(ticket_id) is True
    assert ts.get(ticket_id)["status"] == "closed"
    assert ts.close(ticket_id) is False
    assert ts.get_open_by_user(100) is None
    print("✓ Ticket system tests passed")


def test_ticket_language():
    db = _fresh_db()
    ts = TicketSystem(db=db)
    ticket_id = ts.create(100, 200, language="el")
    ticket = ts.get(ticket_id)
    assert ticket["language"] == "el"
    print("✓ Ticket language tests passed")


def test_user_preferences():
    db = _fresh_db()
    assert db.get_user_language(123) is None
    db.set_user_language(123, "de")
    assert db.get_user_language(123) == "de"
    db.set_user_language(123, "en")
    assert db.get_user_language(123) == "en"
    print("✓ User preferences tests passed")


def test_group_tickets():
    db = _fresh_db()
    ts = TicketSystem(db=db)
    # Same user can have an open ticket in a private chat and a group chat
    private_id = ts.create(100, 200)
    time.sleep(0.02)
    group_id = ts.create(100, -300)
    assert private_id != group_id

    assert ts.get_open_by_user_and_chat(100, 200)["id"] == private_id
    assert ts.get_open_by_user_and_chat(100, -300)["id"] == group_id

    # get_open_by_user returns the most recent open ticket
    assert ts.get_open_by_user(100)["id"] == group_id

    ts.close(private_id)
    assert ts.get_open_by_user_and_chat(100, 200) is None
    assert ts.get_open_by_user_and_chat(100, -300)["id"] == group_id
    print("✓ Group ticket tests passed")


def test_flood_protection():
    db = _fresh_db()
    fp = FloodProtection(max_messages=3, window_seconds=1, db=db)
    user_id = 42
    chat_id = 100
    assert fp.is_flooding(user_id, chat_id) is False
    assert fp.is_flooding(user_id, chat_id) is False
    assert fp.is_flooding(user_id, chat_id) is False
    assert fp.is_flooding(user_id, chat_id) is True

    # Same user in a different chat should not be flagged yet
    other_chat_id = 200
    assert fp.is_flooding(user_id, other_chat_id) is False

    time.sleep(1.1)
    assert fp.is_flooding(user_id, chat_id) is False
    print("✓ Flood protection tests passed")


def test_flood_protection_uses_config_defaults():
    db = _fresh_db()
    fp = FloodProtection(db=db)
    assert fp.max_messages == config.FLOOD_MAX_MESSAGES
    assert fp.window_seconds == config.FLOOD_WINDOW_SECONDS
    print("✓ Flood protection config defaults tests passed")


def test_is_admin_user():
    assert is_admin_user(12345) is True
    assert is_admin_user(99999) is False
    # Also verify string comparison works
    assert is_admin_user("12345") is True
    print("✓ Admin user check tests passed")


def test_multiple_admins():
    original = config.ADMIN_CHAT_ID
    config.ADMIN_CHAT_ID = [111, 222, "@channel"]
    config.ADMIN_CHAT_IDS = config.ADMIN_CHAT_ID
    try:
        assert is_admin_user(111) is True
        assert is_admin_user(222) is True
        assert is_admin_user(333) is False
        assert is_admin_user("111") is True
    finally:
        config.ADMIN_CHAT_ID = original
        config.ADMIN_CHAT_IDS = original
    print("✓ Multiple admin tests passed")


def test_extract_ticket_id():
    assert extract_ticket_id("💬 Ticket #1 – Nachricht") == 1
    assert extract_ticket_id("🆘 Ticket #42\nUser: ...") == 42
    assert extract_ticket_id("No ticket here") is None
    print("✓ Ticket ID extraction tests passed")


if __name__ == "__main__":
    test_analytics()
    test_tickets()
    test_ticket_language()
    test_user_preferences()
    test_group_tickets()
    test_flood_protection()
    test_flood_protection_uses_config_defaults()
    test_is_admin_user()
    test_multiple_admins()
    test_extract_ticket_id()
    print("\nEnhancement tests passed!")
