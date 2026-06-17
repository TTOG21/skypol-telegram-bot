import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.utils import (
    DEFAULT_GROUP_KEYWORDS,
    SUPPORTED_LANGUAGES,
    detect_language,
    escape_markdown_basic,
    format_about,
    format_booking,
    format_location,
    format_social_links,
    format_testimonials,
    get_user_language,
    sanitize_input,
    should_answer_in_group,
)
from src.knowledge import KnowledgeBase
from src.knowledge import KnowledgeBase
from src.config import KNOWLEDGE_BASE_PATH, _parse_admin_chat_id, _parse_admin_chat_ids
from src.database import Database
from src.memory import ConversationMemory


def test_language_detection():
    assert detect_language("Hallo, wie geht es dir?") == "de"
    assert detect_language("Γειά σου, πώς είσαι;") == "el"
    assert detect_language("Hello, how are you?") == "en"
    assert detect_language("Was kostet ein Fotoshooting?") == "de"
    assert detect_language("") == "en"
    print("✓ Language detection tests passed")


def test_get_user_language():
    # Text detection
    assert get_user_language(1, "Hallo") == "de"
    assert get_user_language(1, "Hello") == "en"
    assert get_user_language(1, "Γειά") == "el"
    # Telegram language code
    assert get_user_language(1, "Hello", language_code="de") == "de"
    assert get_user_language(1, "Hello", language_code="EL") == "el"
    # Unsupported language code falls back to text detection
    assert get_user_language(1, "Hallo", language_code="fr") == "de"
    # DB preference wins
    db = Database(db_path=f"/tmp/test_utils_lang_{__import__('uuid').uuid4().hex}.db")
    db.set_user_language(42, "el")
    assert get_user_language(42, "Hallo", language_code="de", db=db) == "el"
    print("✓ get_user_language tests passed")


def test_group_trigger():
    assert should_answer_in_group("@skypolbot Hilfe", "@skypolbot", True) is True
    assert should_answer_in_group("Ich brauche Support", "@skypolbot", False) is True
    assert should_answer_in_group("βοήθεια", "@skypolbot", False) is True
    assert should_answer_in_group("Random chat message", "@skypolbot", False) is False
    print("✓ Group trigger tests passed")


def test_group_trigger_word_boundaries():
    # Should still trigger on whole words
    assert should_answer_in_group("Was kostet der preis?", "@skypolbot", False) is True
    assert should_answer_in_group("Ich habe eine frage.", "@skypolbot", False) is True
    assert should_answer_in_group("help me please", "@skypolbot", False) is True
    # Should NOT trigger on substrings (false positives)
    assert should_answer_in_group("Das ist wirklich unpreisig.", "@skypolbot", False) is False
    assert should_answer_in_group("fragewürdiges Angebot", "@skypolbot", False) is False
    assert should_answer_in_group("unhelper", "@skypolbot", False) is False
    print("✓ Group trigger word-boundary tests passed")


def test_group_trigger_with_custom_keywords():
    custom = {"de": ["shooting"], "en": ["book"]}
    assert should_answer_in_group("Ich will ein Shooting buchen", "@skypolbot", False, custom) is True
    assert should_answer_in_group("Random text", "@skypolbot", False, custom) is False
    print("✓ Group trigger custom keywords tests passed")


def test_sanitize_input():
    assert sanitize_input("Hello\x00world") == "Helloworld"
    assert sanitize_input("Too   many    spaces") == "Too many spaces"
    assert sanitize_input("A\n\n\n\nB") == "A\n\n\nB"
    assert sanitize_input("  \t trim me \n ") == "trim me"
    print("✓ Input sanitization tests passed")


def test_knowledge_base():
    kb = KnowledgeBase(KNOWLEDGE_BASE_PATH)
    company = kb.get_company()
    assert company["name"] == "Skypol Arts & Media"
    assert len(kb.get_services()) == 6
    assert len(kb.get_faq()) >= 4
    assert len(kb.get_testimonials()) >= 1
    assert kb.get_booking().get("form_url")
    assert company.get("map_url")
    context = kb.to_prompt_context()
    assert "Skypol Arts & Media" in context
    assert "Fotografie & Videografie" in context
    print("✓ Knowledge base tests passed")


def test_knowledge_base_faq_exact_match():
    kb = KnowledgeBase(KNOWLEDGE_BASE_PATH)
    # Find an actual FAQ entry to test normalization
    faq = kb.get_faq()
    assert len(faq) > 0
    question = faq[0]["question"]
    answer = faq[0]["answer"]
    assert kb.find_exact_faq_answer(question) == answer
    # Punctuation-insensitive and case-insensitive
    assert kb.find_exact_faq_answer(question.lower() + "?") == answer
    assert kb.find_exact_faq_answer("xyz-not-a-question") is None
    print("✓ Knowledge base FAQ exact match tests passed")


def test_memory():
    db = Database(db_path=f"/tmp/test_bot_utils_memory_{__import__('uuid').uuid4().hex}.db")
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
    print("✓ Memory tests passed")


def test_admin_chat_id_parsing():
    assert _parse_admin_chat_id("123456789") == 123456789
    assert _parse_admin_chat_id("-1001234567890") == -1001234567890
    assert _parse_admin_chat_id('"123456789"') == 123456789
    assert _parse_admin_chat_id("'123456789'") == 123456789
    assert _parse_admin_chat_id(" 123456789 ") == 123456789
    assert _parse_admin_chat_id("@adminchannel") == "@adminchannel"
    assert _parse_admin_chat_id("") is None
    assert _parse_admin_chat_id("   ") is None

    # Multiple comma-separated IDs
    assert _parse_admin_chat_ids("123,456") == [123, 456]
    assert _parse_admin_chat_ids('"123", "456"') == [123, 456]
    assert _parse_admin_chat_ids("123, @channel") == [123, "@channel"]
    assert _parse_admin_chat_ids("") == []
    assert _parse_admin_chat_ids("   ") == []
    print("✓ Admin chat ID parsing tests passed")


def test_escape_markdown_basic():
    assert escape_markdown_basic("_italic_") == "\\_italic\\_"
    assert escape_markdown_basic("*bold*") == "\\*bold\\*"
    assert escape_markdown_basic("`code`") == "\\`code\\`"
    assert escape_markdown_basic("[link]") == "\\[link\\]"
    assert escape_markdown_basic("plain text") == "plain text"
    print("✓ Markdown escaping tests passed")


def test_format_helpers():
    company = {
        "name": "Skypol",
        "slogan": "Test Slogan",
        "description": "Beschreibungstext",
        "stats": ["500+ Projekte", "99+ Fotos"],
        "instagram": "https://instagram.com/skypol",
        "website": "https://skypol.de",
        "location": "Musterstraße 1",
        "map_url": "https://maps.google.com/?q=Musterstraße+1",
    }
    about = format_about(company)
    assert "Skypol" in about
    assert "500+ Projekte" in about

    social = format_social_links(company)
    assert "instagram.com/skypol" in social
    assert "skypol.de" in social

    location = format_location(company)
    assert "Musterstraße 1" in location
    assert "Google Maps" in location

    booking = format_booking({
        "title": "Termin buchen",
        "text": "Buche jetzt.",
        "form_url": "https://formular.de",
        "phone": "+49 123",
        "email": "test@example.com",
    })
    assert "Buche jetzt" in booking
    assert "formular.de" in booking

    testimonials = format_testimonials([
        {"name": "Max", "service": "Foto", "text": "Super!"},
    ])
    assert "Max" in testimonials
    assert "Super!" in testimonials
    print("✓ Format helper tests passed")


if __name__ == "__main__":
    test_language_detection()
    test_get_user_language()
    test_group_trigger()
    test_group_trigger_word_boundaries()
    test_group_trigger_with_custom_keywords()
    test_sanitize_input()
    test_knowledge_base()
    test_knowledge_base_faq_exact_match()
    test_memory()
    test_admin_chat_id_parsing()
    test_escape_markdown_basic()
    test_format_helpers()
    print("\nAll tests passed!")
