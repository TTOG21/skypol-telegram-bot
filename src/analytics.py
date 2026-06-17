import time

from src.database import Database


class Analytics:
    """Database-backed analytics tracker."""

    def __init__(self, db: Database | None = None):
        self._db = db or Database()
        self.started_at = time.time()

    def track_message(self, user_id: int, lang: str):
        self._db.track_event("message", user_id=user_id, language=lang)

    def track_command(self, command: str, user_id: int | None = None):
        self._db.track_event(
            "command",
            user_id=user_id,
            metadata={"command": command},
        )

    def track_support_ticket(self):
        self._db.track_event("support_ticket")

    def track_flood_event(self):
        self._db.track_event("flood_event")

    def get_stats(self) -> dict:
        since_start = self.started_at
        uptime_seconds = int(time.time() - self.started_at)
        return {
            "uptime_seconds": uptime_seconds,
            "messages_total": self._db.count_events("message", since=since_start),
            "messages_by_language": self._db.get_language_distribution(since=since_start),
            "commands_used": self._db.get_command_counts(since=since_start),
            "support_tickets_created": self._db.count_events("support_ticket", since=since_start),
            "flood_events": self._db.count_events("flood_event", since=since_start),
            "active_users_count": self._db.get_active_users(since=since_start),
        }
