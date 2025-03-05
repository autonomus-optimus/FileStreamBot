from __future__ import annotations
import logging
from datetime import datetime
from pyrogram import Client
from typing import Any, Optional

from pyrogram.enums import ParseMode, ChatType
from pyrogram.types import Message
from pyrogram.file_id import FileId
from FileStream.bot import FileStream
from FileStream.utils.database import Database
from FileStream.config import Telegram, Server

db = Database(Telegram.DATABASE_URL, Telegram.SESSION_NAME)


async def get_file_ids(client: Client | bool, db_id: str, multi_clients, message) -> Optional[FileId]:
    try:
        logging.debug("Starting of get_file_ids")
        file_info = await db.get_file(db_id)

        if not file_info or "file_ids" not in file_info or not client:
            logging.debug("Storing file_id of all clients in DB")
            log_msg = await send_file(FileStream, db_id, file_info.get('file_id', ""), message)
            await db.update_file_ids(db_id, await update_file_id(log_msg.id, multi_clients))
            logging.debug("Stored file_id of all clients in DB")
            if not client:
                return None
            file_info = await db.get_file(db_id)

        file_id_info = file_info.setdefault("file_ids", {})

        if str(client.id) not in file_id_info or not file_id_info[str(client.id)]:
            logging.debug(f"No file ID found for client {client.id}, storing now...")
            log_msg = await send_file(FileStream, db_id, file_info.get('file_id', ""), message)
            msg = await client.get_messages(Telegram.FLOG_CHANNEL, log_msg.id)
            media = get_media_from_message(msg)
            file_id_info[str(client.id)] = getattr(media, "file_id", "")
            await db.update_file_ids(db_id, file_id_info)
            logging.debug("Stored file_id in DB")

        if str(client.id) not in file_id_info or not file_id_info[str(client.id)]:
            logging.error(f"No file ID found for client {client.id}, returning None")
            return None

        logging.debug("Middle of get_file_ids")
        file_id = FileId.decode(file_id_info[str(client.id)])
        setattr(file_id, "file_size", file_info.get('file_size', 0))
        setattr(file_id, "mime_type", file_info.get('mime_type', "None/unknown"))
        setattr(file_id, "file_name", file_info.get('file_name', "unknown_file"))
        setattr(file_id, "unique_id", file_info.get('file_unique_id', ""))
        logging.debug("Ending of get_file_ids")

        return file_id
    except Exception as e:
        logging.error(f"Error in get_file_ids: {e}", exc_info=True)
        return None


def get_media_from_message(message: "Message") -> Any:
    try:
        media_types = (
            "audio",
            "document",
            "photo",
            "sticker",
            "animation",
            "video",
            "voice",
            "video_note",
        )
        for attr in media_types:
            media = getattr(message, attr, None)
            if media:
                return media
    except Exception as e:
        logging.error(f"Error in get_media_from_message: {e}", exc_info=True)
    return None


def get_media_file_size(m):
    try:
        media = get_media_from_message(m)
        return getattr(media, "file_size", "None")
    except Exception as e:
        logging.error(f"Error in get_media_file_size: {e}", exc_info=True)
        return "None"


def get_name(media_msg: Message | FileId) -> str:
    try:
        file_name = ""

        if isinstance(media_msg, Message):
            media = get_media_from_message(media_msg)
            file_name = getattr(media, "file_name", "")

        elif isinstance(media_msg, FileId):
            file_name = getattr(media_msg, "file_name", "")

        if not file_name:
            if isinstance(media_msg, Message) and media_msg.media:
                media_type = media_msg.media.value
            elif isinstance(media_msg, FileId) and media_msg.file_type:
                media_type = media_msg.file_type.name.lower()
            else:
                media_type = "file"

            formats = {
                "photo": "jpg", "audio": "mp3", "voice": "ogg",
                "video": "mp4", "animation": "mp4", "video_note": "mp4",
                "sticker": "webp"
            }

            ext = formats.get(media_type, "")
            ext = "." + ext if ext else ""

            date = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            file_name = f"{media_type}-{date}{ext}"

        return file_name
    except Exception as e:
        logging.error(f"Error in get_name: {e}", exc_info=True)
        return "unknown_file"


def get_file_info(message):
    try:
        media = get_media_from_message(message)
        if hasattr(message, "chat") and message.chat and hasattr(message.chat, "type"):
            user_idx = message.from_user.id if message.chat.type == ChatType.PRIVATE else message.chat.id
        else:
            user_idx = "unknown"

        return {
            "user_id": user_idx,
            "file_id": getattr(media, "file_id", ""),
            "file_unique_id": getattr(media, "file_unique_id", ""),
            "file_name": get_name(message),
            "file_size": getattr(media, "file_size", 0),
            "mime_type": getattr(media, "mime_type", "None/unknown")
        }
    except Exception as e:
        logging.error(f"Error in get_file_info: {e}", exc_info=True)
        return {}


async def update_file_id(msg_id, multi_clients):
    try:
        file_ids = {}
        for client_id, client in multi_clients.items():
            log_msg = await client.get_messages(Telegram.FLOG_CHANNEL, msg_id)
            media = get_media_from_message(log_msg)
            file_ids[str(client.id)] = getattr(media, "file_id", "")

        return file_ids
    except Exception as e:
        logging.error(f"Error in update_file_id: {e}", exc_info=True)
        return {}


async def send_file(client: Client, db_id, file_id: str, message):
    try:
        file_caption = getattr(message, 'caption', None) or get_name(message)
        log_msg = await client.send_cached_media(chat_id=Telegram.FLOG_CHANNEL, file_id=file_id,
                                                 caption=f'**{file_caption}**')

        if hasattr(message, "chat") and message.chat and hasattr(message.chat, "type"):
            if message.chat.type == ChatType.PRIVATE:
                await log_msg.reply_text(
                    text=f"**RᴇQᴜᴇꜱᴛᴇᴅ ʙʏ :** [{message.from_user.first_name}](tg://user?id={message.from_user.id})\n"
                         f"**Uꜱᴇʀ ɪᴅ :** {message.from_user.id}\n**Fɪʟᴇ ɪᴅ :** {db_id}",
                    disable_web_page_preview=True, parse_mode=ParseMode.MARKDOWN, quote=True)
            else:
                await log_msg.reply_text(
                    text=f"**RᴇQᴜᴇꜱᴛᴇᴅ ʙʏ :** {message.chat.title} \n"
                         f"**Cʜᴀɴɴᴇʟ ɪᴅ :** {message.chat.id}\n**Fɪʟᴇ ɪᴅ :** {db_id}",
                    disable_web_page_preview=True, parse_mode=ParseMode.MARKDOWN, quote=True)

        return log_msg
    except Exception as e:
        logging.error(f"Error in send_file: {e}", exc_info=True)
        return None
