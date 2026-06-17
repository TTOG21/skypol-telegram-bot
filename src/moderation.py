import logging
import time

from src.database import Database

logger = logging.getLogger(__name__)


class FloodProtection:
    """Per-user, per-chat flood protection backed by the database."""

    def __init__(
        self,
        max_messages: int | None = None,
        window_seconds: int | None = None,
        db: Database | None = None,
    ):
        from src import config

        self.max_messages = (
            max_messages if max_messages is not None else config.FLOOD_MAX_MESSAGES
        )
        self.window_seconds = (
            window_seconds if window_seconds is not None else config.FLOOD_WINDOW_SECONDS
        )
        self._db = db or Database()

    def set_thresholds(self, max_messages: int, window_seconds: int) -> None:
        """Update the flood thresholds at runtime."""
        self.max_messages = max_messages
        self.window_seconds = window_seconds

    def is_flooding(self, user_id: int, chat_id: int | None = None) -> bool:
        """Check if a user is flooding a chat."""
        chat_id = chat_id if chat_id is not None else user_id
        count = self._db.count_recent_flood_events(user_id, chat_id, self.window_seconds)
        if count >= self.max_messages:
            return True
        self._db.record_flood_event(user_id, chat_id)
        return False


def is_admin_user(user_id: int) -> bool:
    """Check if a user is one of the configured admins.

    ADMIN_CHAT_IDs are typically the admins' private chat IDs, which equal their user IDs.
    """
    from src import config

    if not config.ADMIN_CHAT_IDS:
        return False
    try:
        user_id_int = int(user_id)
        return any(int(admin_id) == user_id_int for admin_id in config.ADMIN_CHAT_IDS)
    except (ValueError, TypeError):
        return False
