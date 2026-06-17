import asyncio
import os
import sys
import tempfile
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ["TELEGRAM_BOT_TOKEN"] = "123456:dummy-token-for-testing-only"
os.environ["ANTHROPIC_API_KEY"] = "dummy-anthropic-key"

from src import config
from src.database import Database
from src.memory import ConversationMemory


def _fresh_db() -> Database:
    unique_path = f"/tmp/test_bot_memory_{uuid.uuid4().hex}.db"
    return Database(db_path=unique_path)


def test_memory_limits():
    db = _fresh_db()
    mem = ConversationMemory(max_history=3, db=db)
    mem.add(1, 1, "user", "Hello")
    mem.add(1, 1, "assistant", "Hi")
    assert len(mem.get(1, 1)) == 2
    mem.add(1, 1, "user", "Q1")
    mem.add(1, 1, "assistant", "A1")
    mem.add(1, 1, "user", "Q2")
    # max_history=3 means deque holds 3 items
    assert len(mem.get(1, 1)) == 3
    mem.clear(1, 1)
    assert len(mem.get(1, 1)) == 0
    print("✓ Memory limit tests passed")


def test_memory_persistence():
    db_path = f"/tmp/test_bot_memory_persist_{uuid.uuid4().hex}.db"
    original_persist = config.PERSIST_MEMORY
    try:
        config.PERSIST_MEMORY = True

        db = Database(db_path=db_path)
        mem = ConversationMemory(max_history=5, db=db)
        mem.add(1, 2, "user", "Hallo")
        mem.add(1, 2, "assistant", "Guten Tag")
        del mem

        # Create a new instance and verify it loads the persisted history
        db2 = Database(db_path=db_path)
        mem2 = ConversationMemory(max_history=5, db=db2)
        history = mem2.get(1, 2)
        assert len(history) == 2
        assert history[0]["content"] == "Hallo"
        assert history[1]["content"] == "Guten Tag"

        mem2.clear(1, 2)
        db3 = Database(db_path=db_path)
        mem3 = ConversationMemory(max_history=5, db=db3)
        assert len(mem3.get(1, 2)) == 0
    finally:
        config.PERSIST_MEMORY = original_persist
    print("✓ Memory persistence tests passed")


async def _async_add_and_wait(mem):
    mem.add(1, 2, "user", "Async")
    # Give the scheduled background save a moment to complete
    await asyncio.sleep(0.1)


def test_memory_persistence_async_save():
    db_path = f"/tmp/test_bot_memory_async_{uuid.uuid4().hex}.db"
    original_persist = config.PERSIST_MEMORY
    try:
        config.PERSIST_MEMORY = True

        db = Database(db_path=db_path)
        mem = ConversationMemory(max_history=5, db=db)
        asyncio.run(_async_add_and_wait(mem))
        del mem

        db2 = Database(db_path=db_path)
        mem2 = ConversationMemory(max_history=5, db=db2)
        history = mem2.get(1, 2)
        assert len(history) == 1
        assert history[0]["content"] == "Async"
    finally:
        config.PERSIST_MEMORY = original_persist
    print("✓ Memory persistence async save tests passed")


if __name__ == "__main__":
    test_memory_limits()
    test_memory_persistence()
    test_memory_persistence_async_save()
    print("\nMemory tests passed!")
