import json
import logging
import sqlite3
from pathlib import Path
from typing import Any

from src import config

logger = logging.getLogger(__name__)


class Database:
    """Lightweight SQLite persistence layer using the stdlib sqlite3 driver.

    All write operations are executed via ``asyncio.to_thread`` so that the
    event loop is not blocked by disk I/O.
    """

    def __init__(self, db_path: str | Path | None = None):
        self.db_path = Path(db_path) if db_path else Path(config.DATABASE_PATH)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._local = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._local.row_factory = sqlite3.Row
        self._init_tables()

    def _init_tables(self):
        """Create tables if they do not exist yet."""
        with self._local:
            self._local.executescript(
                """
                CREATE TABLE IF NOT EXISTS tickets (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    chat_id INTEGER NOT NULL,
                    status TEXT NOT NULL DEFAULT 'open',
                    language TEXT,
                    created_at REAL NOT NULL,
                    closed_at REAL,
                    messages TEXT NOT NULL DEFAULT '[]'
                );

                CREATE INDEX IF NOT EXISTS idx_tickets_user_chat
                    ON tickets(user_id, chat_id, status);
                CREATE INDEX IF NOT EXISTS idx_tickets_status
                    ON tickets(status);

                CREATE TABLE IF NOT EXISTS memory (
                    key TEXT PRIMARY KEY,
                    chat_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    messages TEXT NOT NULL DEFAULT '[]',
                    updated_at REAL NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_memory_user
                    ON memory(user_id);

                CREATE TABLE IF NOT EXISTS analytics (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_type TEXT NOT NULL,
                    user_id INTEGER,
                    language TEXT,
                    timestamp REAL NOT NULL,
                    metadata TEXT NOT NULL DEFAULT '{}'
                );

                CREATE INDEX IF NOT EXISTS idx_analytics_type_time
                    ON analytics(event_type, timestamp);

                CREATE TABLE IF NOT EXISTS flood_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    chat_id INTEGER NOT NULL,
                    timestamp REAL NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_flood_events_user_chat_time
                    ON flood_events(user_id, chat_id, timestamp);

                CREATE TABLE IF NOT EXISTS user_preferences (
                    user_id INTEGER PRIMARY KEY,
                    language TEXT,
                    updated_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    chat_id INTEGER NOT NULL,
                    username TEXT,
                    language TEXT,
                    first_seen REAL NOT NULL,
                    last_seen REAL NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_users_last_seen
                    ON users(last_seen);

                CREATE TABLE IF NOT EXISTS blocked_users (
                    user_id INTEGER PRIMARY KEY,
                    reason TEXT,
                    blocked_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS feedback (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    chat_id INTEGER NOT NULL,
                    ticket_id INTEGER,
                    rating INTEGER,
                    comment TEXT,
                    created_at REAL NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_feedback_user
                    ON feedback(user_id);
                CREATE INDEX IF NOT EXISTS idx_feedback_created
                    ON feedback(created_at);

                CREATE TABLE IF NOT EXISTS unanswered_questions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    chat_id INTEGER NOT NULL,
                    question TEXT NOT NULL,
                    language TEXT,
                    created_at REAL NOT NULL,
                    reviewed INTEGER NOT NULL DEFAULT 0
                );

                CREATE INDEX IF NOT EXISTS idx_unanswered_reviewed
                    ON unanswered_questions(reviewed, created_at);

                CREATE TABLE IF NOT EXISTS learned_faq (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    question TEXT NOT NULL,
                    normalized_question TEXT NOT NULL UNIQUE,
                    answer TEXT NOT NULL,
                    source_gap_id INTEGER,
                    use_count INTEGER NOT NULL DEFAULT 0,
                    avg_rating REAL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_learned_faq_normalized
                    ON learned_faq(normalized_question);
                """
            )

    def _execute(self, sql: str, parameters: tuple | dict = ()) -> sqlite3.Cursor:
        with self._local:
            return self._local.execute(sql, parameters)

    def _executemany(self, sql: str, parameters: list[tuple | dict]) -> sqlite3.Cursor:
        with self._local:
            return self._local.executemany(sql, parameters)

    def _fetchone(self, sql: str, parameters: tuple | dict = ()) -> sqlite3.Row | None:
        return self._execute(sql, parameters).fetchone()

    def _fetchall(self, sql: str, parameters: tuple | dict = ()) -> list[sqlite3.Row]:
        return self._execute(sql, parameters).fetchall()

    # --- Tickets ---

    def create_ticket(self, user_id: int, chat_id: int, language: str = "") -> int:
        import time

        cursor = self._execute(
            "INSERT INTO tickets (user_id, chat_id, status, language, created_at, messages) "
            "VALUES (?, ?, 'open', ?, ?, '[]')",
            (user_id, chat_id, language, time.time()),
        )
        return cursor.lastrowid

    def get_ticket(self, ticket_id: int) -> dict | None:
        row = self._fetchone("SELECT * FROM tickets WHERE id = ?", (ticket_id,))
        return self._row_to_ticket(row) if row else None

    def get_open_ticket_by_user_and_chat(
        self, user_id: int, chat_id: int
    ) -> dict | None:
        row = self._fetchone(
            "SELECT * FROM tickets WHERE user_id = ? AND chat_id = ? AND status = 'open' "
            "ORDER BY created_at DESC LIMIT 1",
            (user_id, chat_id),
        )
        return self._row_to_ticket(row) if row else None

    def get_open_ticket_by_user(self, user_id: int) -> dict | None:
        row = self._fetchone(
            "SELECT * FROM tickets WHERE user_id = ? AND status = 'open' "
            "ORDER BY created_at DESC LIMIT 1",
            (user_id,),
        )
        return self._row_to_ticket(row) if row else None

    def list_open_tickets(self) -> list[dict]:
        rows = self._fetchall(
            "SELECT * FROM tickets WHERE status = 'open' ORDER BY created_at DESC"
        )
        return [self._row_to_ticket(row) for row in rows]

    def list_open_tickets_older_than(self, age_seconds: int) -> list[dict]:
        import time

        cutoff = time.time() - age_seconds
        rows = self._fetchall(
            "SELECT * FROM tickets WHERE status = 'open' AND created_at < ? ORDER BY created_at ASC",
            (cutoff,),
        )
        return [self._row_to_ticket(row) for row in rows]

    def list_tickets(
        self, status: str | None = None, limit: int = 10, offset: int = 0
    ) -> tuple[list[dict], int]:
        params: list[Any] = []
        where = ""
        if status:
            where = "WHERE status = ?"
            params.append(status)
        count_row = self._fetchone(f"SELECT COUNT(*) FROM tickets {where}", tuple(params))
        total = count_row[0] if count_row else 0
        rows = self._fetchall(
            f"SELECT * FROM tickets {where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
            tuple(params + [limit, offset]),
        )
        return [self._row_to_ticket(row) for row in rows], total

    def add_ticket_message(self, ticket_id: int, role: str, text: str):
        import time

        ticket = self.get_ticket(ticket_id)
        if not ticket:
            return
        messages = ticket["messages"]
        messages.append({"role": role, "text": text, "at": time.time()})
        self._execute(
            "UPDATE tickets SET messages = ? WHERE id = ?",
            (json.dumps(messages, ensure_ascii=False), ticket_id),
        )

    def close_ticket(self, ticket_id: int) -> bool:
        import time

        ticket = self.get_ticket(ticket_id)
        if not ticket or ticket["status"] != "open":
            return False
        self._execute(
            "UPDATE tickets SET status = 'closed', closed_at = ? WHERE id = ?",
            (time.time(), ticket_id),
        )
        return True

    def _row_to_ticket(self, row: sqlite3.Row) -> dict:
        return {
            "id": row["id"],
            "user_id": row["user_id"],
            "chat_id": row["chat_id"],
            "status": row["status"],
            "language": row["language"],
            "created_at": row["created_at"],
            "closed_at": row["closed_at"],
            "messages": json.loads(row["messages"] or "[]"),
        }

    # --- User preferences ---

    def get_user_language(self, user_id: int) -> str | None:
        row = self._fetchone(
            "SELECT language FROM user_preferences WHERE user_id = ?", (user_id,)
        )
        return row["language"] if row else None

    def set_user_language(self, user_id: int, language: str):
        import time

        self._execute(
            "INSERT INTO user_preferences (user_id, language, updated_at) "
            "VALUES (?, ?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET language = excluded.language, updated_at = excluded.updated_at",
            (user_id, language, time.time()),
        )

    # --- Users ---

    def upsert_user(
        self, user_id: int, chat_id: int, username: str | None, language: str = ""
    ):
        import time

        now = time.time()
        safe_username = str(username) if username is not None else ""
        self._execute(
            "INSERT INTO users (user_id, chat_id, username, language, first_seen, last_seen) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET "
            "chat_id = excluded.chat_id, "
            "username = excluded.username, "
            "language = excluded.language, "
            "last_seen = excluded.last_seen",
            (user_id, chat_id, safe_username, language, now, now),
        )

    def get_user(self, user_id: int) -> dict | None:
        row = self._fetchone("SELECT * FROM users WHERE user_id = ?", (user_id,))
        if not row:
            return None
        return {
            "user_id": row["user_id"],
            "chat_id": row["chat_id"],
            "username": row["username"],
            "language": row["language"],
            "first_seen": row["first_seen"],
            "last_seen": row["last_seen"],
        }

    def list_users(self) -> list[dict]:
        rows = self._fetchall("SELECT * FROM users ORDER BY last_seen DESC")
        return [
            {
                "user_id": r["user_id"],
                "chat_id": r["chat_id"],
                "username": r["username"],
                "language": r["language"],
                "first_seen": r["first_seen"],
                "last_seen": r["last_seen"],
            }
            for r in rows
        ]

    def count_users(self) -> int:
        row = self._fetchone("SELECT COUNT(*) FROM users")
        return row[0] if row else 0

    # --- Blocked users ---

    def block_user(self, user_id: int, reason: str = ""):
        import time

        self._execute(
            "INSERT INTO blocked_users (user_id, reason, blocked_at) "
            "VALUES (?, ?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET reason = excluded.reason, blocked_at = excluded.blocked_at",
            (user_id, reason, time.time()),
        )

    def unblock_user(self, user_id: int) -> bool:
        cursor = self._execute("DELETE FROM blocked_users WHERE user_id = ?", (user_id,))
        return cursor.rowcount > 0

    def is_blocked(self, user_id: int) -> bool:
        row = self._fetchone(
            "SELECT 1 FROM blocked_users WHERE user_id = ?", (user_id,)
        )
        return bool(row)

    def list_blocked_users(self) -> list[dict]:
        rows = self._fetchall("SELECT * FROM blocked_users ORDER BY blocked_at DESC")
        return [
            {
                "user_id": r["user_id"],
                "reason": r["reason"],
                "blocked_at": r["blocked_at"],
            }
            for r in rows
        ]

    # --- Memory ---

    def get_memory(self, key: str) -> list[dict]:
        row = self._fetchone("SELECT messages FROM memory WHERE key = ?", (key,))
        if not row:
            return []
        return json.loads(row["messages"] or "[]")

    def set_memory(self, key: str, chat_id: int, user_id: int, messages: list[dict]):
        import time

        self._execute(
            "INSERT INTO memory (key, chat_id, user_id, messages, updated_at) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET messages = excluded.messages, updated_at = excluded.updated_at",
            (
                key,
                chat_id,
                user_id,
                json.dumps(messages, ensure_ascii=False),
                time.time(),
            ),
        )

    def delete_memory(self, key: str):
        self._execute("DELETE FROM memory WHERE key = ?", (key,))

    def clear_all_memory(self):
        self._execute("DELETE FROM memory")

    # --- Analytics ---

    def track_event(
        self,
        event_type: str,
        user_id: int | None = None,
        language: str = "",
        metadata: dict | None = None,
    ):
        import time

        self._execute(
            "INSERT INTO analytics (event_type, user_id, language, timestamp, metadata) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                event_type,
                user_id,
                language,
                time.time(),
                json.dumps(metadata or {}, ensure_ascii=False),
            ),
        )

    def count_events(
        self,
        event_type: str | None = None,
        since: float | None = None,
        language: str | None = None,
    ) -> int:
        sql = "SELECT COUNT(*) FROM analytics WHERE 1=1"
        params: list[Any] = []
        if event_type:
            sql += " AND event_type = ?"
            params.append(event_type)
        if since:
            sql += " AND timestamp >= ?"
            params.append(since)
        if language:
            sql += " AND language = ?"
            params.append(language)
        row = self._fetchone(sql, tuple(params))
        return row[0] if row else 0

    def get_language_distribution(self, since: float | None = None) -> dict[str, int]:
        sql = "SELECT language, COUNT(*) FROM analytics WHERE event_type = 'message'"
        params: list[Any] = []
        if since:
            sql += " AND timestamp >= ?"
            params.append(since)
        sql += " GROUP BY language"
        rows = self._fetchall(sql, tuple(params))
        return {row["language"] or "unknown": row[1] for row in rows}

    def get_active_users(self, since: float | None = None) -> int:
        sql = "SELECT COUNT(DISTINCT user_id) FROM analytics WHERE event_type = 'message'"
        params: list[Any] = []
        if since:
            sql += " AND timestamp >= ?"
            params.append(since)
        row = self._fetchone(sql, tuple(params))
        return row[0] if row else 0

    def get_command_counts(self, since: float | None = None) -> dict[str, int]:
        sql = "SELECT metadata, COUNT(*) FROM analytics WHERE event_type = 'command'"
        params: list[Any] = []
        if since:
            sql += " AND timestamp >= ?"
            params.append(since)
        sql += " GROUP BY metadata"
        rows = self._fetchall(sql, tuple(params))
        result: dict[str, int] = {}
        for row in rows:
            meta = json.loads(row["metadata"] or "{}")
            command = meta.get("command", "unknown")
            result[command] = row[1]
        return result

    # --- Flood ---

    def record_flood_event(self, user_id: int, chat_id: int):
        import time

        self._execute(
            "INSERT INTO flood_events (user_id, chat_id, timestamp) VALUES (?, ?, ?)",
            (user_id, chat_id, time.time()),
        )

    def count_recent_flood_events(
        self, user_id: int, chat_id: int, window_seconds: float
    ) -> int:
        import time

        cutoff = time.time() - window_seconds
        row = self._fetchone(
            "SELECT COUNT(*) FROM flood_events WHERE user_id = ? AND chat_id = ? AND timestamp >= ?",
            (user_id, chat_id, cutoff),
        )
        return row[0] if row else 0

    def prune_old_flood_events(self, max_age_seconds: float):
        import time

        cutoff = time.time() - max_age_seconds
        self._execute("DELETE FROM flood_events WHERE timestamp < ?", (cutoff,))

    def count_total_flood_events(self) -> int:
        row = self._fetchone("SELECT COUNT(*) FROM flood_events")
        return row[0] if row else 0

    def get_ticket_summary(self) -> dict:
        open_row = self._fetchone(
            "SELECT COUNT(*) FROM tickets WHERE status = 'open'"
        )
        closed_row = self._fetchone(
            "SELECT COUNT(*) FROM tickets WHERE status = 'closed'"
        )
        return {
            "open": open_row[0] if open_row else 0,
            "closed": closed_row[0] if closed_row else 0,
        }

    # --- Feedback ---

    def add_feedback(
        self,
        user_id: int,
        chat_id: int,
        comment: str = "",
        rating: int | None = None,
        ticket_id: int | None = None,
    ) -> int:
        import time

        cursor = self._execute(
            "INSERT INTO feedback (user_id, chat_id, ticket_id, rating, comment, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (user_id, chat_id, ticket_id, rating, comment, time.time()),
        )
        return cursor.lastrowid

    def list_feedback(self, limit: int = 50) -> list[dict]:
        rows = self._fetchall(
            "SELECT id, user_id, chat_id, ticket_id, rating, comment, created_at FROM feedback "
            "ORDER BY created_at DESC LIMIT ?",
            (limit,),
        )
        return [dict(row) for row in rows]

    def get_average_rating(self) -> float | None:
        row = self._fetchone(
            "SELECT AVG(rating), COUNT(*) FROM feedback WHERE rating IS NOT NULL"
        )
        if not row or row[1] == 0:
            return None
        return round(row[0], 2)

    # --- Unanswered questions (knowledge gaps) ---

    def add_unanswered_question(
        self, user_id: int, chat_id: int, question: str, language: str = ""
    ) -> int:
        import time

        cursor = self._execute(
            "INSERT INTO unanswered_questions (user_id, chat_id, question, language, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (user_id, chat_id, question, language, time.time()),
        )
        return cursor.lastrowid

    def list_unanswered_questions(
        self, reviewed: bool | None = None, limit: int = 50
    ) -> list[dict]:
        sql = (
            "SELECT id, user_id, chat_id, question, language, created_at, reviewed "
            "FROM unanswered_questions"
        )
        params: list[Any] = []
        if reviewed is not None:
            sql += " WHERE reviewed = ?"
            params.append(1 if reviewed else 0)
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        rows = self._fetchall(sql, tuple(params))
        return [dict(row) for row in rows]

    def mark_unanswered_question_reviewed(self, question_id: int) -> bool:
        cursor = self._execute(
            "UPDATE unanswered_questions SET reviewed = 1 WHERE id = ?",
            (question_id,),
        )
        return cursor.rowcount > 0

    def count_unanswered_questions(self, reviewed: bool | None = None) -> int:
        sql = "SELECT COUNT(*) FROM unanswered_questions"
        params: list[Any] = []
        if reviewed is not None:
            sql += " WHERE reviewed = ?"
            params.append(1 if reviewed else 0)
        row = self._fetchone(sql, tuple(params))
        return row[0] if row else 0

    def get_unanswered_question(self, question_id: int) -> dict | None:
        row = self._fetchone(
            "SELECT id, user_id, chat_id, question, language, created_at, reviewed "
            "FROM unanswered_questions WHERE id = ?",
            (question_id,),
        )
        return dict(row) if row else None

    # --- Learned FAQs (self-learning loop) ---

    @staticmethod
    def _normalize_text(text: str) -> str:
        import re

        if not text:
            return ""
        return re.sub(r"[^\w\s]", "", text.lower()).strip()

    def add_learned_faq(
        self, question: str, answer: str, source_gap_id: int | None = None
    ) -> int:
        import time

        normalized = self._normalize_text(question)
        now = time.time()
        cursor = self._execute(
            "INSERT OR REPLACE INTO learned_faq "
            "(normalized_question, question, answer, source_gap_id, use_count, avg_rating, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, "
            "COALESCE((SELECT use_count FROM learned_faq WHERE normalized_question = ?), 0), "
            "COALESCE((SELECT avg_rating FROM learned_faq WHERE normalized_question = ?), NULL), "
            "COALESCE((SELECT created_at FROM learned_faq WHERE normalized_question = ?), ?), "
            "?)",
            (
                normalized,
                question,
                answer,
                source_gap_id,
                normalized,
                normalized,
                normalized,
                now,
                now,
            ),
        )
        return cursor.lastrowid

    def get_learned_faq_by_normalized(self, normalized: str) -> dict | None:
        row = self._fetchone(
            "SELECT id, question, answer, source_gap_id, use_count, avg_rating, created_at, updated_at "
            "FROM learned_faq WHERE normalized_question = ?",
            (normalized,),
        )
        return dict(row) if row else None

    def list_learned_faq(self, limit: int = 50) -> list[dict]:
        rows = self._fetchall(
            "SELECT id, question, answer, source_gap_id, use_count, avg_rating, created_at, updated_at "
            "FROM learned_faq ORDER BY use_count DESC, updated_at DESC LIMIT ?",
            (limit,),
        )
        return [dict(row) for row in rows]

    def increment_learned_faq_use(self, learned_id: int) -> bool:
        cursor = self._execute(
            "UPDATE learned_faq SET use_count = use_count + 1 WHERE id = ?",
            (learned_id,),
        )
        return cursor.rowcount > 0

    def delete_learned_faq(self, learned_id: int) -> bool:
        cursor = self._execute(
            "DELETE FROM learned_faq WHERE id = ?", (learned_id,)
        )
        return cursor.rowcount > 0

    def get_recurring_unanswered_questions(
        self, min_count: int = 2, limit: int = 20
    ) -> list[dict]:
        rows = self._fetchall(
            "SELECT id, question FROM unanswered_questions WHERE reviewed = 0"
        )
        groups: dict[str, dict] = {}
        for row in rows:
            normalized = self._normalize_text(row["question"])
            if not normalized:
                continue
            if normalized not in groups:
                groups[normalized] = {
                    "normalized": normalized,
                    "count": 0,
                    "sample_question": row["question"],
                    "ids": [],
                }
            groups[normalized]["count"] += 1
            groups[normalized]["ids"].append(row["id"])
        recurring = [g for g in groups.values() if g["count"] >= min_count]
        recurring.sort(key=lambda x: x["count"], reverse=True)
        return recurring[:limit]

    def is_unanswered_question_logged(self, normalized: str) -> bool:
        row = self._fetchone(
            "SELECT 1 FROM unanswered_questions WHERE reviewed = 0 AND "
            "LOWER(TRIM(question)) = LOWER(TRIM(?)) LIMIT 1",
            (normalized,),
        )
        return row is not None
