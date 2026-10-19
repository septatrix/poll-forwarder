import argparse
import json
import logging
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

from telegram import ChatMember, Message, Update
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    ChatMemberHandler,
    PersistenceInput,
    PicklePersistence,
    filters,
)

logging.basicConfig(level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)

# Bots can only delete messages which are less than 48 hours old,
# so there is no point in remembering forwards for longer than that
FORWARD_RETENTION = timedelta(hours=48)


def get_forwards(context: ContextTypes.DEFAULT_TYPE) -> dict[tuple[int, int], Any]:
    """Map (chat_id, message_id) of original polls to info about their forwards"""
    return context.bot_data.setdefault("forwards", {})


def prune_forwards(context: ContextTypes.DEFAULT_TYPE):
    forwards = get_forwards(context)
    cutoff = datetime.now(UTC) - FORWARD_RETENTION
    for key in [key for key, info in forwards.items() if info["date"] < cutoff]:
        del forwards[key]


def find_forward(context: ContextTypes.DEFAULT_TYPE, msg: Message):
    """Find the original poll for either itself or one of its forwarded messages"""
    msg_key = (msg.chat_id, msg.message_id)
    for key, info in get_forwards(context).items():
        if key == msg_key or msg_key in info["messages"]:
            return key
    return None


async def forward(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_message:
        logging.error(f"Update does not contain message: {update}")
        return

    # Remember the forwarded messages so they can be deleted again later on
    prune_forwards(context)
    msg = update.effective_message
    sent_messages: list[tuple[int, int]] = []
    get_forwards(context)[(msg.chat_id, msg.message_id)] = {
        "date": msg.date,
        "author_id": msg.from_user.id if msg.from_user else None,
        "messages": sent_messages,
    }

    # If the poll is a reply to another message (e.g. a picture),
    # forward that message first to provide context for the poll
    context_msg = update.effective_message.reply_to_message
    # In forum topics messages implicitly reply to the topic creation message
    if context_msg and context_msg.forum_topic_created:
        context_msg = None

    # TODO use asyncio.gather to speed this up
    for target_chat_id in (context.chat_data or {}).get("target_chat_ids", []):
        if context_msg:
            # Forwarding the context is best-effort, it may e.g. be protected
            try:
                forwarded_context_msg = await context_msg.forward(
                    target_chat_id, disable_notification=True
                )
                sent_messages.append(
                    (forwarded_context_msg.chat_id, forwarded_context_msg.message_id)
                )
            except TelegramError as e:
                logging.warning(f"Could not forward message replied to by poll: {e}")
        forwarded_msg = await update.effective_message.forward(target_chat_id)
        sent_messages.append((forwarded_msg.chat_id, forwarded_msg.message_id))
        if update.effective_message.link:
            link_msg = await forwarded_msg.reply_text(
                update.effective_message.link, disable_notification=True
            )
            sent_messages.append((link_msg.chat_id, link_msg.message_id))


async def may_delete(update: Update, author_id: int | None) -> bool:
    msg = cast(Message, update.effective_message)
    # Anonymous admins send messages on behalf of the chat itself
    if msg.sender_chat and msg.sender_chat.id == msg.chat_id:
        return True
    if not update.effective_user:
        return False
    if update.effective_user.id == author_id:
        return True
    member = await msg.chat.get_member(update.effective_user.id)
    return member.status in (ChatMember.ADMINISTRATOR, ChatMember.OWNER)


async def delete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Delete all forwards of a poll when replying to the original or a forward"""
    msg = update.effective_message
    if not msg or not msg.reply_to_message:
        return

    key = find_forward(context, msg.reply_to_message)
    if key is None:
        await msg.reply_text(
            "This is not a poll forwarded within the last 48 hours.",
            disable_notification=True,
        )
        return
    forwards = get_forwards(context)
    if not await may_delete(update, forwards[key]["author_id"]):
        await msg.reply_text(
            "Only the author of the poll or admins can delete it.",
            disable_notification=True,
        )
        return

    message_ids_by_chat: defaultdict[int, list[int]] = defaultdict(list)
    for chat_id, message_id in forwards.pop(key)["messages"]:
        message_ids_by_chat[chat_id].append(message_id)
    for chat_id, message_ids in message_ids_by_chat.items():
        try:
            await context.bot.delete_messages(chat_id, message_ids)
        except TelegramError as e:
            logging.warning(f"Could not delete forwarded messages in {chat_id}: {e}")
    logging.info(f"Deleted forwards of poll {key}")

    # Deleting the command itself requires admin rights so this is best-effort
    try:
        await msg.delete()
    except TelegramError:
        pass


async def membership_update(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logging.info(f"Membership update: {update.to_json()}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--token-file", required=True, type=Path)
    parser.add_argument("--config", default="config.json", type=Path)
    parser.add_argument("--state", default="state.pickle", type=Path)
    args = parser.parse_args()

    token = cast(Path, args.token_file).read_text().strip()

    # Only persist bot_data as chat_data is populated from the config
    persistence = PicklePersistence(
        args.state,
        store_data=PersistenceInput(
            chat_data=False, user_data=False, callback_data=False
        ),
    )
    application = ApplicationBuilder().token(token).persistence(persistence).build()

    with cast(Path, args.config).open("rb") as f:
        config = {
            int(chat_id): targets
            for chat_id, targets in cast(dict[str, list[int]], json.load(f)).items()
        }

    for chat_id, forward_targets in config.items():
        application.chat_data[chat_id]["target_chat_ids"] = forward_targets

    async def post_init_hook(application: Application):
        for chat_id, forward_targets in config.items():
            from_chat = await application.bot.get_chat(chat_id)
            for target in forward_targets:
                to_chat = await application.bot.get_chat(target)
                logging.info(
                    f"Forwarding from {from_chat.effective_name} to {to_chat.effective_name}"
                )

    application.post_init = post_init_hook

    poll_handler = MessageHandler(filters.POLL & ~filters.UpdateType.EDITED, forward)
    application.add_handler(poll_handler)
    delete_handler = CommandHandler(
        "delete", delete, filters.REPLY & ~filters.UpdateType.EDITED
    )
    application.add_handler(delete_handler)
    membership_handler = ChatMemberHandler(
        membership_update, chat_member_types=ChatMemberHandler.MY_CHAT_MEMBER
    )
    application.add_handler(membership_handler)

    # Retry indefinitely during bootstrapping, e.g. when network connectivity
    # is not yet available directly after a reboot
    application.run_polling(bootstrap_retries=-1)


if __name__ == "__main__":
    main()
