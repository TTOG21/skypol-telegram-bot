import asyncio
import logging
from collections import deque
from typing import Dict

from src import config
from src.database import Database

logger = logging.getLogger(__name__)


class ConversationMemory:
    """Conversation storage with optional database persistence.

    A small in-memory cache keeps recently accessed histories hot while the
    database acts as the source of truth.
    """

    def __init__(self, max_history: int = None, db: Database | None = None):
        self.max_history = max_history or config.MAX_HISTORY
        self._db = db or Database()
        self._persist = config.PERSIST_MEMORY
        self._cache: Dict[int, deque] = {}

    def _key(self, chat_id: int, user_id: int) -> int:
        return hash((chat_id, user_id))

    def _db_key(self, chat_id: int, user_id: int) -> str:
        return f"{chat_id}:{user_id}"

    def _load(self, chat_id: int, user_id: int) -> deque:
        key = self._key(chat_id, user_id)
        if key in self._cache:
            return self._cache[key]

        db_key = self._db_key(chat_id, user_id)
        items = self._db.get_memory(db_key) if self._persist else []
        d = deque(maxlen=self.max_history)
        d.extend(items)
        self._cache[key] = d
        return d

    def _schedule_save(self, chat_id: int, user_id: int):
        if not self._persist:
            return
        try:
            loop = asyncio.get_running_loop()
            if loop.is_running():
                asyncio.create_task(self._save_async(chat_id, user_id))
                return
        except RuntimeError:
            pass
        self._save_sync(chat_id, user_id)

    def _save_sync(self, chat_id: int, user_id: int):
        key = self._key(chat_id, user_id)
        items = list(self._cache.get(key, deque()))
        self._db.set_memory(self._db_key(chat_id, user_id), chat_id, user_id, items)

    async def _save_async(self, chat_id: int, user_id: int):
        await asyncio.to_thread(self._save_sync, chat_id, user_id)

    def add(self, chat_id: int, user_id: int, role: str, content: str):
        key = self._key(chat_id, user_id)
        if key not in self._cache:
            self._cache[key] = deque(maxlen=self.max_history)
        self._cache[key].append({"role": role, "content": content})
        if self._persist:
            self._schedule_save(chat_id, user_id)

    def get(self, chat_id: int, user_id: int) -> list[dict]:
        return list(self._load(chat_id, user_id))

    def clear(self, chat_id: int, user_id: int):
        key = self._key(chat_id, user_id)
        self._cache.pop(key, None)
        if self._persist:
            self._db.delete_memory(self._db_key(chat_id, user_id))

    def reset_all(self):
        self._cache.clear()
        if self._persist:
            self._db.clear_all_memory()
