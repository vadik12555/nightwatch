"""Run the Telegram bot (long polling): `python manage.py run_bot`.

Without TELEGRAM_BOT_TOKEN the command idles instead of exiting, so the `bot`
container in docker compose stays up quietly until a token is configured.
"""

import asyncio
import logging
import time

from django.conf import settings
from django.core.management.base import BaseCommand

from notifications.bot import run_polling


class Command(BaseCommand):
    help = "Run the NightWatch Telegram bot (long polling)."

    def handle(self, *args, **options):
        if not settings.TELEGRAM_BOT_TOKEN:
            self.stderr.write(
                "TELEGRAM_BOT_TOKEN is not set — the Telegram bot is disabled. "
                "Set it in .env and restart the 'bot' service."
            )
            while True:
                time.sleep(3600)

        logging.getLogger("aiogram").setLevel(logging.INFO)
        asyncio.run(run_polling())
