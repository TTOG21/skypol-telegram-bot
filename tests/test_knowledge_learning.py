import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ["TELEGRAM_BOT_TOKEN"] = "123456:dummy-token-for-testing-only"
os.environ["ANTHROPIC_API_KEY"] = "dummy-anthropic-key"
os.environ["ADMIN_CHAT_ID"] = "12345"

from src.database import Database
from src.knowledge import KnowledgeBase


def _fresh_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    db = Database(path)
    return db, path


def test_database_learned_faq_roundtrip():
    db, path = _fresh_db()
    try:
        learned_id = db.add_learned_faq(
            "Was kostet ein Marsflug?", "Das bieten wir nicht an.", source_gap_id=7
        )
        assert learned_id > 0

        normalized = db._normalize_text("Was kostet ein Marsflug?")
        entry = db.get_learned_faq_by_normalized(normalized)
        assert entry is not None
        assert entry["answer"] == "Das bieten wir nicht an."
        assert entry["source_gap_id"] == 7

        db.increment_learned_faq_use(entry["id"])
        entry = db.get_learned_faq_by_normalized(normalized)
        assert entry["use_count"] == 1

        learned = db.list_learned_faq()
        assert len(learned) == 1
        assert learned[0]["question"] == "Was kostet ein Marsflug?"

        assert db.delete_learned_faq(entry["id"]) is True
        assert db.list_learned_faq() == []
        print("✓ Learned FAQ database roundtrip works")
    finally:
        db._local.close()
        os.unlink(path)


def test_recurring_unanswered_questions():
    db, path = _fresh_db()
    try:
        db.add_unanswered_question(1, 1, "Wie buche ich einen Termin?", "de")
        db.add_unanswered_question(2, 2, "Wie buche ich einen Termin?", "de")
        db.add_unanswered_question(3, 3, "Was ist Skypol?", "de")

        recurring = db.get_recurring_unanswered_questions(min_count=2, limit=10)
        assert len(recurring) == 1
        assert recurring[0]["count"] == 2
        assert "buche" in recurring[0]["sample_question"].lower()
        print("✓ Recurring unanswered questions are grouped")
    finally:
        db._local.close()
        os.unlink(path)


def test_learned_faq_exact_match():
    db, path = _fresh_db()
    try:
        kb = KnowledgeBase(
            Path(__file__).resolve().parent.parent / "data" / "knowledge_base.yaml",
            db=db,
        )
        kb.add_learned_faq("Wie lange dauert ein Shooting?", "Ca. 1–2 Stunden.")
        assert kb.find_exact_faq_answer("Wie lange dauert ein Shooting?") == "Ca. 1–2 Stunden."
        print("✓ Learned FAQ exact match works")
    finally:
        db._local.close()
        os.unlink(path)


def test_learned_faq_in_relevant_context():
    db, path = _fresh_db()
    try:
        kb = KnowledgeBase(
            Path(__file__).resolve().parent.parent / "data" / "knowledge_base.yaml",
            db=db,
        )
        kb.add_learned_faq("Bietet ihr Drohnenaufnahmen an?", "Ja, als Add-on für Videoprojekte.")
        context = kb.find_relevant_context("Drohnenaufnahmen")
        assert context is not None
        assert "Drohnenaufnahmen" in context
        print("✓ Learned FAQ appears in relevant context")
    finally:
        db._local.close()
        os.unlink(path)


def test_learned_faqs_loaded_at_init():
    db, path = _fresh_db()
    try:
        db.add_learned_faq("Gibt es Geschenkgutscheine?", "Ja, auf Anfrage.")
        kb = KnowledgeBase(
            Path(__file__).resolve().parent.parent / "data" / "knowledge_base.yaml",
            db=db,
        )
        assert kb.find_exact_faq_answer("Gibt es Geschenkgutscheine?") == "Ja, auf Anfrage."
        print("✓ Learned FAQs are loaded at KnowledgeBase init")
    finally:
        db._local.close()
        os.unlink(path)


if __name__ == "__main__":
    test_database_learned_faq_roundtrip()
    test_recurring_unanswered_questions()
    test_learned_faq_exact_match()
    test_learned_faq_in_relevant_context()
    test_learned_faqs_loaded_at_init()
