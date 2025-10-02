"""Telegram handler registration for MentorMatch bot."""
from __future__ import annotations

from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    filters,
)


def setup(application: Application, bot) -> None:
    application.add_handler(CommandHandler("start", bot.cmd_start2))
    application.add_handler(CommandHandler("help", bot.cmd_help))

    application.add_handler(
        CallbackQueryHandler(bot.cb_list_students_nav, pattern=r"^list_students(?:_\d+)?$")
    )
    application.add_handler(
        CallbackQueryHandler(bot.cb_list_supervisors_nav, pattern=r"^list_supervisors(?:_\d+)?$")
    )
    application.add_handler(
        CallbackQueryHandler(bot.cb_list_topics_nav, pattern=r"^list_topics(?:_\d+)?$")
    )
    application.add_handler(
        CallbackQueryHandler(bot.cb_import_students, pattern=r"^import_students$")
    )

    application.add_handler(CallbackQueryHandler(bot.cb_add_student_info, pattern=r"^add_student$"))
    application.add_handler(
        CallbackQueryHandler(bot.cb_add_supervisor_start, pattern=r"^add_supervisor$")
    )
    application.add_handler(CallbackQueryHandler(bot.cb_add_topic_start, pattern=r"^add_topic$"))
    application.add_handler(
        CallbackQueryHandler(bot.cb_add_topic_choose, pattern=r"^add_topic_role_(student|supervisor)$")
    )
    application.add_handler(CallbackQueryHandler(bot.cb_add_role_start, pattern=r"^add_role_\d+$"))

    application.add_handler(CallbackQueryHandler(bot.cb_confirm_me, pattern=r"^confirm_me_\d+$"))
    application.add_handler(CallbackQueryHandler(bot.cb_not_me, pattern=r"^not_me$"))
    application.add_handler(
        CallbackQueryHandler(bot.cb_register_role, pattern=r"^register_role_(student|supervisor)$")
    )
    application.add_handler(CallbackQueryHandler(bot.cb_student_me, pattern=r"^student_me$"))
    application.add_handler(CallbackQueryHandler(bot.cb_supervisor_me, pattern=r"^supervisor_me$"))
    application.add_handler(CallbackQueryHandler(bot.cb_my_topics, pattern=r"^my_topics$"))
    application.add_handler(
        CallbackQueryHandler(bot.cb_match_topics_for_me, pattern=r"^match_topics_for_me$")
    )
    application.add_handler(CallbackQueryHandler(bot.cb_view_student, pattern=r"^student_\d+$"))
    application.add_handler(
        CallbackQueryHandler(bot.cb_view_supervisor, pattern=r"^supervisor_\d+$")
    )
    application.add_handler(CallbackQueryHandler(bot.cb_view_topic, pattern=r"^topic_\d+$"))
    application.add_handler(CallbackQueryHandler(bot.cb_view_role, pattern=r"^role_\d+$"))
    application.add_handler(CallbackQueryHandler(bot.cb_apply_topic, pattern=r"^apply_topic_\d+$"))
    application.add_handler(CallbackQueryHandler(bot.cb_apply_role, pattern=r"^apply_role_\d+$"))
    application.add_handler(
        CallbackQueryHandler(bot.cb_invite_supervisor, pattern=r"^invite_supervisor_\d+_\d+$")
    )
    application.add_handler(
        CallbackQueryHandler(bot.cb_edit_student_start, pattern=r"^edit_student_\d+$")
    )
    application.add_handler(
        CallbackQueryHandler(bot.cb_edit_supervisor_start, pattern=r"^edit_supervisor_\d+$")
    )
    application.add_handler(
        CallbackQueryHandler(bot.cb_edit_topic_start, pattern=r"^edit_topic_\d+$")
    )
    application.add_handler(CallbackQueryHandler(bot.cb_edit_role_start, pattern=r"^edit_role_\d+$"))

    application.add_handler(
        CallbackQueryHandler(bot.cb_match_student, pattern=r"^match_student_\d+$")
    )
    application.add_handler(
        CallbackQueryHandler(bot.cb_match_supervisor, pattern=r"^match_supervisor_\d+$")
    )
    application.add_handler(
        CallbackQueryHandler(bot.cb_match_students_for_topic, pattern=r"^match_students_topic_\d+$")
    )
    application.add_handler(
        CallbackQueryHandler(bot.cb_match_students_for_role, pattern=r"^match_role_\d+$")
    )
    application.add_handler(
        CallbackQueryHandler(
            bot.cb_match_topics_for_supervisor,
            pattern=r"^match_topics_for_supervisor_\d+$",
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            bot.cb_messages_inbox, pattern=r"^messages_inbox(?:_(?:all|pending))?$"
        )
    )
    application.add_handler(
        CallbackQueryHandler(
            bot.cb_messages_outbox, pattern=r"^messages_outbox(?:_(?:all|pending))?$"
        )
    )
    application.add_handler(CallbackQueryHandler(bot.cb_message_view, pattern=r"^message_\d+$"))
    application.add_handler(
        CallbackQueryHandler(
            bot.cb_message_action,
            pattern=r"^message_(?:accept|reject|cancel)_\d+$",
        )
    )

    application.add_handler(CallbackQueryHandler(bot.cb_back, pattern=r"^back_to_main$"))
    application.add_error_handler(bot.on_error)

    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, bot.on_text))
