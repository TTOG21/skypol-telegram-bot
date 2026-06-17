import re

from src.database import Database


class TicketSystem:
    """Database-backed support ticket system."""

    def __init__(self, db: Database | None = None):
        self._db = db or Database()

    def create(self, user_id: int, chat_id: int, language: str = "") -> int:
        return self._db.create_ticket(user_id, chat_id, language)

    def get(self, ticket_id: int) -> dict | None:
        return self._db.get_ticket(ticket_id)

    def get_open_by_user(self, user_id: int) -> dict | None:
        return self._db.get_open_ticket_by_user(user_id)

    def get_open_by_user_and_chat(self, user_id: int, chat_id: int) -> dict | None:
        return self._db.get_open_ticket_by_user_and_chat(user_id, chat_id)

    def add_message(self, ticket_id: int, role: str, text: str):
        self._db.add_ticket_message(ticket_id, role, text)

    def close(self, ticket_id: int) -> bool:
        return self._db.close_ticket(ticket_id)

    def list_open(self) -> list[dict]:
        return self._db.list_open_tickets()

    def list_tickets(
        self, status: str | None = None, limit: int = 10, offset: int = 0
    ) -> tuple[list[dict], int]:
        return self._db.list_tickets(status=status, limit=limit, offset=offset)

    def list_open_older_than(self, age_seconds: int) -> list[dict]:
        return self._db.list_open_tickets_older_than(age_seconds)

    def get_summary(self) -> dict:
        return self._db.get_ticket_summary()


def extract_ticket_id(text: str) -> int | None:
    """Extract a ticket ID from text like 'Ticket #123' or '💬 Ticket #123'."""
    match = re.search(r"Ticket #(\d+)", text)
    if match:
        return int(match.group(1))
    return None
