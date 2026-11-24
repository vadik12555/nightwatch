"""Telegram bot (aiogram 3, long polling). Started by `python manage.py run_bot`.

Handlers only translate Telegram updates into calls of `services` (sync Django ORM,
wrapped with sync_to_async so the event loop is never blocked by the database).
"""

import logging

from aiogram import Dispatcher, Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import BotCommand, Message
from asgiref.sync import sync_to_async

from . import services, telegram

logger = logging.getLogger(__name__)

router = Router(name="nightwatch")

COMMANDS = [
    BotCommand(command="status", description="Сводка: серверы и активные алерты"),
    BotCommand(command="servers", description="Серверы с CPU / RAM / диском"),
    BotCommand(command="unlink", description="Отвязать этот чат"),
    BotCommand(command="help", description="Справка"),
]


def _chat_title(message: Message) -> str:
    user = message.from_user
    if user is None:
        return message.chat.title or ""
    return " ".join(filter(None, [user.first_name, user.last_name]))


@router.message(CommandStart())
async def cmd_start(message: Message, command: CommandObject) -> None:
    code = (command.args or "").strip()
    if code:
        username = message.from_user.username if message.from_user else ""
        reply = await sync_to_async(services.link_chat)(
            code, message.chat.id, username or "", _chat_title(message)
        )
    else:
        reply = await sync_to_async(services.start_without_code)(message.chat.id)
    await message.answer(reply)


@router.message(Command("status"))
async def cmd_status(message: Message) -> None:
    await message.answer(await sync_to_async(services.status_text)(message.chat.id))


@router.message(Command("servers"))
async def cmd_servers(message: Message) -> None:
    await message.answer(await sync_to_async(services.servers_text)(message.chat.id))


@router.message(Command("unlink"))
async def cmd_unlink(message: Message) -> None:
    await message.answer(await sync_to_async(services.unlink_chat)(message.chat.id))


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(await sync_to_async(services.help_text)(message.chat.id))


def build_dispatcher() -> Dispatcher:
    dispatcher = Dispatcher()
    dispatcher.include_router(router)
    return dispatcher


async def run_polling() -> None:
    bot = telegram.make_bot()
    me = await bot.get_me()
    logger.info("Starting Telegram bot @%s", me.username)
    await bot.set_my_commands(COMMANDS)
    await build_dispatcher().start_polling(bot)
