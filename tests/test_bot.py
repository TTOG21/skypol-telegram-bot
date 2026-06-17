import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ["TELEGRAM_BOT_TOKEN"] = "123456:dummy-token-for-testing-only"
os.environ["ANTHROPIC_API_KEY"] = "dummy-anthropic-key"
os.environ["ADMIN_CHAT_ID"] = "12345"
os.environ["DATABASE_PATH"] = f"/tmp/test_bot_{__import__('uuid').uuid4().hex}.db"

from src import config
from src.database import Database
from src.bot import (
    _auto_close_job,
    _db,
    _handle_learn_button,
    _handle_pending_ticket_reply,
    _handle_rating_button,
    _is_fallback_reply,
    _is_user_blocked,
    _log_unanswered_question,
    _send_reply_to_ticket,
    _track_user,
    about_command,
    block_command,
    broadcast_command,
    button_callback,
    cancel_command,
    contact_command,
    export_command,
    feedback_command,
    flood_protection,
    gaps_command,
    handle_message,
    help_command,
    human_command,
    learn_command,
    learned_command,
    location_command,
    menu_command,
    portfolio_command,
    reset_command,
    services_command,
    setflood_command,
    social_command,
    start_command,
    stats_command,
    tickets,
    tickets_command,
    unblock_command,
    warn_command,
)

# Ensure predictable admin ID for tests (overrides real .env)
config.ADMIN_CHAT_ID = [12345]
config.ADMIN_CHAT_IDS = [12345]


def _make_update(
    user_id: int = 1,
    chat_id: int = 1,
    chat_type: str = "private",
    text: str = "",
    reply_to_user_id: int | None = None,
    language_code: str = "en",
    first_name: str = "Test",
):
    update = MagicMock()
    update.effective_user = MagicMock()
    update.effective_user.id = user_id
    update.effective_user.first_name = first_name
    update.effective_user.language_code = language_code
    update.effective_chat = MagicMock()
    update.effective_chat.id = chat_id
    update.effective_chat.type = chat_type
    update.message = MagicMock()
    update.message.text = text
    update.message.message_id = 1
    update.message.entities = None
    update.message.reply_text = AsyncMock()
    update.message.reply_document = AsyncMock()
    update.message.reply_to_message = None
    update.callback_query = None
    if reply_to_user_id is not None:
        update.message.reply_to_message = MagicMock()
        update.message.reply_to_message.from_user = MagicMock()
        update.message.reply_to_message.from_user.id = reply_to_user_id
        update.message.reply_to_message.from_user.full_name = "Target"
    return update


def _make_context(args: list[str] | None = None):
    context = MagicMock()
    context.args = args or []
    context.bot = MagicMock()
    context.bot.id = 99
    context.bot.send_message = AsyncMock()
    context.bot.restrict_chat_member = AsyncMock()
    context.bot.ban_chat_member = AsyncMock()
    context.bot.unban_chat_member = AsyncMock()
    return context


def _make_callback_update(user_id: int = 12345, chat_id: int = 1, data: str = "services"):
    update = MagicMock()
    update.effective_user = MagicMock()
    update.effective_user.id = user_id
    update.effective_user.language_code = "de"
    update.effective_chat = MagicMock()
    update.effective_chat.id = chat_id
    update.callback_query = MagicMock()
    update.callback_query.data = data
    update.callback_query.answer = AsyncMock()
    update.callback_query.edit_message_reply_markup = AsyncMock()
    update.callback_query.edit_message_text = AsyncMock()
    return update


def run_async(coro):
    import asyncio

    return asyncio.run(coro)


def test_setflood_updates_thresholds():
    # Reset to known state
    flood_protection.set_thresholds(5, 10)

    update = _make_update(user_id=12345, text="/setflood 3 15")
    context = _make_context(args=["3", "15"])

    run_async(setflood_command(update, context))

    assert flood_protection.max_messages == 3
    assert flood_protection.window_seconds == 15
    update.message.reply_text.assert_called_once()
    assert "3" in update.message.reply_text.call_args[0][0]
    print("✓ /setflood updates thresholds")


def test_setflood_rejects_non_admin():
    update = _make_update(user_id=99999, text="/setflood 3 15")
    context = _make_context(args=["3", "15"])

    run_async(setflood_command(update, context))

    update.message.reply_text.assert_not_called()
    print("✓ /setflood rejects non-admin")


def test_setflood_rejects_invalid_arguments():
    update = _make_update(user_id=12345, text="/setflood abc")
    context = _make_context(args=["abc"])

    run_async(setflood_command(update, context))

    update.message.reply_text.assert_called_once()
    assert "Usage" in update.message.reply_text.call_args[0][0]
    print("✓ /setflood rejects invalid arguments")


def test_group_admin_can_warn():
    update = _make_update(
        user_id=111,
        chat_id=-100,
        chat_type="supergroup",
        text="/warn spam",
        reply_to_user_id=222,
    )
    context = _make_context(args=["spam"])

    member = MagicMock()
    member.status = "administrator"
    context.bot.get_chat_member = AsyncMock(return_value=member)

    run_async(warn_command(update, context))

    context.bot.get_chat_member.assert_called_once_with(-100, 111)
    update.message.reply_text.assert_called_once()
    assert "verwarnt" in update.message.reply_text.call_args[0][0]
    print("✓ Group admin can warn")


def test_non_group_admin_cannot_warn():
    update = _make_update(
        user_id=111,
        chat_id=-100,
        chat_type="supergroup",
        text="/warn spam",
        reply_to_user_id=222,
    )
    context = _make_context(args=["spam"])

    member = MagicMock()
    member.status = "member"
    context.bot.get_chat_member = AsyncMock(return_value=member)

    run_async(warn_command(update, context))

    update.message.reply_text.assert_not_called()
    print("✓ Non-group-admin cannot warn")


def test_handle_message_ignores_group_without_trigger():
    update = _make_update(
        user_id=1,
        chat_id=-100,
        chat_type="supergroup",
        text="Random chat message",
    )
    context = _make_context()
    context.bot.username = "skypolbot"

    run_async(handle_message(update, context))

    # Bot should not reply because there is no trigger
    update.message.reply_text.assert_not_called()
    print("✓ handle_message ignores group without trigger")


def test_handle_message_uses_faq_exact_match():
    update = _make_update(
        user_id=1,
        chat_id=1,
        chat_type="private",
        text="Wie lange dauert eine Buchung?",
        language_code="de",
    )
    context = _make_context()

    run_async(handle_message(update, context))

    update.message.reply_text.assert_called_once()
    reply = update.message.reply_text.call_args[0][0]
    assert "ersten Gespräch" in reply or "Termin" in reply
    print("✓ handle_message uses FAQ exact match")


def test_handle_message_shows_menu_in_group():
    update = _make_update(
        user_id=1,
        chat_id=-100,
        chat_type="supergroup",
        text="@skypolbot Hilfe",
        language_code="de",
        first_name="Max",
    )
    context = _make_context()
    context.bot.username = "skypolbot"

    run_async(handle_message(update, context))

    update.message.reply_text.assert_called_once()
    reply = update.message.reply_text.call_args[0][0]
    assert "Max" in reply
    assert "option" in reply.lower() or "Option" in reply
    # The reply should include the inline keyboard markup
    assert update.message.reply_text.call_args.kwargs.get("reply_markup") is not None
    print("✓ handle_message shows menu in group")


def test_auto_mute_on_flood():
    from src.bot import flood_protection as fp

    # Set very low thresholds for the test
    original_max = fp.max_messages
    original_window = fp.window_seconds
    fp.set_thresholds(2, 10)
    try:
        context = _make_context()
        context.bot.username = "skypolbot"
        context.application = MagicMock()
        context.application.job_queue = MagicMock()

        for i in range(3):
            update = _make_update(
                user_id=42,
                chat_id=-200,
                chat_type="supergroup",
                text=f"spam {i}",
            )
            run_async(handle_message(update, context))

        # After 3 messages with threshold=2, the third should trigger mute
        context.bot.restrict_chat_member.assert_called()
        context.application.job_queue.run_once.assert_called()
    finally:
        fp.set_thresholds(original_max, original_window)
    print("✓ Auto-mute on flood works")


def test_start_command():
    update = _make_update(user_id=1, text="/start", language_code="de")
    context = _make_context()
    run_async(start_command(update, context))
    update.message.reply_text.assert_called_once()
    assert "Skypol" in update.message.reply_text.call_args[0][0]
    print("✓ /start command works")


def test_menu_command():
    update = _make_update(user_id=1, text="/menu", language_code="de")
    context = _make_context()
    run_async(menu_command(update, context))
    update.message.reply_text.assert_called_once()
    args = update.message.reply_text.call_args
    assert args.kwargs.get("reply_markup") is not None
    print("✓ /menu command works")


def test_help_command():
    update = _make_update(user_id=1, text="/help", language_code="de")
    context = _make_context()
    run_async(help_command(update, context))
    update.message.reply_text.assert_called_once()
    print("✓ /help command works")


def test_reset_command():
    update = _make_update(user_id=1, text="/reset", language_code="de")
    context = _make_context()
    run_async(reset_command(update, context))
    update.message.reply_text.assert_called_once()
    print("✓ /reset command works")


def test_services_command():
    update = _make_update(user_id=1, text="/services", language_code="de")
    context = _make_context()
    run_async(services_command(update, context))
    update.message.reply_text.assert_called_once()
    print("✓ /services command works")


def test_about_command():
    update = _make_update(user_id=1, text="/about", language_code="de")
    context = _make_context()
    run_async(about_command(update, context))
    update.message.reply_text.assert_called_once()
    print("✓ /about command works")


def test_contact_command():
    update = _make_update(user_id=1, text="/contact", language_code="de")
    context = _make_context()
    run_async(contact_command(update, context))
    update.message.reply_text.assert_called_once()
    print("✓ /contact command works")


def test_human_command_creates_ticket():
    update = _make_update(user_id=1, text="/human", language_code="de")
    context = _make_context()
    run_async(human_command(update, context))
    update.message.reply_text.assert_called_once()
    print("✓ /human command creates ticket")


def test_stats_command_for_admin():
    update = _make_update(user_id=12345, text="/stats", language_code="de")
    context = _make_context()
    run_async(stats_command(update, context))
    update.message.reply_text.assert_called_once()
    print("✓ /stats command works for admin")


def test_portfolio_command():
    update = _make_update(user_id=1, text="/portfolio", language_code="de")
    context = _make_context()
    run_async(portfolio_command(update, context))
    update.message.reply_text.assert_called_once()
    print("✓ /portfolio command works")


def test_social_command():
    update = _make_update(user_id=1, text="/social", language_code="de")
    context = _make_context()
    run_async(social_command(update, context))
    update.message.reply_text.assert_called_once()
    print("✓ /social command works")


def test_location_command():
    update = _make_update(user_id=1, text="/location", language_code="de")
    context = _make_context()
    run_async(location_command(update, context))
    update.message.reply_text.assert_called_once()
    print("✓ /location command works")


def test_ticket_close_button_closes_ticket():
    ticket_id = tickets.create(user_id=7, chat_id=8)
    update = _make_callback_update(user_id=12345, data=f"ticket_close:{ticket_id}")
    context = _make_context()

    run_async(button_callback(update, context))

    # Top-level empty answer plus optional close answer
    assert update.callback_query.answer.call_count >= 1
    assert context.bot.send_message.call_count == 2
    assert tickets.get(ticket_id)["status"] == "closed"
    update.callback_query.edit_message_reply_markup.assert_called_once()
    print("✓ Ticket close button closes ticket and asks for rating")


def test_ticket_reply_button_shows_hint():
    ticket_id = tickets.create(user_id=7, chat_id=8)
    update = _make_callback_update(user_id=12345, data=f"ticket_reply:{ticket_id}")
    context = _make_context()

    run_async(button_callback(update, context))

    update.callback_query.answer.assert_called_once()
    context.bot.send_message.assert_called_once()
    call_args = context.bot.send_message.call_args
    assert str(ticket_id) in call_args.kwargs.get("text", "")
    print("✓ Ticket reply button shows hint")


def test_ticket_close_button_rejects_non_admin():
    ticket_id = tickets.create(user_id=7, chat_id=8)
    update = _make_callback_update(user_id=99999, data=f"ticket_close:{ticket_id}")
    context = _make_context()

    run_async(button_callback(update, context))

    # Top-level answer + rejection answer
    assert update.callback_query.answer.call_count == 2
    assert "Admins" in update.callback_query.answer.call_args_list[-1][0][0]
    assert tickets.get(ticket_id)["status"] == "open"
    print("✓ Ticket close button rejects non-admin")


def test_reply_to_group_ticket_tries_private_first():
    """For a group ticket, reply should be sent privately to the user first."""
    ticket_id = tickets.create(user_id=111, chat_id=-100)
    update = _make_update(user_id=12345, text="/reply 1 Hello")
    context = _make_context()

    result = run_async(_send_reply_to_ticket(update, context, ticket_id, "Hello from support"))

    assert result is True
    assert context.bot.send_message.call_count == 1
    call = context.bot.send_message.call_args
    assert call.kwargs["chat_id"] == 111  # private user chat
    update.message.reply_text.assert_called_once()
    assert "Reply sent" in update.message.reply_text.call_args[0][0]
    print("✓ Group ticket reply sent privately")


def test_reply_to_group_ticket_falls_back_to_group():
    """If private delivery fails, the reply should fall back to the group chat."""
    ticket_id = tickets.create(user_id=111, chat_id=-100)
    update = _make_update(user_id=12345, text="/reply 1 Hello")
    context = _make_context()

    async def failing_then_succeed(*args, **kwargs):
        if kwargs.get("chat_id") == 111:
            raise Exception("Bot can't initiate chat")
        return None

    context.bot.send_message = AsyncMock(side_effect=failing_then_succeed)

    result = run_async(_send_reply_to_ticket(update, context, ticket_id, "Hello from support"))

    assert result is True
    assert context.bot.send_message.call_count == 2
    assert context.bot.send_message.call_args_list[0].kwargs["chat_id"] == 111
    assert context.bot.send_message.call_args_list[1].kwargs["chat_id"] == -100
    print("✓ Group ticket reply falls back to group chat")


def test_pending_ticket_reply_flow():
    """Admin can tap Reply and then send a normal message as the ticket reply."""
    ticket_id = tickets.create(user_id=111, chat_id=-100)
    # Simulate the admin tapping the Reply button
    update = _make_callback_update(user_id=12345, data=f"ticket_reply:{ticket_id}")
    context = _make_context()
    run_async(button_callback(update, context))

    # Admin now sends a normal text message
    reply_update = _make_update(user_id=12345, text="Thanks for reaching out!")
    run_async(_handle_pending_ticket_reply(reply_update, context))

    # First call is the prompt, second call is the actual reply
    assert context.bot.send_message.call_count == 2
    reply_call = context.bot.send_message.call_args_list[-1]
    assert reply_call.kwargs["chat_id"] == 111
    assert "Thanks for reaching out" in reply_call.kwargs["text"]
    print("✓ Pending ticket reply flow works")


def test_cancel_command_clears_pending_reply():
    ticket_id = tickets.create(user_id=111, chat_id=-100)
    update = _make_callback_update(user_id=12345, data=f"ticket_reply:{ticket_id}")
    context = _make_context()
    run_async(button_callback(update, context))

    cancel_update = _make_update(user_id=12345, text="/cancel")
    cancel_context = _make_context()
    run_async(cancel_command(cancel_update, cancel_context))

    cancel_update.message.reply_text.assert_called_once()
    assert "abgebrochen" in cancel_update.message.reply_text.call_args[0][0].lower()
    print("✓ /cancel clears pending ticket reply")


def test_track_user_inserts_directory_entry():
    _db.block_user(777001, "test")  # ensure clean state
    _db.unblock_user(777001)
    update = _make_update(user_id=777001, chat_id=777002, text="Hi", language_code="de")
    _track_user(update)
    user = _db.get_user(777001)
    assert user is not None
    assert user["chat_id"] == 777002
    assert user["language"] == "de"
    print("✓ User tracking inserts directory entry")


def test_blocked_user_is_ignored():
    update = _make_update(user_id=777003, chat_id=777004, text="Hi", language_code="en")
    _db.block_user(777003, "spam")
    assert _is_user_blocked(777003) is True
    run_async(handle_message(update, _make_context()))
    update.message.reply_text.assert_not_called()
    _db.unblock_user(777003)
    print("✓ Blocked user messages are ignored")


def test_block_and_unblock_command():
    update = _make_update(user_id=12345, text="/block 777005 spammer")
    context = _make_context(args=["777005", "spammer"])
    run_async(block_command(update, context))
    assert _db.is_blocked(777005)

    unblock_update = _make_update(user_id=12345, text="/unblock 777005")
    unblock_context = _make_context(args=["777005"])
    run_async(unblock_command(unblock_update, unblock_context))
    assert not _db.is_blocked(777005)
    print("✓ /block and /unblock manage the blocklist")


def test_tickets_command_lists_open_tickets():
    ticket_id = tickets.create(user_id=888001, chat_id=888002, language="en")
    update = _make_update(user_id=12345, text="/tickets")
    context = _make_context()
    run_async(tickets_command(update, context))
    text = update.message.reply_text.call_args[0][0]
    assert str(ticket_id) in text
    assert "888001" in text
    tickets.close(ticket_id)
    print("✓ /tickets lists open tickets")


def test_export_command_sends_csv():
    _db.upsert_user(999001, 999002, "tester", "en")
    update = _make_update(user_id=12345, text="/export users")
    context = _make_context(args=["users"])
    run_async(export_command(update, context))
    update.message.reply_document.assert_called_once()
    filename = update.message.reply_document.call_args.kwargs["filename"]
    assert filename == "users_export.csv"
    print("✓ /export sends CSV document")


def test_broadcast_command_sends_to_users():
    _db.upsert_user(999003, 999004, "tester2", "en")
    update = _make_update(user_id=12345, text="/broadcast Hello all")
    context = _make_context(args=["Hello", "all"])
    run_async(broadcast_command(update, context))
    assert context.bot.send_message.call_count >= 1
    update.message.reply_text.assert_called_once()
    print("✓ /broadcast sends message to known users")


def test_auto_close_job_closes_stale_ticket():
    import time

    ticket_id = tickets.create(user_id=555001, chat_id=555002, language="en")
    # Manually age the ticket
    _db._execute("UPDATE tickets SET created_at = ? WHERE id = ?", (time.time() - 8 * 24 * 3600, ticket_id))
    context = _make_context()
    run_async(_auto_close_job(context))
    ticket = tickets.get(ticket_id)
    assert ticket["status"] == "closed"
    print("✓ Auto-close job closes stale tickets")


def test_feedback_command_stores_feedback():
    update = _make_update(user_id=12345, text="/feedback Great bot!")
    context = _make_context(args=["Great", "bot!"])
    run_async(feedback_command(update, context))
    update.message.reply_text.assert_called_once()
    stored = _db.list_feedback(limit=1)
    assert stored and stored[0]["comment"] == "Great bot!"
    print("✓ /feedback stores feedback and notifies user")


def test_gaps_command_lists_unanswered_questions():
    _db.add_unanswered_question(333001, 333002, "Unknown service question", "en")
    update = _make_update(user_id=12345, text="/gaps")
    context = _make_context()
    run_async(gaps_command(update, context))
    text = update.message.reply_text.call_args[0][0]
    assert "Unknown service question" in text
    print("✓ /gaps lists unanswered questions")


def test_rating_button_records_feedback():
    ticket_id = tickets.create(user_id=444001, chat_id=444002, language="en")
    tickets.close(ticket_id)
    update = _make_callback_update(user_id=444001, chat_id=444002, data=f"rating:{ticket_id}:5")
    context = _make_context()
    run_async(_handle_rating_button(update, context, data=f"rating:{ticket_id}:5"))
    update.callback_query.edit_message_text.assert_called_once()
    stored = _db.list_feedback(limit=1)
    assert stored and stored[0]["rating"] == 5 and stored[0]["ticket_id"] == ticket_id
    print("✓ Rating button records feedback")


def test_fallback_reply_logs_unanswered_question():
    update = _make_update(user_id=555001, chat_id=555002, text="Completely unknown query xyz")
    context = _make_context()
    # Ensure no FAQ match and force an English fallback LLM response
    original_chat = handle_message.__globals__["llm"].chat
    async def fake_chat(*args, **kwargs):
        return "Sorry, I cannot give you a satisfactory answer to that. Please use /human so our team can help you personally."
    handle_message.__globals__["llm"].chat = fake_chat
    try:
        run_async(handle_message(update, context))
        gaps = _db.list_unanswered_questions(reviewed=False, limit=10)
        assert any(g["question"] == "Completely unknown query xyz" for g in gaps)
    finally:
        handle_message.__globals__["llm"].chat = original_chat
    print("✓ Fallback reply logs unanswered question")


def test_learn_command_stores_faq():
    gap_id = _db.add_unanswered_question(666001, 666002, "Wie lerne ich eine Antwort?", "de")
    update = _make_update(user_id=12345, text="/learn {} Einfach so.".format(gap_id))
    context = _make_context(args=[str(gap_id), "Einfach", "so."])
    run_async(learn_command(update, context))
    update.message.reply_text.assert_called_once()
    normalized = _db._normalize_text("Wie lerne ich eine Antwort?")
    learned = _db.get_learned_faq_by_normalized(normalized)
    assert learned is not None
    assert learned["answer"] == "Einfach so."
    assert _db.get_unanswered_question(gap_id)["reviewed"] == 1
    print("✓ /learn stores FAQ and marks gap reviewed")


def test_learned_command_lists_faqs():
    _db.add_learned_faq("Testfrage Alpha?", "Antwort Alpha")
    update = _make_update(user_id=12345, text="/learned")
    context = _make_context()
    run_async(learned_command(update, context))
    text = update.message.reply_text.call_args[0][0]
    assert "Testfrage Alpha?" in text
    print("✓ /learned lists learned FAQs")


def test_gaps_command_shows_learn_button():
    _db.add_unanswered_question(777001, 777002, "Was ist eine Testlücke?", "de")
    update = _make_update(user_id=12345, text="/gaps")
    context = _make_context()
    run_async(gaps_command(update, context))
    reply_markup = update.message.reply_text.call_args[1]["reply_markup"]
    assert reply_markup is not None
    assert any("learn:" in btn.callback_data for row in reply_markup.inline_keyboard for btn in row)
    print("✓ /gaps shows learn buttons")


def test_learn_button_prompts_for_answer():
    gap_id = _db.add_unanswered_question(888001, 888002, "Was passiert beim Lern-Button?", "de")
    update = _make_callback_update(user_id=12345, chat_id=1, data=f"learn:{gap_id}")
    context = _make_context()
    run_async(_handle_learn_button(update, context, data=f"learn:{gap_id}"))
    update.callback_query.edit_message_text.assert_called_once()
    text = update.callback_query.edit_message_text.call_args[0][0]
    assert f"/learn {gap_id}" in text
    print("✓ Learn button prompts admin with /learn command")


if __name__ == "__main__":
    test_setflood_updates_thresholds()
    test_setflood_rejects_non_admin()
    test_setflood_rejects_invalid_arguments()
    test_group_admin_can_warn()
    test_non_group_admin_cannot_warn()
    test_handle_message_ignores_group_without_trigger()
    test_handle_message_uses_faq_exact_match()
    test_handle_message_shows_menu_in_group()
    test_auto_mute_on_flood()
    test_start_command()
    test_menu_command()
    test_help_command()
    test_reset_command()
    test_services_command()
    test_about_command()
    test_contact_command()
    test_human_command_creates_ticket()
    test_stats_command_for_admin()
    test_portfolio_command()
    test_social_command()
    test_location_command()
    test_ticket_close_button_closes_ticket()
    test_ticket_reply_button_shows_hint()
    test_ticket_close_button_rejects_non_admin()
    test_reply_to_group_ticket_tries_private_first()
    test_reply_to_group_ticket_falls_back_to_group()
    test_pending_ticket_reply_flow()
    test_cancel_command_clears_pending_reply()
    test_track_user_inserts_directory_entry()
    test_blocked_user_is_ignored()
    test_block_and_unblock_command()
    test_tickets_command_lists_open_tickets()
    test_export_command_sends_csv()
    test_broadcast_command_sends_to_users()
    test_auto_close_job_closes_stale_ticket()
    test_feedback_command_stores_feedback()
    test_gaps_command_lists_unanswered_questions()
    test_rating_button_records_feedback()
    test_fallback_reply_logs_unanswered_question()
    test_learn_command_stores_faq()
    test_learned_command_lists_faqs()
    test_gaps_command_shows_learn_button()
    test_learn_button_prompts_for_answer()
    print("\nBot handler tests passed!")
