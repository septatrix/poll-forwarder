import os

import dotenv
from pyrogram import Client, filters
from pyrogram.types import Message

dotenv.load_dotenv()

FORWARD_FROM_CHATID = int(os.environ["FORWARD_FROM_CHATID"])
FORWARD_TO_CHATID = int(os.environ["FORWARD_TO_CHATID"])

app = Client("PollForwardingBot", bot_token=os.environ["BOT_TOKEN"])


@app.on_message(filters.poll & ~filters.edited & filters.chat(FORWARD_FROM_CHATID))
async def forward_poll(client, message: Message):
    """Forward a poll to another chat and reference the original message"""
    forwarded = await message.forward(FORWARD_TO_CHATID)
    if not isinstance(forwarded, Message):
        raise RuntimeError(f"Unexpected return value from forward: {forwarded}")
    if message.chat.type in {"group", "supergroup", "channel"}:
        await forwarded.reply_text(message.link, quote=True, disable_notification=True)


@app.on_message(
    (filters.new_chat_members | filters.left_chat_member)
    & filters.chat(FORWARD_TO_CHATID)
)
async def delete_member_change_notification(client, message: Message):
    return await message.delete()


if __name__ == "__main__":
    app.run()
