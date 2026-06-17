import re
from typing import Optional

# Default keyword triggers for group chat support, used as fallback when no
# KnowledgeBase keywords are passed in.
DEFAULT_GROUP_KEYWORDS = {
    "de": ["skypol", "hilfe", "support", "frage", "info", "leistung", "preis", "termin"],
    "el": ["skypol", "βοήθεια", "υποστήριξη", "ερώτηση", "πληροφορίες", "υπηρεσία", "τιμή", "ραντεβού"],
    "en": ["skypol", "help", "support", "question", "info", "service", "price", "appointment"],
}

# Pre-compile regexes used in hot paths
_GREEK_CHARS_RE = re.compile(r"[\u0370-\u03FF\u1F00-\u1FFF]")
_GERMAN_CHARS_RE = re.compile(r"[äöüßÄÖÜ]")

_SANITIZE_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f\u200b-\u200f\ufeff]")
_SANITIZE_NEWLINES_RE = re.compile(r"\n{4,}")
_SANITIZE_SPACES_RE = re.compile(r"[ \t]+")


def detect_language(text: str) -> str:
    """Detect language from text. Falls back to English."""
    if not text:
        return "en"

    if _GREEK_CHARS_RE.search(text):
        return "el"
    if _GERMAN_CHARS_RE.search(text):
        return "de"

    # Simple heuristic: common German words
    german_words = {
        "hallo", "danke", "bitte", "preis", "termin", "leistung", "frage",
        "guten", "tag", "wie", "was", "kostet", "ein", "eine", "für", "und",
        "oder", "mit", "bei", "von", "zu", "der", "die", "das", "ist", "sind",
        "wir", "ihr", "du", "sie", "fotoshooting", "fotografie", "videografie",
        "webseite", "social", "media", "drucksachen", "hochzeit", "taufe",
    }
    lowered = text.lower()
    if any(word in lowered.split() for word in german_words):
        return "de"

    return "en"


# Supported UI/UX languages
SUPPORTED_LANGUAGES = {"de", "el", "en"}


def get_user_language(
    user_id: int,
    text: str = "",
    language_code: str | None = None,
    db=None,
) -> str:
    """Determine the user's preferred language.

    Resolution order:
    1. Stored preference in the database.
    2. Telegram's language_code if it is one of the supported languages.
    3. Heuristic detection from the provided text.
    """
    if db is not None:
        stored = db.get_user_language(user_id)
        if stored and stored in SUPPORTED_LANGUAGES:
            return stored

    code = (language_code or "").lower().strip()
    if code in SUPPORTED_LANGUAGES:
        return code

    detected = detect_language(text)
    return detected if detected in SUPPORTED_LANGUAGES else "en"


def _compile_group_keywords(keywords: dict[str, list[str]]) -> dict[str, re.Pattern]:
    """Build one compiled word-boundary regex per language for fast matching."""
    compiled = {}
    for lang, words in keywords.items():
        if not words:
            continue
        escaped = "|".join(re.escape(word.lower()) for word in words)
        compiled[lang] = re.compile(r"\b(?:" + escaped + r")\b")
    return compiled


def should_answer_in_group(
    text: str,
    bot_username: str,
    is_mention: bool,
    group_keywords: dict[str, list[str]] | None = None,
) -> bool:
    """Determine if bot should respond to a group message.

    Keyword matching uses word boundaries to avoid triggering on unrelated
    substrings (e.g. 'preis' inside 'unpreisig'). Keywords can be loaded from
    `data/knowledge_base.yaml` via `KnowledgeBase.get_group_keywords()`.
    """
    if is_mention:
        return True

    keywords = group_keywords if group_keywords is not None else DEFAULT_GROUP_KEYWORDS
    compiled = _compile_group_keywords(keywords)
    lowered = text.lower()
    for pattern in compiled.values():
        if pattern.search(lowered):
            return True

    # Also answer if username is mentioned without @
    if bot_username.replace("@", "").lower() in lowered:
        return True

    return False


def sanitize_input(text: str) -> str:
    """Strip control characters and collapse excessive whitespace."""
    if not text:
        return ""
    # Remove zero-width and control characters except newlines/tabs
    cleaned = _SANITIZE_CONTROL_RE.sub("", text)
    # Collapse more than three consecutive newlines
    cleaned = _SANITIZE_NEWLINES_RE.sub("\n\n\n", cleaned)
    # Collapse multiple spaces/tabs to a single space
    cleaned = _SANITIZE_SPACES_RE.sub(" ", cleaned)
    return cleaned.strip()


def escape_markdown(text: str) -> str:
    """Escape MarkdownV2 reserved characters."""
    escape_chars = r"_\*\[\]\(\)~`>#+\-=|{}\.!"
    return re.sub(f"([{re.escape(escape_chars)}])", r"\\\1", text)


def escape_markdown_basic(text: str) -> str:
    """Escape characters reserved in Telegram's legacy Markdown mode.

    Use this for text content that is wrapped in *bold* or _italic_.
    """
    escape_chars = r"_\*\[\]`"
    return re.sub(f"([{re.escape(escape_chars)}])", r"\\\1", text)


def format_services_list(services: list[dict], lang: str) -> str:
    lines = []
    for service in services:
        lines.append(f"{service.get('icon', '')} *{service.get('category', '')}*")
        for item in service.get("items", []):
            lines.append(f"  • {item}")
        lines.append("")
    return "\n".join(lines)


def format_faq(faq: list[dict], lang: str) -> str:
    lines = []
    for entry in faq:
        lines.append(f"*Q:* {entry.get('question', '')}")
        lines.append(f"*A:* {entry.get('answer', '')}\n")
    return "\n".join(lines)


def format_testimonials(testimonials: list[dict]) -> str:
    lines = []
    for t in testimonials:
        lines.append(
            f"⭐ *{escape_markdown_basic(t.get('name', ''))}* – "
            f"{escape_markdown_basic(t.get('service', ''))}"
        )
        lines.append(f"_{escape_markdown_basic(t.get('text', '').strip())}_\n")
    return "\n".join(lines)


def format_social_links(company: dict) -> str:
    links = []
    if company.get("instagram"):
        links.append(f"📸 [Instagram]({company['instagram']})")
    if company.get("facebook"):
        links.append(f"👍 [Facebook]({company['facebook']})")
    if company.get("website"):
        links.append(f"🌐 [Webseite]({company['website']})")
    return "\n".join(links)


def format_about(company: dict) -> str:
    stats = company.get("stats", [])
    stats_text = "  |  ".join(escape_markdown_basic(s) for s in stats) if stats else ""
    return (
        f"*{escape_markdown_basic(company.get('name', ''))}*\n"
        f"_{escape_markdown_basic(company.get('slogan', ''))}_\n\n"
        f"{escape_markdown_basic(company.get('description', '').strip())}\n\n"
        f"{stats_text}"
    )


def format_booking(booking: dict) -> str:
    return (
        f"*{escape_markdown_basic(booking.get('title', 'Termin buchen'))}*\n\n"
        f"{escape_markdown_basic(booking.get('text', '').strip())}\n\n"
        f"🗓️ Kontaktformular: {booking.get('form_url', '')}\n"
        f"📞 Telefon: {booking.get('phone', '')}\n"
        f"📧 E-Mail: {booking.get('email', '')}"
    )


def format_location(company: dict) -> str:
    return (
        f"📍 *Standort*\n\n"
        f"{escape_markdown_basic(company.get('location', ''))}\n\n"
        f"🗺️ [Auf Google Maps anzeigen]({company.get('map_url', '')})"
    )
