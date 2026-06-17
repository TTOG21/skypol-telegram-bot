import asyncio
import csv
import io
import logging
import re
from datetime import timedelta
from pathlib import Path

from telegram import ChatPermissions, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatAction, ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    ChatMemberHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from src import config
from src.analytics import Analytics
from src.database import Database
from src.knowledge import KnowledgeBase
from src.llm import create_llm_client
from src.logging_config import configure_logging
from src.memory import ConversationMemory
from src.moderation import FloodProtection, is_admin_user
from src.tickets import TicketSystem, extract_ticket_id
from src.utils import (
    SUPPORTED_LANGUAGES,
    detect_language,
    escape_markdown,
    escape_markdown_basic,
    format_about,
    format_booking,
    format_faq,
    format_location,
    format_services_list,
    format_social_links,
    format_testimonials,
    get_user_language,
    sanitize_input,
    should_answer_in_group,
)

configure_logging(
    level=config.LOG_LEVEL, use_json=config.LOG_FORMAT_JSON, bot_token=config.TELEGRAM_BOT_TOKEN
)
logger = logging.getLogger(__name__)

_db = Database()
knowledge = KnowledgeBase(config.KNOWLEDGE_BASE_PATH, db=_db)
memory = ConversationMemory(db=_db)
llm = create_llm_client(knowledge)
analytics = Analytics(db=_db)
tickets = TicketSystem(db=_db)
flood_protection = FloodProtection(db=_db)

# Pending ticket replies keyed by admin user_id -> ticket_id
_pending_ticket_replies: dict[int, int] = {}
_last_error_alert = 0.0
_ERROR_ALERT_COOLDOWN_SECONDS = 60


class _PendingReplyFilter(filters.MessageFilter):
    """Match text messages from admins who have a pending ticket reply."""

    def filter(self, message):
        return message.from_user.id in _pending_ticket_replies


def tracked_command(name: str):
    """Decorator to track command usage in analytics and keep user directory up to date."""
    def decorator(func):
        async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
            user = update.effective_user
            chat = update.effective_chat
            if not user or not chat:
                return await func(update, context)
            if _db.is_blocked(user.id):
                logger.info("Ignoring command /%s from blocked user %s", name, user.id)
                return
            logger.info("Command /%s received from user %s (%s)", name, user.id, user.username or "n/a")
            analytics.track_command(name, user_id=user.id)
            _db.upsert_user(user.id, chat.id, user.username, _language_for(update))
            return await func(update, context)
        return wrapper
    return decorator


def cleanup_command(delay: int = 10):
    """Delete the user's command message in groups after a short delay."""
    def decorator(func):
        async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
            result = await func(update, context)
            chat = update.effective_chat
            message = update.message
            if chat and message and chat.type in ("group", "supergroup"):
                try:
                    await asyncio.sleep(delay)
                    await context.bot.delete_message(
                        chat_id=chat.id, message_id=message.message_id
                    )
                except Exception as e:
                    logger.debug("Could not delete command message: %s", e)
            return result
        return wrapper
    return decorator


def _language_for(update: Update, text: str = "") -> str:
    """Resolve the user's preferred language for this update."""
    user = update.effective_user
    return get_user_language(
        user_id=user.id,
        text=text,
        language_code=user.language_code,
        db=_db,
    )


async def _send_typing(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send a 'typing...' chat action, ignoring errors."""
    try:
        await context.bot.send_chat_action(
            chat_id=update.effective_chat.id, action=ChatAction.TYPING
        )
    except Exception:
        pass


def _is_user_blocked(user_id: int) -> bool:
    """Check whether a user is on the blocklist."""
    return _db.is_blocked(user_id)


def _track_user(update: Update):
    """Store or update the user in the directory."""
    user = update.effective_user
    chat = update.effective_chat
    if user and chat:
        _db.upsert_user(user.id, chat.id, user.username, _language_for(update))


def _format_admin_notification(
    ticket_id: int,
    user,
    chat_id: int,
    lang: str,
    text: str,
) -> str:
    """Build a consistent admin notification text."""
    preview = text[:300].strip().replace("\n", " ")
    return (
        f"🆘 Ticket #{ticket_id}\n"
        f"👤 {user.full_name} (@{user.username or 'n/a'})\n"
        f"💬 Chat: {chat_id}\n"
        f"🌐 Language: {lang.upper()}\n\n"
        f"📝 {preview}\n\n"
        "Tippe auf *Antworten* und antworte auf diese Nachricht, "
        "oder tippe auf *Schließen*, um das Ticket zu schließen."
    )


if config.ADMIN_CHAT_ID:
    logger.info("Admin notifications enabled for configured ADMIN_CHAT_ID")
else:
    logger.warning("ADMIN_CHAT_ID is not configured; human support notifications will be skipped")


async def _notify_admins(
    context: ContextTypes.DEFAULT_TYPE,
    text: str,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> tuple[bool, list[int | str]]:
    """Send a notification message to every configured admin.

    Returns (at_least_one_success, list_of_failed_admin_ids).
    """
    if not config.ADMIN_CHAT_ID:
        return False, []
    failed = []
    success = False
    for admin_id in config.ADMIN_CHAT_ID:
        try:
            await context.bot.send_message(
                chat_id=admin_id,
                text=text,
                reply_markup=reply_markup,
                parse_mode=ParseMode.MARKDOWN,
            )
            success = True
            logger.info("Notification sent to admin %s", admin_id)
        except Exception as e:
            logger.error("Failed to notify admin %s: %s", admin_id, e)
            failed.append(admin_id)
    return success, failed


# --- Keyboard builders ---
def main_menu_keyboard(lang: str) -> InlineKeyboardMarkup:
    texts = {
        "de": [
            [("🛍️ Leistungen", "services"), ("❓ FAQ", "faq")],
            [("🖼️ Portfolio", "portfolio"), ("🏢 Über uns", "about")],
            [("🗓️ Termin buchen", "booking"), ("⭐ Bewertungen", "testimonials")],
            [("📍 Standort", "location"), ("📱 Social Media", "social")],
            [("📞 Kontakt", "contact"), ("👤 Support", "human")],
        ],
        "el": [
            [("🛍️ Υπηρεσίες", "services"), ("❓ Συχνές Ερωτήσεις", "faq")],
            [("🖼️ Portfolio", "portfolio"), ("🏢 Σχετικά με εμάς", "about")],
            [("🗓️ Κράτηση ραντεβού", "booking"), ("⭐ Κριτικές", "testimonials")],
            [("📍 Τοποθεσία", "location"), ("📱 Social Media", "social")],
            [("📞 Επικοινωνία", "contact"), ("👥 Υποστήριξη", "human")],
        ],
        "en": [
            [("🛍️ Services", "services"), ("❓ FAQ", "faq")],
            [("🖼️ Portfolio", "portfolio"), ("🏢 About us", "about")],
            [("🗓️ Book appointment", "booking"), ("⭐ Testimonials", "testimonials")],
            [("📍 Location", "location"), ("📱 Social Media", "social")],
            [("📞 Contact", "contact"), ("👤 Support", "human")],
        ],
    }
    rows = texts.get(lang, texts["en"])
    keyboard = [
        [InlineKeyboardButton(text, callback_data=data) for text, data in row]
        for row in rows
    ]
    return InlineKeyboardMarkup(keyboard)


def _ticket_admin_keyboard(ticket_id: int, lang: str) -> InlineKeyboardMarkup:
    """Inline keyboard for admin ticket notifications."""
    texts = {
        "de": ("↩️ Antworten", "✅ Schließen"),
        "el": ("↩️ Απάντηση", "✅ Κλείσιμο"),
        "en": ("↩️ Reply", "✅ Close"),
    }
    reply_text, close_text = texts.get(lang, texts["en"])
    keyboard = [
        [
            InlineKeyboardButton(reply_text, callback_data=f"ticket_reply:{ticket_id}"),
            InlineKeyboardButton(close_text, callback_data=f"ticket_close:{ticket_id}"),
        ]
    ]
    return InlineKeyboardMarkup(keyboard)


# --- Bot command menu definitions ---
PRIVATE_COMMANDS = [
    ("start", "Hauptmenü anzeigen"),
    ("help", "Hilfe und Befehlsübersicht"),
    ("services", "Unsere Leistungen"),
    ("faq", "Häufige Fragen"),
    ("portfolio", "Portfolio ansehen"),
    ("about", "Über Skypol Arts & Media"),
    ("testimonials", "Kundenbewertungen"),
    ("booking", "Termin buchen"),
    ("social", "Social-Media-Kanäle"),
    ("location", "Standort anzeigen"),
    ("contact", "Kontaktdaten"),
    ("human", "Anfrage an das Team weiterleiten"),
    ("reset", "Gespräch zurücksetzen"),
    ("language", "Sprache festlegen"),
    ("menu", "Menü anzeigen"),
]

GROUP_COMMANDS = [
    ("help", "Hilfe und Befehlsübersicht"),
    ("services", "Unsere Leistungen"),
    ("faq", "Häufige Fragen"),
    ("portfolio", "Portfolio ansehen"),
    ("about", "Über Skypol Arts & Media"),
    ("testimonials", "Kundenbewertungen"),
    ("booking", "Termin buchen"),
    ("social", "Social-Media-Kanäle"),
    ("location", "Standort anzeigen"),
    ("contact", "Kontaktdaten"),
    ("human", "Anfrage an das Team weiterleiten"),
    ("menu", "Menü anzeigen"),
    ("language", "Sprache festlegen"),
    ("pinmenu", "Hauptmenü in Gruppe anpinnen (Admin)"),
]


async def _set_bot_commands(application: Application):
    """Register the bot's command menu for private and group chats."""
    from telegram import BotCommand
    from telegram.constants import BotCommandScopeType

    private = [BotCommand(cmd, desc) for cmd, desc in PRIVATE_COMMANDS]
    group = [BotCommand(cmd, desc) for cmd, desc in GROUP_COMMANDS]

    try:
        await application.bot.set_my_commands(
            private,
            scope={"type": BotCommandScopeType.ALL_PRIVATE_CHATS},
        )
        await application.bot.set_my_commands(
            group,
            scope={"type": BotCommandScopeType.ALL_GROUP_CHATS},
        )
        logger.info("Bot command menus registered for private and group chats")
    except Exception as e:
        logger.warning("Could not set bot command menu: %s", e)


# --- Commands ---
@cleanup_command()
@tracked_command("start")
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = _language_for(update)
    welcome = knowledge.get_welcome(lang)
    await update.message.reply_text(
        welcome,
        reply_markup=main_menu_keyboard(lang),
        parse_mode=ParseMode.MARKDOWN,
    )


@cleanup_command()
@tracked_command("menu")
async def menu_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show the main menu in private or group chats."""
    lang = _language_for(update)
    menu_texts = {
        "de": "Wähle eine Option:",
        "el": "Επίλεξε μια επιλογή:",
        "en": "Choose an option:",
    }
    await update.message.reply_text(
        menu_texts.get(lang, menu_texts["en"]),
        reply_markup=main_menu_keyboard(lang),
        parse_mode=ParseMode.MARKDOWN,
    )


@cleanup_command()
@tracked_command("help")
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = _language_for(update)
    help_texts = {
        "de": (
            "*So kann ich dir helfen:*\n\n"
            "/start – Hauptmenü\n"
            "/menu – Menü anzeigen\n"
            "/services – Unsere Leistungen\n"
            "/portfolio – Portfolio ansehen\n"
            "/about – Über Skypol Arts & Media\n"
            "/faq – Häufige Fragen\n"
            "/testimonials – Kundenbewertungen\n"
            "/booking – Termin buchen\n"
            "/social – Social-Media-Kanäle\n"
            "/location – Standort\n"
            "/contact – Kontaktdaten\n"
            "/human – Anfrage an das Team weiterleiten\n"
            "/pinmenu – Hauptmenü in Gruppe anpinnen (Admin)\n"
            "/reset – Gespräch zurücksetzen\n\n"
            "*Admin-Befehle:*\n"
            "/stats – Statistiken anzeigen\n"
            "/reply <ticket_id> <Text> – Auf Ticket antworten (oder einfach auf die weitergeleitete Nachricht antworten)\n"
            "/close <ticket_id> – Ticket schließen\n"
            "/gaps – Unbeantwortete Fragen anzeigen\n"
            "/learn <gap_id> <Antwort> – Neue FAQ-Antwort lernen\n"
            "/learned – Gelernte Antworten anzeigen\n"
            "/warn /mute /kick /ban – Nutzer moderieren (auf Nachricht antworten)\n\n"
            "Schreib mir einfach deine Frage, ich antworte dir gerne."
        ),
        "el": (
            "*Πώς μπορώ να σε βοηθήσω:*\n\n"
            "/start – Κύριο μενού\n"
            "/menu – Εμφάνιση μενού\n"
            "/services – Οι υπηρεσίες μας\n"
            "/portfolio – Δείτε το portfolio\n"
            "/about – Σχετικά με τη Skypol Arts & Media\n"
            "/faq – Συχνές ερωτήσεις\n"
            "/testimonials – Κριτικές πελατών\n"
            "/booking – Κλείστε ραντεβού\n"
            "/social – Κανάλια social media\n"
            "/location – Τοποθεσία\n"
            "/contact – Στοιχεία επικοινωνίας\n"
            "/human – Προώθηση ερώτησης στην ομάδα\n"
            "/pinmenu – Καρφίτσωμα του κύριου μενού στην ομάδα (Admin)\n"
            "/reset – Επαναφορά συνομιλίας\n\n"
            "*Εντολές διαχειριστή:*\n"
            "/stats – Προβολή στατιστικών\n"
            "/reply <ticket_id> <κείμενο> – Απάντηση σε εισιτήριο (ή απλώς απάντηση στο προωθημένο μήνυμα)\n"
            "/close <ticket_id> – Κλείσιμο εισιτηρίου\n"
            "/gaps – Προβολή αναπάντητων ερωτήσεων\n"
            "/learn <gap_id> <απάντηση> – Εκμάθηση νέας FAQ απάντησης\n"
            "/learned – Προβολή εκμαθημένων απαντήσεων\n"
            "/warn /mute /kick /ban – Συντονισμός χρηστών\n\n"
            "Γράψε μου απλώς την ερώτησή σου, χαίρομαι να σε βοηθήσω."
        ),
        "en": (
            "*How I can help you:*\n\n"
            "/start – Main menu\n"
            "/menu – Show menu\n"
            "/services – Our services\n"
            "/portfolio – View portfolio\n"
            "/about – About Skypol Arts & Media\n"
            "/faq – Frequently asked questions\n"
            "/testimonials – Customer reviews\n"
            "/booking – Book an appointment\n"
            "/social – Social media channels\n"
            "/location – Location\n"
            "/contact – Contact details\n"
            "/human – Forward request to the team\n"
            "/pinmenu – Pin main menu in group (admin)\n"
            "/reset – Reset conversation\n\n"
            "*Admin commands:*\n"
            "/stats – Show statistics\n"
            "/reply <ticket_id> <text> – Reply to a ticket (or simply reply to the forwarded message)\n"
            "/close <ticket_id> – Close a ticket\n"
            "/gaps – Show unanswered questions\n"
            "/learn <gap_id> <answer> – Teach a new FAQ answer\n"
            "/learned – Show learned answers\n"
            "/warn /mute /kick /ban – Moderate users (reply to their message)\n\n"
            "Just send me your question, I'm happy to help."
        ),
    }
    await update.message.reply_text(
        help_texts.get(lang, help_texts["en"]),
        parse_mode=ParseMode.MARKDOWN,
    )


@cleanup_command()
@tracked_command("services")
async def services_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = _language_for(update)
    services_text = format_services_list(knowledge.get_services(), lang)
    await update.message.reply_text(
        f"*Unsere Leistungen:*\n\n{services_text}",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=main_menu_keyboard(lang),
    )


@cleanup_command()
@tracked_command("faq")
async def faq_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = _language_for(update)
    faq_text = format_faq(knowledge.get_faq(), lang)
    await update.message.reply_text(
        f"*Häufige Fragen:*\n\n{faq_text}",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=main_menu_keyboard(lang),
    )


@cleanup_command()
@tracked_command("contact")
async def contact_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = _language_for(update)
    company = knowledge.get_company()
    contact_texts = {
        "de": (
            f"*Kontakt*\n\n"
            f"📧 E-Mail: {company.get('email')}\n"
            f"📞 Telefon: {company.get('phone')}\n"
            f"📍 Adresse: {company.get('location')}\n"
            f"🌐 Webseite: {company.get('website')}\n"
            f"📸 Instagram: {company.get('instagram')}\n\n"
            "Schreib uns – wir melden uns schnellstmöglich!"
        ),
        "el": (
            f"*Επικοινωνία*\n\n"
            f"📧 E-Mail: {company.get('email')}\n"
            f"📞 Τηλέφωνο: {company.get('phone')}\n"
            f"📍 Διεύθυνση: {company.get('location')}\n"
            f"🌐 Ιστοσελίδα: {company.get('website')}\n"
            f"📸 Instagram: {company.get('instagram')}\n\n"
            "Γράψε μας – θα επικοινωνήσουμε το συντομότερο δυνατό!"
        ),
        "en": (
            f"*Contact*\n\n"
            f"📧 E-Mail: {company.get('email')}\n"
            f"📞 Phone: {company.get('phone')}\n"
            f"📍 Address: {company.get('location')}\n"
            f"🌐 Website: {company.get('website')}\n"
            f"📸 Instagram: {company.get('instagram')}\n\n"
            "Write to us – we'll get back to you as soon as possible!"
        ),
    }
    await update.message.reply_text(
        contact_texts.get(lang, contact_texts["en"]),
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=main_menu_keyboard(lang),
    )


async def _send_human_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = _language_for(update)
    user = update.effective_user
    chat = update.effective_chat

    # Create or reuse existing open ticket for this user/chat (works in private and group chats)
    existing_ticket = tickets.get_open_by_user_and_chat(user.id, chat.id)
    if existing_ticket:
        ticket_id = existing_ticket["id"]
    else:
        ticket_id = tickets.create(user.id, chat.id, language=lang)
        analytics.track_support_ticket()

    if ticket_id:
        messages = {
            "de": (
                f"Kein Problem! 👤 Ich habe Ticket *#{ticket_id}* erstellt und leite deine Anfrage "
                f"an unser Team weiter. Beschreibe einfach weiterhin hier dein Anliegen.\n\n"
                f"Alternativ nutze das Kontaktformular:\n"
                f"https://skypol-arts-media.netlify.app/kontakt.html"
            ),
            "el": (
                f"Κανένα πρόβλημα! 👤 Δημιούργησα το εισιτήριο *#{ticket_id}* και θα προωθήσω "
                f"το αίτημά σου στην ομάδα μας. Περιέγραψε συνέχεια εδώ το θέμα σου.\n\n"
                f"Εναλλακτικά χρησιμοποίησε τη φόρμα επικοινωνίας:\n"
                f"https://skypol-arts-media.netlify.app/kontakt.html"
            ),
            "en": (
                f"No problem! 👤 I've created ticket *#{ticket_id}* and forwarded your request "
                f"to our team. Just keep describing your issue here.\n\n"
                f"Alternatively, use the contact form:\n"
                f"https://skypol-arts-media.netlify.app/kontakt.html"
            ),
        }
    else:
        messages = {
            "de": (
                "Kein Problem! 👤 Ich leite deine Anfrage an unser Team weiter. "
                "Bitte beschreibe kurz, worum es geht, oder nutze das Kontaktformular:\n"
                "https://skypol-arts-media.netlify.app/kontakt.html"
            ),
            "el": (
                "Κανένα πρόβλημα! 👤 Θα προωθήσω το αίτημά σου στην ομάδα μας. "
                "Περιέγραψε συνοπτικά το θέμα ή χρησιμοποίησε τη φόρμα επικοινωνίας:\n"
                "https://skypol-arts-media.netlify.app/kontakt.html"
            ),
            "en": (
                "No problem! 👤 I'll forward your request to our team. "
                "Please briefly describe what it's about, or use the contact form:\n"
                "https://skypol-arts-media.netlify.app/kontakt.html"
            ),
        }
    confirmation_text = messages.get(lang, messages["en"])
    failure_messages = {
        "de": (
            "❌ Leider konnte die Benachrichtigung an unser Team gerade nicht versendet werden. "
            "Bitte nutze das Kontaktformular:\n"
            "https://skypol-arts-media.netlify.app/kontakt.html"
        ),
        "el": (
            "❌ Δυστυχώς δεν ήταν δυνατή η ειδοποίηση της ομάδας μας αυτή τη στιγμή. "
            "Παρακαλώ χρησιμοποίησε τη φόρμα επικοινωνίας:\n"
            "https://skypol-arts-media.netlify.app/kontakt.html"
        ),
        "en": (
            "❌ Unfortunately we couldn't notify our team right now. "
            "Please use the contact form:\n"
            "https://skypol-arts-media.netlify.app/kontakt.html"
        ),
    }
    failure_text = failure_messages.get(lang, failure_messages["en"])

    if update.message:
        await update.message.reply_text(confirmation_text, parse_mode=ParseMode.MARKDOWN)
    elif update.callback_query and update.callback_query.message:
        await update.callback_query.message.reply_text(confirmation_text, parse_mode=ParseMode.MARKDOWN)

    if not config.ADMIN_CHAT_ID:
        logger.warning("ADMIN_CHAT_ID is not configured; skipping admin notification")
        return

    if ticket_id:
        admin_text = _format_admin_notification(
            ticket_id, user, chat.id, lang, "Support-Anfrage gestartet mit /human"
        )
        ticket_keyboard = _ticket_admin_keyboard(ticket_id, lang)
    else:
        admin_text = (
            f"🆘 Support-Anfrage\n"
            f"👤 {user.full_name} (@{user.username or 'n/a'})\n"
            f"💬 Chat: {chat.id}\n"
            f"🌐 Language: {lang.upper()}\n\n"
            f"Antworte direkt im Chat."
        )
        ticket_keyboard = None
    ticket_label = f"Ticket #{ticket_id}" if ticket_id else "Support-Anfrage"
    logger.info("Sending human-support notification to admins (%s)", ticket_label)
    try:
        notified, failed = await _notify_admins(context, admin_text, ticket_keyboard)
        if notified and not failed:
            logger.info("Admin notification sent successfully to all admins (%s)", ticket_label)
        elif notified:
            logger.warning(
                "Admin notification partially sent (%s); failed admins: %s", ticket_label, failed
            )
        else:
            raise Exception(f"No admin could be notified; failed: {failed}")
    except Exception as e:
        logger.error("Failed to notify admins: %s", e)
        if update.message:
            await update.message.reply_text(failure_text)
        elif update.callback_query and update.callback_query.message:
            await update.callback_query.message.reply_text(failure_text)


@cleanup_command()
@tracked_command("about")
async def about_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = _language_for(update)
    company = knowledge.get_company()
    await update.message.reply_text(
        format_about(company),
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=main_menu_keyboard(lang),
    )


@cleanup_command()
@tracked_command("portfolio")
async def portfolio_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = _language_for(update)
    portfolio = knowledge.get_portfolio()
    text = (
        f"🖼️ *{portfolio.get('title', 'Portfolio')}*\n\n"
        f"Entdecke unsere Arbeit in diesen Bereichen:\n"
        f"{', '.join(portfolio.get('categories', []))}\n\n"
        f"🔗 {portfolio.get('url', '')}"
    )
    await update.message.reply_text(
        text,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=main_menu_keyboard(lang),
    )


@cleanup_command()
@tracked_command("testimonials")
async def testimonials_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = _language_for(update)
    testimonials = knowledge.get_testimonials()
    if not testimonials:
        no_testimonials = {
            "de": "⭐ Aktuell sind noch keine Bewertungen hinterlegt.",
            "el": "⭐ Αυτή τη στιγμή δεν υπάρχουν κριτικές.",
            "en": "⭐ No testimonials available yet.",
        }
        await update.message.reply_text(no_testimonials.get(lang, no_testimonials["en"]))
        return
    await update.message.reply_text(
        f"*Das sagen unsere Kund:innen:*\n\n{format_testimonials(testimonials)}",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=main_menu_keyboard(lang),
    )


@cleanup_command()
@tracked_command("booking")
async def booking_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = _language_for(update)
    booking = knowledge.get_booking()
    await update.message.reply_text(
        format_booking(booking),
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=main_menu_keyboard(lang),
    )


@cleanup_command()
@tracked_command("social")
async def social_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = _language_for(update)
    company = knowledge.get_company()
    social_texts = {
        "de": "*Folge uns für aktuelle Projekte und Insights:*\n\n",
        "el": "*Ακολούθησέ μας για τρέχοντα έργα και insights:*\n\n",
        "en": "*Follow us for latest projects and insights:*\n\n",
    }
    await update.message.reply_text(
        social_texts.get(lang, social_texts["en"]) + format_social_links(company),
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=main_menu_keyboard(lang),
    )


@cleanup_command()
@tracked_command("location")
async def location_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = _language_for(update)
    company = knowledge.get_company()
    await update.message.reply_text(
        format_location(company),
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=main_menu_keyboard(lang),
    )


@cleanup_command()
@tracked_command("human")
async def human_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _send_human_message(update, context)


@cleanup_command()
@tracked_command("reset")
async def reset_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    memory.clear(chat_id, user_id)
    lang = _language_for(update)
    messages = {
        "de": "🔄 Gespräch wurde zurückgesetzt. Wie kann ich dir helfen?",
        "el": "🔄 Η συνομιλία επαναφέρθηκε. Πώς μπορώ να σε βοηθήσω;",
        "en": "🔄 Conversation reset. How can I help you?",
    }
    await update.message.reply_text(messages.get(lang, messages["en"]))


@cleanup_command()
@tracked_command("language")
async def language_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Let a user set or view their language preference."""
    user = update.effective_user
    lang = _language_for(update)
    args = context.args or []

    if args and args[0].lower() in SUPPORTED_LANGUAGES:
        chosen = args[0].lower()
        _db.set_user_language(user.id, chosen)
        confirmations = {
            "de": f"✅ Sprache auf {chosen.upper()} gesetzt.",
            "el": f"✅ Η γλώσσα ορίστηκε σε {chosen.upper()}.",
            "en": f"✅ Language set to {chosen.upper()}.",
        }
        await update.message.reply_text(confirmations.get(chosen, confirmations["en"]))
        return

    usage = {
        "de": (
            f"🌐 Aktuelle Sprache: {lang.upper()}\n\n"
            "Nutze `/language de`, `/language el` oder `/language en`, "
            "um deine bevorzugte Sprache festzulegen."
        ),
        "el": (
            f"🌐 Τρέχουσα γλώσσα: {lang.upper()}\n\n"
            "Χρησιμοποίησε `/language de`, `/language el` ή `/language en` "
            "για να ορίσεις την προτιμώμενη γλώσσά σου."
        ),
        "en": (
            f"🌐 Current language: {lang.upper()}\n\n"
            "Use `/language de`, `/language el` or `/language en` "
            "to set your preferred language."
        ),
    }
    await update.message.reply_text(
        usage.get(lang, usage["en"]), parse_mode=ParseMode.MARKDOWN
    )


@cleanup_command(delay=5)
@tracked_command("pinmenu")
async def pinmenu_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Pin the main menu in a group (requires admin rights)."""
    chat = update.effective_chat
    lang = _language_for(update)
    welcome = knowledge.get_welcome(lang)
    try:
        menu_message = await update.message.reply_text(
            welcome,
            reply_markup=main_menu_keyboard(lang),
            parse_mode=ParseMode.MARKDOWN,
        )
        await context.bot.pin_chat_message(chat_id=chat.id, message_id=menu_message.message_id)
    except Exception as e:
        logger.error("Failed to pin menu in chat %s: %s", chat.id, e)
        error_messages = {
            "de": "❌ Ich konnte das Menü nicht anpinnen. Stelle sicher, dass ich Admin-Rechte habe.",
            "el": "❌ Δεν μπόρεσα να καρφιτσώσω το μενού. Βεβαιώσου ότι έχω δικαιώματα διαχειριστή.",
            "en": "❌ I couldn't pin the menu. Please make sure I have admin rights.",
        }
        await update.message.reply_text(error_messages.get(lang, error_messages["en"]))


@tracked_command("stats")
async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin_user(update.effective_user.id):
        return
    stats = analytics.get_stats()
    ticket_summary = tickets.get_summary()
    text = (
        f"📊 *Bot Statistiken*\n\n"
        f"Uptime: {stats['uptime_seconds']}s\n"
        f"Nachrichten gesamt: {stats['messages_total']}\n"
        f"Aktive Nutzer: {stats['active_users_count']}\n"
        f"Sprachen: {stats['messages_by_language']}\n"
        f"Befehle: {stats['commands_used']}\n"
        f"Support-Tickets: {ticket_summary['open']} offen / {ticket_summary['closed']} geschlossen\n"
        f"Flood-Ereignisse: {stats.get('flood_events', 0)}"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


@tracked_command("notifytest")
async def notifytest_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin command to test notification delivery to all configured admins."""
    if not is_admin_user(update.effective_user.id):
        return
    if not config.ADMIN_CHAT_ID:
        await update.message.reply_text("❌ Keine ADMIN_CHAT_ID konfiguriert.")
        return

    test_text = (
        "🔔 Admin-Testbenachrichtigung\n"
        f"Ausgelöst von: {update.effective_user.full_name}\n"
        f"Konfigurierte Admins: {config.ADMIN_CHAT_ID}"
    )
    notified, failed = await _notify_admins(context, test_text)

    if not notified:
        await update.message.reply_text(
            f"❌ Kein Admin konnte benachrichtigt werden. Fehlgeschlagene IDs: {failed}\n"
            "Hinweis: Jeder Admin muss den Bot zuerst mit /start starten."
        )
        return

    if failed:
        await update.message.reply_text(
            f"⚠️ Teilweise erfolgreich. Diese Admins konnten nicht erreicht werden: {failed}\n"
            "Hinweis: Diese Admins müssen den Bot zuerst mit /start starten."
        )
    else:
        await update.message.reply_text(
            f"✅ Testbenachrichtigung an alle Admins gesendet: {config.ADMIN_CHAT_ID}"
        )


@tracked_command("setflood")
async def setflood_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Allow admins to change flood protection thresholds at runtime."""
    if not is_admin_user(update.effective_user.id):
        return
    args = context.args
    if len(args) != 2:
        await update.message.reply_text("Usage: /setflood <max_messages> <window_seconds>")
        return
    try:
        max_messages = int(args[0])
        window_seconds = int(args[1])
    except ValueError:
        await update.message.reply_text("Both arguments must be numbers.")
        return

    if max_messages < 1 or window_seconds < 1:
        await update.message.reply_text("Values must be at least 1.")
        return

    flood_protection.set_thresholds(max_messages, window_seconds)
    await update.message.reply_text(
        f"✅ Flood-Schutz aktualisiert: {max_messages} Nachrichten in {window_seconds} Sekunden."
    )


# --- Admin business commands ---

@tracked_command("block")
async def block_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Block a user from interacting with the bot."""
    if not is_admin_user(update.effective_user.id):
        return
    args = context.args
    if not args:
        await update.message.reply_text("Usage: /block <user_id> [reason]")
        return
    try:
        user_id = int(args[0])
    except ValueError:
        await update.message.reply_text("❌ user_id must be a number.")
        return
    reason = " ".join(args[1:]) if len(args) > 1 else ""
    _db.block_user(user_id, reason)
    logger.info("Admin %s blocked user %s (reason: %s)", update.effective_user.id, user_id, reason)
    await update.message.reply_text(f"✅ User `{user_id}` blocked." if not reason else f"✅ User `{user_id}` blocked.\n📝 Reason: {reason}")


@tracked_command("unblock")
async def unblock_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Remove a user from the blocklist."""
    if not is_admin_user(update.effective_user.id):
        return
    args = context.args
    if not args:
        await update.message.reply_text("Usage: /unblock <user_id>")
        return
    try:
        user_id = int(args[0])
    except ValueError:
        await update.message.reply_text("❌ user_id must be a number.")
        return
    removed = _db.unblock_user(user_id)
    if removed:
        logger.info("Admin %s unblocked user %s", update.effective_user.id, user_id)
        await update.message.reply_text(f"✅ User `{user_id}` unblocked.")
    else:
        await update.message.reply_text(f"ℹ️ User `{user_id}` was not blocked.")


@tracked_command("tickets")
async def tickets_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """List open support tickets (admin only)."""
    if not is_admin_user(update.effective_user.id):
        return
    args = context.args
    page = 1
    if args:
        try:
            page = max(1, int(args[0]))
        except ValueError:
            pass
    limit = 5
    offset = (page - 1) * limit
    ticket_list, total = tickets.list_tickets(status="open", limit=limit, offset=offset)
    if not ticket_list:
        await update.message.reply_text("🎫 No open tickets." if page == 1 else f"🎫 No tickets on page {page}.")
        return
    lines = [f"🎫 *Open Tickets* (page {page}, total {total})\n"]
    for ticket in ticket_list:
        created = _format_timestamp(ticket["created_at"])
        preview = ""
        if ticket["messages"]:
            preview = ticket["messages"][0]["text"][:120].replace("\n", " ")
        lines.append(
            f"*#{ticket['id']}* – User `{ticket['user_id']}`\n"
            f"Lang: {ticket['language'].upper() or '-'} | Created: {created}\n"
            f"Preview: {preview}\n"
        )
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)


@tracked_command("export")
async def export_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Export users or tickets as CSV (admin only)."""
    if not is_admin_user(update.effective_user.id):
        return
    args = context.args
    what = (args[0] if args else "users").lower()
    if what not in ("users", "tickets"):
        await update.message.reply_text("Usage: /export [users|tickets]")
        return

    output = io.StringIO()
    writer = csv.writer(output)
    if what == "users":
        writer.writerow(["user_id", "chat_id", "username", "language", "first_seen", "last_seen"])
        for user in _db.list_users():
            writer.writerow([
                user["user_id"],
                user["chat_id"],
                user["username"] or "",
                user["language"] or "",
                _format_timestamp(user["first_seen"]),
                _format_timestamp(user["last_seen"]),
            ])
        filename = "users_export.csv"
    else:
        writer.writerow(["id", "user_id", "chat_id", "status", "language", "created_at", "closed_at"])
        ticket_list, _ = tickets.list_tickets(status=None, limit=10000, offset=0)
        for ticket in ticket_list:
            writer.writerow([
                ticket["id"],
                ticket["user_id"],
                ticket["chat_id"],
                ticket["status"],
                ticket["language"] or "",
                _format_timestamp(ticket["created_at"]),
                _format_timestamp(ticket["closed_at"]) if ticket["closed_at"] else "",
            ])
        filename = "tickets_export.csv"

    data = output.getvalue().encode("utf-8")
    await update.message.reply_document(document=io.BytesIO(data), filename=filename)


@tracked_command("broadcast")
async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send a message to all known users (admin only)."""
    if not is_admin_user(update.effective_user.id):
        return
    if not context.args:
        await update.message.reply_text("Usage: /broadcast <message>")
        return
    message_text = " ".join(context.args)
    users = _db.list_users()
    sent = 0
    failed = 0
    for user in users:
        try:
            await context.bot.send_message(chat_id=user["chat_id"], text=message_text)
            sent += 1
        except Exception as e:
            logger.debug("Broadcast failed for user %s: %s", user["user_id"], e)
            failed += 1
    logger.info("Admin %s broadcast message to %s users (%s failed)", update.effective_user.id, sent, failed)
    await update.message.reply_text(f"✅ Broadcast sent to {sent} user(s).\n❌ Failed: {failed}")


@tracked_command("backup")
async def backup_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send the SQLite database file to the admin as a backup."""
    if not is_admin_user(update.effective_user.id):
        return
    db_path = Path(config.DATABASE_PATH)
    if not db_path.exists():
        await update.message.reply_text("❌ Database file not found.")
        return
    try:
        await update.message.reply_document(
            document=open(db_path, "rb"),
            filename=f"bot_backup_{int(time.time())}.db",
        )
        logger.info("Admin %s downloaded database backup", update.effective_user.id)
    except Exception as e:
        logger.error("Failed to send database backup: %s", e)
        await update.message.reply_text("❌ Could not send backup.")


@tracked_command("feedback")
async def feedback_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Allow users to send feedback to the team."""
    if not context.args:
        await update.message.reply_text(
            "Danke für dein Feedback! Schreibe bitte /feedback <deine Nachricht>."
        )
        return
    comment = " ".join(context.args)
    user = update.effective_user
    chat = update.effective_chat
    _db.add_feedback(user.id, chat.id, comment=comment)
    logger.info("User %s sent feedback: %s", user.id, comment[:100])
    await update.message.reply_text("✅ Vielen Dank für dein Feedback!")

    if config.ADMIN_CHAT_ID:
        alert = (
            f"📝 *Neues Feedback*\n"
            f"User: `{user.id}` (@{user.username or 'n/a'})\n"
            f"Chat: `{chat.id}`\n\n"
            f"{comment[:500]}"
        )
        try:
            await _notify_admins(context, alert)
        except Exception as e:
            logger.error("Failed to notify admins about feedback: %s", e)


@tracked_command("gaps")
def _gap_learn_keyboard(gap_id: int) -> InlineKeyboardMarkup:
    """Build a single 'Learn' button for a knowledge gap."""
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("📖 Lernen", callback_data=f"learn:{gap_id}")]]
    )


async def gaps_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """List unanswered questions (knowledge gaps) for admins.

    Recurring questions (same normalized text, count >= 2) are shown first.
    Each entry gets a 'Learn' button to turn it into a learned FAQ.
    """
    if not is_admin_user(update.effective_user.id):
        return
    recurring = _db.get_recurring_unanswered_questions(min_count=2, limit=10)
    if recurring:
        lines = ["🔁 *Häufig wiederkehrende Fragen*\n"]
        buttons = []
        for item in recurring:
            preview = item["sample_question"][:200].replace("\n", " ")
            gap_id = item["ids"][0]
            lines.append(
                f"*#{gap_id}* (×{item['count']}) – {preview}\n"
            )
            buttons.append([InlineKeyboardButton(f"📖 #{gap_id} lernen", callback_data=f"learn:{gap_id}")])
        await update.message.reply_text(
            "\n".join(lines),
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup(buttons),
        )
        return

    gap_list = _db.list_unanswered_questions(reviewed=False, limit=10)
    if not gap_list:
        await update.message.reply_text("🎉 Keine unbeantworteten Fragen vorhanden.")
        return
    lines = ["🕳️ *Unbeantwortete Fragen*\n"]
    buttons = []
    for gap in gap_list:
        created = _format_timestamp(gap["created_at"])
        preview = gap["question"][:200].replace("\n", " ")
        lines.append(
            f"*#{gap['id']}* – User `{gap['user_id']}` | {gap['language'].upper() or '-'} | {created}\n"
            f"{preview}\n"
        )
        buttons.append([InlineKeyboardButton(f"📖 #{gap['id']} lernen", callback_data=f"learn:{gap['id']}")])
    await update.message.reply_text(
        "\n".join(lines), parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(buttons)
    )


@tracked_command("learn")
async def learn_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Teach the bot a new FAQ answer from an unanswered question.

    Usage: /learn <gap_id> <answer>
    """
    if not is_admin_user(update.effective_user.id):
        return
    if len(context.args) < 2:
        await update.message.reply_text(
            "Usage: /learn <gap_id> <Antwort>",
            parse_mode=ParseMode.MARKDOWN,
        )
        return
    try:
        gap_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ Ungültige Gap-ID.")
        return
    gap = _db.get_unanswered_question(gap_id)
    if not gap:
        await update.message.reply_text(f"❌ Frage #{gap_id} nicht gefunden.")
        return
    answer = " ".join(context.args[1:]).strip()
    if not answer:
        await update.message.reply_text("❌ Bitte gib eine Antwort an.")
        return
    if len(answer) > 2000:
        await update.message.reply_text("❌ Antwort zu lang (max. 2000 Zeichen).")
        return

    # Prevent learned answers from introducing new price information
    disallowed = re.findall(r"\d+[\d\s.,]*\s*€|\d+[\d\s.,]*\s*EUR|\b\d{3,}\s*€", answer)
    if disallowed:
        await update.message.reply_text(
            "❌ Gelernte Antworten dürfen keine neuen Preisangaben enthalten."
        )
        return

    try:
        _db.add_learned_faq(gap["question"], answer, source_gap_id=gap_id)
        _db.mark_unanswered_question_reviewed(gap_id)
        knowledge.add_learned_faq(gap["question"], answer, persist=False)
        # Refresh the full LLM context so the new FAQ appears in fallback prompts too
        llm.knowledge_context = knowledge.to_prompt_context()
        await update.message.reply_text(
            f"✅ Antwort gelernt für:\n*Q:* {gap['question'][:200]}\n*A:* {answer[:500]}",
            parse_mode=ParseMode.MARKDOWN,
        )
        logger.info("Admin %s learned FAQ for gap #%s", update.effective_user.id, gap_id)
    except Exception as e:
        logger.error("Failed to learn FAQ from gap #%s: %s", gap_id, e)
        await update.message.reply_text("❌ Antwort konnte nicht gespeichert werden.")


@tracked_command("learned")
async def learned_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """List all learned FAQs for admins."""
    if not is_admin_user(update.effective_user.id):
        return
    learned = _db.list_learned_faq(limit=20)
    if not learned:
        await update.message.reply_text("📚 Noch keine gelernten Antworten vorhanden.")
        return
    lines = ["📚 *Gelernte Antworten*\n"]
    for entry in learned:
        question = entry["question"][:200]
        answer = entry["answer"][:300].replace("\n", " ")
        lines.append(
            f"*#{entry['id']}* – {question}\n"
            f"_{answer}_\n"
            f"Nutzung: {entry['use_count']} | Ø-Bewertung: {entry['avg_rating'] or '-'}\n"
        )
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)


async def _auto_close_job(context: ContextTypes.DEFAULT_TYPE):
    """Close tickets that have been open for too long."""
    max_age_seconds = 7 * 24 * 60 * 60  # 7 days
    stale_tickets = tickets.list_open_older_than(max_age_seconds)
    if not stale_tickets:
        return
    closed_count = 0
    for ticket in stale_tickets:
        ticket_id = ticket["id"]
        try:
            tickets.close(ticket_id)
            tickets.add_message(ticket_id, "system", "Ticket automatically closed after 7 days of inactivity.")
            closed_count += 1
            try:
                await context.bot.send_message(
                    chat_id=ticket["chat_id"],
                    text="ℹ️ Your support ticket has been automatically closed due to inactivity. Feel free to open a new one with /human.",
                )
            except Exception:
                pass
            await _send_rating_request(context, ticket, ticket.get("language", "en"))
        except Exception as e:
            logger.error("Failed to auto-close ticket #%s: %s", ticket_id, e)
    logger.info("Auto-closed %s stale ticket(s)", closed_count)


async def _ticket_reminder_job(context: ContextTypes.DEFAULT_TYPE):
    """Remind admins about open tickets that have been waiting for a while."""
    reminder_age_seconds = 4 * 60 * 60  # 4 hours
    stale_tickets = tickets.list_open_older_than(reminder_age_seconds)
    if not stale_tickets or not config.ADMIN_CHAT_ID:
        return

    lines = [f"⏰ *Offene Tickets (älter als 4h)*\n"]
    for ticket in stale_tickets[:10]:
        created = _format_timestamp(ticket["created_at"])
        preview = ""
        if ticket["messages"]:
            preview = ticket["messages"][0]["text"][:120].replace("\n", " ")
        lines.append(
            f"*#{ticket['id']}* – User `{ticket['user_id']}`\n"
            f"Lang: {ticket['language'].upper() or '-'} | Created: {created}\n"
            f"Preview: {preview}\n"
        )
    await _notify_admins(context, "\n".join(lines))
    logger.info("Reminded admins about %s open ticket(s)", len(stale_tickets))


def _format_timestamp(ts: float | None) -> str:
    """Format a unix timestamp as a readable UTC string."""
    from datetime import datetime, timezone

    if ts is None:
        return ""
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _is_fallback_reply(reply: str, lang: str) -> bool:
    """Check whether the bot replied with the safe fallback message."""
    try:
        return reply.strip() == llm.safe_reply(lang).strip()
    except Exception:
        return False


def _log_unanswered_question(user_id: int, chat_id: int, question: str, language: str = ""):
    """Store a question the bot could not answer for later review.

    Duplicate unanswered questions (same normalized text) are only logged once
    to keep the review queue clean.
    """
    try:
        normalized = knowledge._normalize(question)
        if _db.is_unanswered_question_logged(normalized):
            logger.debug("Skipping duplicate unanswered question from user %s", user_id)
            return
        _db.add_unanswered_question(user_id, chat_id, question, language)
        logger.info("Logged unanswered question from user %s", user_id)
    except Exception as e:
        logger.error("Failed to log unanswered question: %s", e)


def _rating_keyboard(ticket_id: int, lang: str) -> InlineKeyboardMarkup:
    """Build a 1-5 star rating keyboard for a closed ticket."""
    texts = {
        "de": "Wie zufrieden bist du mit der Hilfe?",
        "el": "Πόσο ικανοποιημένος είσαι με τη βοήθεια;",
        "en": "How satisfied are you with the support?",
    }
    stars = ["⭐", "⭐⭐", "⭐⭐⭐", "⭐⭐⭐⭐", "⭐⭐⭐⭐⭐"]
    buttons = [
        [InlineKeyboardButton(stars[i], callback_data=f"rating:{ticket_id}:{i + 1}")]
        for i in range(5)
    ]
    return InlineKeyboardMarkup(buttons)


async def _send_rating_request(
    context: ContextTypes.DEFAULT_TYPE, ticket: dict, lang: str = "en"
):
    """Ask the ticket creator to rate the support experience."""
    texts = {
        "de": "Wie zufrieden bist du mit der Hilfe?",
        "el": "Πόσο ικανοποιημένος είσαι με τη βοήθεια;",
        "en": "How satisfied are you with the support?",
    }
    try:
        await context.bot.send_message(
            chat_id=ticket["user_id"],
            text=texts.get(lang, texts["en"]),
            reply_markup=_rating_keyboard(ticket["id"], lang),
        )
        logger.info("Sent rating request for ticket #%s to user %s", ticket["id"], ticket["user_id"])
    except Exception as e:
        logger.debug("Could not send rating request for ticket #%s: %s", ticket["id"], e)


async def _handle_ticket_reply_button(
    update: Update, context: ContextTypes.DEFAULT_TYPE, data: str
):
    """Put the admin into reply mode for a ticket and ask for the reply text."""
    query = update.callback_query
    try:
        ticket_id = int(data.split(":", 1)[1])
    except (ValueError, IndexError):
        await query.answer("Ungültige Ticket-ID.")
        return

    admin_id = update.effective_user.id
    if not is_admin_user(admin_id):
        await query.answer("Nur für Admins.")
        return

    ticket = tickets.get(ticket_id)
    if not ticket:
        await query.answer(f"Ticket #{ticket_id} nicht gefunden.")
        return
    if ticket["status"] != "open":
        await query.answer(f"Ticket #{ticket_id} ist bereits geschlossen.")
        return

    _pending_ticket_replies[admin_id] = ticket_id
    logger.info("Admin %s entered reply mode for ticket #%s", admin_id, ticket_id)

    prompt_texts = {
        "de": (
            f"↩️ *Antwort auf Ticket #{ticket_id}*\n\n"
            f"Schreibe jetzt deine Antwort. Sie wird direkt an den Nutzer weitergeleitet.\n\n"
            f"Tippe /cancel, um abzubrechen."
        ),
        "el": (
            f"↩️ *Απάντηση στο εισιτήριο #{ticket_id}*\n\n"
            f"Γράψε τώρα την απάντησή σου. Θα προωθηθεί απευθείας στον χρήστη.\n\n"
            f"Πληκτρολόγησε /cancel για ακύρωση."
        ),
        "en": (
            f"↩️ *Reply to ticket #{ticket_id}*\n\n"
            f"Type your reply now. It will be forwarded directly to the user.\n\n"
            f"Send /cancel to abort."
        ),
    }
    lang = _language_for(update)
    try:
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=prompt_texts.get(lang, prompt_texts["en"]),
            parse_mode=ParseMode.MARKDOWN,
        )
    except Exception as e:
        logger.error("Failed to send ticket reply prompt: %s", e)


async def _handle_ticket_close_button(
    update: Update, context: ContextTypes.DEFAULT_TYPE, data: str
):
    """Close a ticket from the admin inline button."""
    query = update.callback_query
    try:
        ticket_id = int(data.split(":", 1)[1])
    except (ValueError, IndexError):
        await query.answer("Ungültige Ticket-ID.")
        return

    if not is_admin_user(update.effective_user.id):
        await query.answer("Nur für Admins.")
        return

    ticket = tickets.get(ticket_id)
    if not ticket:
        await query.answer(f"Ticket #{ticket_id} nicht gefunden.")
        return
    if ticket["status"] != "open":
        await query.answer(f"Ticket #{ticket_id} ist bereits geschlossen.")
        return

    if tickets.close(ticket_id):
        try:
            await context.bot.send_message(
                chat_id=ticket["chat_id"],
                text="✅ Dein Ticket wurde geschlossen. Bei weiteren Fragen einfach /human nutzen.",
            )
        except Exception as e:
            logger.error("Failed to notify user about closed ticket: %s", e)

        await _send_rating_request(context, ticket, ticket.get("language", "en"))

        # Update the admin message to reflect the closed state and remove buttons
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception as e:
            logger.debug("Could not remove admin ticket buttons: %s", e)
    else:
        await query.answer(f"Ticket #{ticket_id} konnte nicht geschlossen werden.")


async def _handle_rating_button(
    update: Update, context: ContextTypes.DEFAULT_TYPE, data: str
):
    """Record a user's ticket rating from an inline button."""
    query = update.callback_query
    try:
        _, ticket_id_str, rating_str = data.split(":")
        ticket_id = int(ticket_id_str)
        rating = int(rating_str)
    except (ValueError, IndexError):
        await query.answer("Ungültige Bewertung.")
        return

    if not 1 <= rating <= 5:
        await query.answer("Bewertung muss zwischen 1 und 5 liegen.")
        return

    user = update.effective_user
    chat = update.effective_chat
    _db.add_feedback(user.id, chat.id, rating=rating, ticket_id=ticket_id)
    logger.info("User %s rated ticket #%s with %s stars", user.id, ticket_id, rating)

    thanks = {
        "de": "✅ Vielen Dank für deine Bewertung!",
        "el": "✅ Ευχαριστούμε για την αξιολόγησή σας!",
        "en": "✅ Thank you for your rating!",
    }
    lang = _language_for(update)
    await query.edit_message_text(thanks.get(lang, thanks["en"]))


async def _handle_learn_button(
    update: Update, context: ContextTypes.DEFAULT_TYPE, data: str
):
    """Prompt the admin to teach an answer for a knowledge gap."""
    query = update.callback_query
    if not is_admin_user(update.effective_user.id):
        await query.answer("Nur für Admins.")
        return
    try:
        gap_id = int(data.split(":", 1)[1])
    except (ValueError, IndexError):
        await query.answer("Ungültige ID.")
        return
    gap = _db.get_unanswered_question(gap_id)
    if not gap:
        await query.answer(f"Frage #{gap_id} nicht gefunden.")
        return
    question = gap["question"][:300]
    prompt = (
        f"📖 *Lern-Modus für Lücke #{gap_id}*\n\n"
        f"*Frage:* {question}\n\n"
        f"Sende nun:\n`/learn {gap_id} <Antwort>`"
    )
    await query.edit_message_text(prompt, parse_mode=ParseMode.MARKDOWN)


async def _handle_gap_reviewed_button(
    update: Update, context: ContextTypes.DEFAULT_TYPE, data: str
):
    """Mark an unanswered question as reviewed."""
    query = update.callback_query
    if not is_admin_user(update.effective_user.id):
        await query.answer("Nur für Admins.")
        return
    try:
        question_id = int(data.split(":", 1)[1])
    except (ValueError, IndexError):
        await query.answer("Ungültige ID.")
        return

    if _db.mark_unanswered_question_reviewed(question_id):
        await query.edit_message_text(f"✅ Frage #{question_id} als reviewed markiert.")
    else:
        await query.answer(f"Frage #{question_id} nicht gefunden.")


async def _send_reply_to_ticket(
    update: Update, context: ContextTypes.DEFAULT_TYPE, ticket_id: int, reply_text: str
) -> bool:
    """Send a reply to a ticket and confirm to the admin.

    For tickets created in groups, the reply is first sent privately to the
    user. If that fails (e.g. the user never started the bot privately), it
    falls back to the original chat (group or private).
    """
    ticket = tickets.get(ticket_id)
    if not ticket:
        await update.message.reply_text(f"Ticket #{ticket_id} not found.")
        return False
    if ticket["status"] != "open":
        await update.message.reply_text(f"Ticket #{ticket_id} is already closed.")
        return False
    tickets.add_message(ticket_id, "admin", reply_text)

    text = f"👤 *Antwort vom Team:*\n\n{escape_markdown_basic(reply_text)}"
    sent_to = None

    # Try private reply first (works for private chats and group tickets)
    try:
        await context.bot.send_message(
            chat_id=ticket["user_id"],
            text=text,
            parse_mode=ParseMode.MARKDOWN,
        )
        sent_to = "user"
        logger.info("Ticket #%s reply sent privately to user %s", ticket_id, ticket["user_id"])
    except Exception as private_error:
        logger.warning(
            "Could not send ticket #%s reply privately to %s: %s",
            ticket_id,
            ticket["user_id"],
            private_error,
        )

    # Fallback to the original chat if private delivery failed and the original
    # chat is different from the user (i.e. a group ticket).
    if sent_to is None and ticket["chat_id"] != ticket["user_id"]:
        try:
            await context.bot.send_message(
                chat_id=ticket["chat_id"],
                text=text,
                parse_mode=ParseMode.MARKDOWN,
            )
            sent_to = "chat"
            logger.info("Ticket #%s reply sent to original chat %s", ticket_id, ticket["chat_id"])
        except Exception as chat_error:
            logger.error(
                "Failed to send ticket #%s reply to chat %s: %s",
                ticket_id,
                ticket["chat_id"],
                chat_error,
            )

    if sent_to:
        await update.message.reply_text(f"✅ Reply sent to ticket #{ticket_id}.")
        return True

    await update.message.reply_text(f"❌ Failed to send reply for ticket #{ticket_id}.")
    return False


@tracked_command("reply")
async def reply_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin_user(update.effective_user.id):
        return
    args = context.args
    if not args:
        await update.message.reply_text("Usage: /reply <ticket_id> <message> or just reply to a forwarded ticket message.")
        return

    # If only one arg and it's not a number, treat whole input as message to the only open ticket
    if len(args) == 1:
        try:
            ticket_id = int(args[0])
            await update.message.reply_text("Usage: /reply <ticket_id> <message>")
            return
        except ValueError:
            open_tickets = tickets.list_open()
            if len(open_tickets) == 1:
                ticket_id = open_tickets[0]["id"]
                await _send_reply_to_ticket(update, context, ticket_id, args[0])
                return
            await update.message.reply_text(
                "Mehrere offene Tickets. Bitte ID angeben: /reply <ticket_id> <Nachricht>"
            )
            return

    try:
        ticket_id = int(args[0])
    except ValueError:
        await update.message.reply_text("Ticket ID must be a number.")
        return
    reply_text = " ".join(args[1:])
    await _send_reply_to_ticket(update, context, ticket_id, reply_text)


@tracked_command("close")
async def close_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin_user(update.effective_user.id):
        return
    args = context.args
    if not args:
        await update.message.reply_text("Usage: /close <ticket_id>")
        return
    try:
        ticket_id = int(args[0])
    except ValueError:
        await update.message.reply_text("Ticket ID must be a number.")
        return
    ticket = tickets.get(ticket_id)
    if not ticket:
        await update.message.reply_text(f"Ticket #{ticket_id} not found.")
        return
    if tickets.close(ticket_id):
        try:
            await context.bot.send_message(
                chat_id=ticket["chat_id"],
                text="✅ Dein Ticket wurde geschlossen. Bei weiteren Fragen einfach /human nutzen.",
            )
        except Exception as e:
            logger.error("Failed to notify user about closed ticket: %s", e)
        await _send_rating_request(context, ticket, ticket.get("language", "en"))
        await update.message.reply_text(f"✅ Ticket #{ticket_id} closed.")
    else:
        await update.message.reply_text(f"Ticket #{ticket_id} is already closed.")


@tracked_command("cancel")
async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancel a pending ticket reply for admins."""
    admin_id = update.effective_user.id
    if not is_admin_user(admin_id):
        return
    if _pending_ticket_replies.pop(admin_id, None):
        await update.message.reply_text("✅ Antwort abgebrochen.")
    else:
        await update.message.reply_text("Es wartet keine Antwort auf dich.")


async def _handle_pending_ticket_reply(
    update: Update, context: ContextTypes.DEFAULT_TYPE
):
    """Send an admin's pending message as a ticket reply."""
    user = update.effective_user
    if not user:
        return

    ticket_id = _pending_ticket_replies.pop(user.id, None)
    if not ticket_id:
        return

    message = update.message
    if not message or not message.text:
        return

    logger.info("Admin %s sent pending reply for ticket #%s", user.id, ticket_id)
    success = await _send_reply_to_ticket(update, context, ticket_id, message.text)
    if not success:
        # Put the pending state back so the admin can retry
        _pending_ticket_replies[user.id] = ticket_id


async def _auto_mute_flooder(
    update: Update, context: ContextTypes.DEFAULT_TYPE, user, chat
):
    """Temporarily mute a user who flooded a group and schedule an unmute."""
    try:
        await context.bot.restrict_chat_member(
            chat_id=chat.id,
            user_id=user.id,
            permissions=ChatPermissions(can_send_messages=False),
        )
        logger.info("Auto-muted user %s in chat %s for flooding", user.id, chat.id)

        async def unmute_job(job_context: ContextTypes.DEFAULT_TYPE):
            try:
                await job_context.bot.restrict_chat_member(
                    chat_id=chat.id,
                    user_id=user.id,
                    permissions=ChatPermissions(
                        can_send_messages=True,
                        can_send_polls=True,
                        can_send_other_messages=True,
                        can_add_web_page_previews=True,
                    ),
                )
                logger.info("Auto-unmuted user %s in chat %s", user.id, chat.id)
            except Exception as e:
                logger.error("Could not auto-unmute user %s: %s", user.id, e)

        context.application.job_queue.run_once(
            unmute_job,
            when=config.FLOOD_MUTE_SECONDS,
            name=f"unmute_{chat.id}_{user.id}",
        )
    except Exception as e:
        logger.error("Could not auto-mute user %s: %s", user.id, e)


async def _is_group_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Check if the issuing user is an admin or creator of the current group chat."""
    chat = update.effective_chat
    user = update.effective_user
    if not chat or chat.type not in ("group", "supergroup"):
        return False
    try:
        member = await context.bot.get_chat_member(chat.id, user.id)
        return member.status in ("administrator", "creator")
    except Exception as e:
        logger.error("Could not check group admin status for user %s: %s", user.id, e)
        return False


async def _get_moderation_target(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Validate that the user issuing the command is admin and replied to a message."""
    user = update.effective_user
    chat = update.effective_chat
    is_authorized = is_admin_user(user.id)
    if not is_authorized and chat and chat.type in ("group", "supergroup"):
        is_authorized = await _is_group_admin(update, context)

    if not is_authorized:
        return None
    if not update.message.reply_to_message:
        await update.message.reply_text("Bitte antworte auf die Nachricht des Nutzers, den du moderieren möchtest.")
        return None
    target_user = update.message.reply_to_message.from_user
    if target_user.id == context.bot.id:
        await update.message.reply_text("Ich kann mich nicht selbst moderieren. 🙂")
        return None
    return target_user


@tracked_command("warn")
async def warn_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    target_user = await _get_moderation_target(update, context)
    if not target_user:
        return
    reason = " ".join(context.args) if context.args else "Kein Grund angegeben"
    try:
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=f"⚠️ Warnung an {target_user.full_name}: {reason}",
        )
        await update.message.reply_text(f"⚠️ {target_user.full_name} wurde verwarnt.")
    except Exception as e:
        logger.error("warn failed: %s", e)
        await update.message.reply_text(f"❌ Warnung für {target_user.full_name} konnte nicht gesendet werden.")


@tracked_command("mute")
async def mute_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    target_user = await _get_moderation_target(update, context)
    if not target_user:
        return
    try:
        await context.bot.restrict_chat_member(
            chat_id=update.effective_chat.id,
            user_id=target_user.id,
            permissions=ChatPermissions(can_send_messages=False),
        )
        await update.message.reply_text(f"🔇 {target_user.full_name} wurde stummgeschaltet.")
    except Exception as e:
        logger.error("mute failed: %s", e)
        await update.message.reply_text(f"❌ {target_user.full_name} konnte nicht stummgeschaltet werden. Brauche ich Admin-Rechte?")


@tracked_command("kick")
async def kick_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    target_user = await _get_moderation_target(update, context)
    if not target_user:
        return
    try:
        await context.bot.ban_chat_member(
            chat_id=update.effective_chat.id,
            user_id=target_user.id,
        )
        await context.bot.unban_chat_member(
            chat_id=update.effective_chat.id,
            user_id=target_user.id,
        )
        await update.message.reply_text(f"👢 {target_user.full_name} wurde aus der Gruppe geworfen.")
    except Exception as e:
        logger.error("kick failed: %s", e)
        await update.message.reply_text(f"❌ {target_user.full_name} konnte nicht entfernt werden. Brauche ich Admin-Rechte?")


@tracked_command("ban")
async def ban_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    target_user = await _get_moderation_target(update, context)
    if not target_user:
        return
    try:
        await context.bot.ban_chat_member(
            chat_id=update.effective_chat.id,
            user_id=target_user.id,
        )
        await update.message.reply_text(f"🚫 {target_user.full_name} wurde gebannt.")
    except Exception as e:
        logger.error("ban failed: %s", e)
        await update.message.reply_text(f"❌ {target_user.full_name} konnte nicht gebannt werden. Brauche ich Admin-Rechte?")


async def handle_admin_reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Allow any admin to reply to a forwarded ticket message by simply replying to it."""
    if not config.ADMIN_CHAT_ID:
        return
    user = update.effective_user
    chat = update.effective_chat
    if not is_admin_user(user.id):
        return
    admin_chat_ids = set()
    for admin_id in config.ADMIN_CHAT_ID:
        try:
            admin_chat_ids.add(int(admin_id))
        except (ValueError, TypeError):
            continue
    if chat.id not in admin_chat_ids:
        return
    message = update.message
    if not message or not message.text or not message.reply_to_message:
        return

    replied_text = message.reply_to_message.text or ""
    ticket_id = extract_ticket_id(replied_text)
    if not ticket_id:
        return
    reply_text = message.text
    await _send_reply_to_ticket(update, context, ticket_id, reply_text)


async def welcome_new_member(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Welcome new members in groups when the bot is an admin."""
    if not update.chat_member:
        return
    old_status = update.chat_member.old_chat_member.status
    new_member = update.chat_member.new_chat_member
    chat = update.effective_chat

    # Bot joined the group: show a short info message.
    if new_member.user.id == context.bot.id and old_status not in ("member", "administrator", "creator"):
        bot_username = context.bot.username or "bot"
        intro_text = (
            "👋 Hallo! Ich bin der Skypol Assistant.\n\n"
            f"Erwähne mich mit @{bot_username} oder nutze die Befehle im Menü. "
            "Admins können das Menü mit `/pinmenu` dauerhaft anpinnen."
        )
        try:
            await context.bot.send_message(
                chat_id=chat.id,
                text=intro_text,
                reply_markup=main_menu_keyboard("de"),
                parse_mode=ParseMode.MARKDOWN,
            )
        except Exception as e:
            logger.error("Failed to send bot join intro in chat %s: %s", chat.id, e)
        return

    # Only greet users who actually joined (were not members before)
    if old_status in ("member", "administrator", "creator") or new_member.status != "member" or new_member.user.is_bot:
        return

    user = new_member.user
    lang = get_user_language(user.id, language_code=user.language_code, db=_db)
    bot_username = context.bot.username or "bot"
    welcome_texts = {
        "de": (
            f"Willkommen in der Gruppe, {escape_markdown_basic(user.full_name)}! 👋\n\n"
            f"Ich bin der Skypol Assistant. Schreib mir deine Frage oder nutze das Menü. "
            f"Erwähne mich mit @{bot_username}, damit ich antworte."
        ),
        "el": (
            f"Καλώς ήρθες στην ομάδα, {escape_markdown_basic(user.full_name)}! 👋\n\n"
            f"Είμαι ο Skypol Assistant. Γράψε μου την ερώτησή σου ή χρησιμοποίησε το μενού. "
            f"Ανέφερέ με με @{bot_username} για να απαντήσω."
        ),
        "en": (
            f"Welcome to the group, {escape_markdown_basic(user.full_name)}! 👋\n\n"
            f"I'm the Skypol Assistant. Send me your question or use the menu. "
            f"Mention me with @{bot_username} to get a reply."
        ),
    }
    try:
        await context.bot.send_message(
            chat_id=chat.id,
            text=welcome_texts.get(lang, welcome_texts["en"]),
            reply_markup=main_menu_keyboard(lang),
            parse_mode=ParseMode.MARKDOWN,
        )
    except Exception as e:
        logger.error("Failed to welcome new member in chat %s: %s", chat.id, e)


# --- Callbacks ---
async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    lang = _language_for(update)

    if data == "services":
        text = f"*Unsere Leistungen:*\n\n{format_services_list(knowledge.get_services(), lang)}"
    elif data == "faq":
        text = f"*Häufige Fragen:*\n\n{format_faq(knowledge.get_faq(), lang)}"
    elif data == "contact":
        company = knowledge.get_company()
        text = (
            f"📧 {company.get('email')}\n"
            f"📞 {company.get('phone')}\n"
            f"📍 {company.get('location')}\n"
            f"🌐 {company.get('website')}"
        )
    elif data == "portfolio":
        portfolio = knowledge.get_portfolio()
        text = (
            f"🖼️ *{portfolio.get('title', 'Portfolio')}*\n\n"
            f"Entdecke unsere Arbeit in diesen Bereichen:\n"
            f"{', '.join(portfolio.get('categories', []))}\n\n"
            f"🔗 {portfolio.get('url', '')}"
        )
    elif data == "about":
        text = format_about(knowledge.get_company())
    elif data == "testimonials":
        testimonials = knowledge.get_testimonials()
        text = (
            f"*Das sagen unsere Kund:innen:*\n\n{format_testimonials(testimonials)}"
            if testimonials
            else "⭐ Aktuell sind noch keine Bewertungen hinterlegt."
        )
    elif data == "booking":
        text = format_booking(knowledge.get_booking())
    elif data == "social":
        text = "*Folge uns für aktuelle Projekte und Insights:*\n\n" + format_social_links(knowledge.get_company())
    elif data == "location":
        text = format_location(knowledge.get_company())
    elif data == "human":
        await _send_human_message(update, context)
        return
    elif data.startswith("ticket_reply:"):
        await _handle_ticket_reply_button(update, context, data)
        return
    elif data.startswith("ticket_close:"):
        await _handle_ticket_close_button(update, context, data)
        return
    elif data.startswith("rating:"):
        await _handle_rating_button(update, context, data)
        return
    elif data.startswith("learn:"):
        await _handle_learn_button(update, context, data)
        return
    elif data.startswith("gap_reviewed:"):
        await _handle_gap_reviewed_button(update, context, data)
        return
    else:
        text = "Unbekannte Auswahl."

    try:
        await query.edit_message_text(
            text=text,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=main_menu_keyboard(lang),
        )
    except Exception as e:
        if "Message is not modified" in str(e):
            # User pressed the same button twice; ignore
            return
        logger.error("Failed to edit message: %s", e)
        # Fallback: try plain text so the user at least sees the content
        try:
            await query.edit_message_text(
                text=text,
                reply_markup=main_menu_keyboard(lang),
            )
        except Exception as fallback_error:
            logger.error("Plain-text fallback also failed: %s", fallback_error)
            raise


# --- Message handler ---
def _group_menu_text(user_first_name: str, lang: str) -> str:
    """Return a short, button-friendly greeting for group chats."""
    texts = {
        "de": (
            f"Hallo {user_first_name}! 👋\n"
            "Wähle eine Option – ich zeige dir die Infos direkt hier im Chat."
        ),
        "el": (
            f"Γειά σου {user_first_name}! 👋\n"
            "Επίλεξε μια επιλογή – θα σου δείξω τις πληροφορίες εδώ στη συνομιλία."
        ),
        "en": (
            f"Hi {user_first_name}! 👋\n"
            "Choose an option – I'll show the info right here in the chat."
        ),
    }
    return texts.get(lang, texts["en"])


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    user = update.effective_user
    message = update.message

    if not message or not message.text:
        return

    text = sanitize_input(message.text)
    is_group = chat.type in ("group", "supergroup")
    bot_username = f"@{context.bot.username}" if context.bot.username else ""

    # Track analytics for every text message and keep user directory up to date
    lang = _language_for(update, text)
    analytics.track_message(user.id, lang)
    _track_user(update)

    if _is_user_blocked(user.id):
        logger.info("Ignoring message from blocked user %s", user.id)
        return

    # Group spam / flood protection
    if is_group and flood_protection.is_flooding(user.id, chat.id):
        logger.warning("Flood detected from user %s in chat %s", user.id, chat.id)
        analytics.track_flood_event()
        try:
            await message.delete()
        except Exception as e:
            logger.error("Could not delete flood message: %s", e)
        await _auto_mute_flooder(update, context, user, chat)
        return

    # In groups, only respond to mentions, replies to the bot, or keywords
    if is_group:
        is_direct_mention = bool(message.entities and any(
            e.type == "mention" and text[e.offset:e.offset + e.length] == bot_username
            for e in message.entities
        ))
        is_name_mention = bot_username.replace("@", "").lower() in text.lower()
        is_reply_to_bot = bool(
            message.reply_to_message
            and message.reply_to_message.from_user
            and message.reply_to_message.from_user.id == context.bot.id
        )
        is_mention = is_direct_mention or is_name_mention or is_reply_to_bot

        if not should_answer_in_group(text, bot_username, is_mention, knowledge.get_group_keywords()):
            return

        # In groups, show a compact button menu unless the user is directly
        # replying to one of the bot's messages (then treat it as a follow-up).
        if not is_reply_to_bot:
            logger.info("Showing group menu to user %s in chat %s", user.id, chat.id)
            await message.reply_text(
                _group_menu_text(user.first_name or "", lang),
                reply_markup=main_menu_keyboard(lang),
                parse_mode=ParseMode.MARKDOWN,
            )
            return

    # Ticket mode: forward user messages to admin instead of AI reply
    open_ticket = tickets.get_open_by_user_and_chat(user.id, chat.id)
    if open_ticket:
        ticket_id = open_ticket["id"]
        tickets.add_message(ticket_id, "user", text)
        if config.ADMIN_CHAT_ID:
            try:
                await _send_typing(update, context)
                notified, failed = await _notify_admins(
                    context,
                    _format_admin_notification(ticket_id, user, chat.id, lang, text),
                    _ticket_admin_keyboard(ticket_id, lang),
                )
                if notified:
                    await message.reply_text(
                        "✅ Deine Nachricht wurde an unser Team weitergeleitet."
                    )
                else:
                    raise Exception(f"No admin could be notified; failed: {failed}")
            except Exception as e:
                logger.error("Failed to forward ticket message: %s", e)
                await message.reply_text(
                    "❌ Deine Nachricht konnte leider nicht weitergeleitet werden."
                )
        else:
            await message.reply_text(
                "❌ Es ist kein Admin hinterlegt. Bitte nutze das Kontaktformular."
            )
        return

    chat_id = chat.id
    user_id = user.id

    # Add user message to memory
    memory.add(chat_id, user_id, "user", text)

    await _send_typing(update, context)

    # Fast path: exact FAQ match bypasses the LLM entirely
    faq_answer = knowledge.find_exact_faq_answer(text)
    if faq_answer:
        logger.info("FAQ exact match used for user %s", user_id)
        reply = faq_answer
        memory.add(chat_id, user_id, "assistant", reply)
        await message.reply_text(
            escape_markdown_basic(reply),
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    # Build messages for Claude
    history = memory.get(chat_id, user_id)
    messages_for_claude = [{"role": m["role"], "content": m["content"]} for m in history]

    try:
        await _send_typing(update, context)
        reply = await llm.chat(messages_for_claude, lang=lang, is_group=is_group)
    except Exception as e:
        logger.error("LLM error: %s", e)
        contact_form = "https://skypol-arts-media.netlify.app/kontakt.html"
        error_messages = {
            "de": (
                "Entschuldigung, ich habe gerade ein technisches Problem. "
                f"Bitte versuche es später noch einmal oder nutze das Kontaktformular: {contact_form}"
            ),
            "el": (
                "Συγγνώμη, αντιμετωπίζω αυτή τη στιγμή ένα τεχνικό πρόβλημα. "
                f"Παρακαλώ δοκίμασε ξανά αργότερα ή χρησιμοποίησε τη φόρμα επικοινωνίας: {contact_form}"
            ),
            "en": (
                "Sorry, I'm experiencing a technical issue right now. "
                f"Please try again later or use the contact form: {contact_form}"
            ),
        }
        reply = error_messages.get(lang, error_messages["en"])

    memory.add(chat_id, user_id, "assistant", reply)

    if _is_fallback_reply(reply, lang):
        _log_unanswered_question(user.id, chat.id, text, lang)

    await message.reply_text(
        escape_markdown_basic(reply),
        parse_mode=ParseMode.MARKDOWN,
    )


# --- Error handler ---
async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.error("Update %s caused error %s", update, context.error)

    # In private chats, send a short apology so the user knows something went wrong.
    # In groups, avoid noisy messages unless the error happened on a direct interaction.
    if not update or not update.effective_message or not update.effective_chat:
        return

    chat = update.effective_chat
    if chat.type in ("group", "supergroup"):
        message = update.effective_message
        bot_username = f"@{context.bot.username}" if context.bot.username else ""
        is_direct = bool(
            message.entities
            and any(
                e.type == "mention" and message.text[e.offset:e.offset + e.length] == bot_username
                for e in message.entities
            )
        ) or (bot_username.replace("@", "").lower() in (message.text or "").lower())
        if not is_direct and not message.reply_to_message:
            return

    lang = _language_for(update)
    apologies = {
        "de": "😕 Da ist etwas schiefgelaufen. Bitte versuche es noch einmal.",
        "el": "😕 Κάτι πήγε στραβά. Παρακαλώ δοκίμασε ξανά.",
        "en": "😕 Something went wrong. Please try again.",
    }
    try:
        await update.effective_message.reply_text(apologies.get(lang, apologies["en"]))
    except Exception:
        pass

    # Notify admins about unexpected errors, but rate-limit to avoid alert spam.
    global _last_error_alert
    if config.ADMIN_CHAT_ID and context.error:
        now = time.time()
        if now - _last_error_alert >= _ERROR_ALERT_COOLDOWN_SECONDS:
            _last_error_alert = now
            user = update.effective_user
            chat = update.effective_chat
            error_text = str(context.error)[:200]
            alert = (
                f"🚨 *Bot Error*\n"
                f"User: `{user.id if user else 'n/a'}`\n"
                f"Chat: `{chat.id if chat else 'n/a'}`\n"
                f"Error: `{error_text}`"
            )
            try:
                await _notify_admins(context, alert)
            except Exception:
                pass


# --- Application setup ---
def create_application() -> Application:
    app = Application.builder().token(config.TELEGRAM_BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("menu", menu_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("services", services_command))
    app.add_handler(CommandHandler("faq", faq_command))
    app.add_handler(CommandHandler("contact", contact_command))
    app.add_handler(CommandHandler("portfolio", portfolio_command))
    app.add_handler(CommandHandler("about", about_command))
    app.add_handler(CommandHandler("testimonials", testimonials_command))
    app.add_handler(CommandHandler("booking", booking_command))
    app.add_handler(CommandHandler("social", social_command))
    app.add_handler(CommandHandler("location", location_command))
    app.add_handler(CommandHandler("human", human_command))
    app.add_handler(CommandHandler("reset", reset_command))
    app.add_handler(CommandHandler("language", language_command))
    app.add_handler(CommandHandler("pinmenu", pinmenu_command))
    app.add_handler(CommandHandler("stats", stats_command))
    app.add_handler(CommandHandler("notifytest", notifytest_command))
    app.add_handler(CommandHandler("setflood", setflood_command))
    app.add_handler(CommandHandler("reply", reply_command))
    app.add_handler(CommandHandler("close", close_command))
    app.add_handler(CommandHandler("cancel", cancel_command))
    app.add_handler(CommandHandler("warn", warn_command))
    app.add_handler(CommandHandler("mute", mute_command))
    app.add_handler(CommandHandler("kick", kick_command))
    app.add_handler(CommandHandler("ban", ban_command))
    app.add_handler(CommandHandler("block", block_command))
    app.add_handler(CommandHandler("unblock", unblock_command))
    app.add_handler(CommandHandler("tickets", tickets_command))
    app.add_handler(CommandHandler("export", export_command))
    app.add_handler(CommandHandler("broadcast", broadcast_command))
    app.add_handler(CommandHandler("backup", backup_command))
    app.add_handler(CommandHandler("feedback", feedback_command))
    app.add_handler(CommandHandler("gaps", gaps_command))
    app.add_handler(CommandHandler("learn", learn_command))
    app.add_handler(CommandHandler("learned", learned_command))
    app.add_handler(ChatMemberHandler(welcome_new_member, ChatMemberHandler.CHAT_MEMBER))
    app.add_handler(CallbackQueryHandler(button_callback))
    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND & _PendingReplyFilter(),
            _handle_pending_ticket_reply,
        )
    )
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & filters.REPLY, handle_admin_reply))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_error_handler(error_handler)

    if app.job_queue:
        app.job_queue.run_repeating(
            _auto_close_job,
            interval=timedelta(hours=24),
            first=timedelta(minutes=1),
        )
        app.job_queue.run_repeating(
            _ticket_reminder_job,
            interval=timedelta(hours=4),
            first=timedelta(minutes=5),
        )

    return app
