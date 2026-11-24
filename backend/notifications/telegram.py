"""Sending messages through the Telegram Bot API from synchronous code (Celery tasks).

aiogram is async; a Celery task runs one short event loop per notification batch.
"""

import asyncio
import logging

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramAPIError, TelegramRetryAfter
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from django.conf import settings

logger = logging.getLogger(__name__)


def is_configured() -> bool:
    return bool(settings.TELEGRAM_BOT_TOKEN)


def make_bot() -> Bot:
    return Bot(
        settings.TELEGRAM_BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML, link_preview_is_disabled=True),
    )


def link_button(url: str | None, text: str = "Открыть в NightWatch") -> InlineKeyboardMarkup | None:
    if not url:
        return None
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=text, url=url)]])


async def _send_all(
    chat_ids: list[int], text: str, markup: InlineKeyboardMarkup | None
) -> dict[int, Exception | None]:
    results: dict[int, Exception | None] = {}
    async with make_bot() as bot:
        for chat_id in chat_ids:
            for attempt in range(2):
                try:
                    await bot.send_message(chat_id, text, reply_markup=markup)
                    results[chat_id] = None
                    break
                except TelegramRetryAfter as exc:  # flood control: Telegram says how long to wait
                    if attempt:
                        results[chat_id] = exc
                    else:
                        await asyncio.sleep(min(exc.retry_after, 30))
                except TelegramAPIError as exc:
                    results[chat_id] = exc
                    break
    return results


def send_messages(
    chat_ids: list[int], text: str, url: str | None = None
) -> dict[int, Exception | None]:
    """Send one text to several chats. Returns {chat_id: None | error} — never raises API errors."""
    if not chat_ids:
        return {}
    return asyncio.run(_send_all(chat_ids, text, link_button(url)))
