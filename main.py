import argparse
import json
import logging
from pathlib import Path
from typing import cast

from telegram import Update
from telegram.ext import (
    Application,
    ApplicationBuilder,
    ContextTypes,
    MessageHandler,
    ChatMemberHandler,
    filters,
)

logging.basicConfig(level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)


async def forward(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_message:
        logging.error(f"Update does not contain message: {update}")
        return

    # TODO use asyncio.gather to speed this up
    for target_chat_id in (context.chat_data or {}).get("target_chat_ids", []):
        forwarded_msg = await update.effective_message.forward(target_chat_id)
        if update.effective_message.link:
            await forwarded_msg.reply_text(
                update.effective_message.link, disable_notification=True
            )


async def membership_update(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logging.info(f"Membership update: {update.to_json()}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--token-file", required=True, type=Path)
    parser.add_argument("--config", default="config.json", type=Path)
    args = parser.parse_args()

    token = cast(Path, args.token_file).read_text().strip()

    application = ApplicationBuilder().token(token).build()

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
    membership_handler = ChatMemberHandler(
        membership_update, chat_member_types=ChatMemberHandler.MY_CHAT_MEMBER
    )
    application.add_handler(membership_handler)

    application.run_polling()


if __name__ == "__main__":
    main()
