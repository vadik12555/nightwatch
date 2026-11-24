"""Create demo servers with a week of plausible history so the UI looks alive right away.

python manage.py seed_demo            # create demo fleet (skips if it already exists)
python manage.py seed_demo --reset    # drop demo servers and recreate them
python manage.py seed_demo --if-empty # only when the database has no servers at all
python manage.py seed_demo --follow   # keep sending live reports every 15 s (no Celery)
"""

import time

from django.core.management.base import BaseCommand

from metrics import demo
from servers.models import Server


class Command(BaseCommand):
    help = "Create demo servers with realistic metrics, services and logs."

    def add_arguments(self, parser):
        parser.add_argument("--reset", action="store_true", help="delete demo servers first")
        parser.add_argument(
            "--if-empty", action="store_true", help="do nothing if any server exists"
        )
        parser.add_argument(
            "--follow",
            action="store_true",
            help="after seeding, emit live demo reports every 15 seconds until Ctrl+C",
        )

    def handle(self, *args, reset=False, if_empty=False, follow=False, **options):
        if if_empty and Server.objects.exists():
            self.stdout.write("Servers already exist — skipping demo seed.")
        elif reset or not demo.demo_queryset().exists():
            if reset:
                deleted = demo.demo_queryset().count()
                demo.demo_queryset().delete()
                self.stdout.write(f"Deleted {deleted} demo server(s).")
            servers = demo.seed()
            for server in servers:
                self.stdout.write(
                    f"  {server.name:<16} {server.snapshots.count():>5} snapshots, "
                    f"{server.logs.count():>4} logs, {server.services.count()} services"
                )
            self.stdout.write(self.style.SUCCESS(f"Created {len(servers)} demo servers."))
        else:
            self.stdout.write("Demo servers already exist (use --reset to recreate).")

        if follow:
            self.stdout.write("Sending live demo reports every 15 s. Ctrl+C to stop.")
            try:
                while True:
                    sent = demo.tick()
                    self.stdout.write(f"  tick: {sent} report(s)")
                    time.sleep(15)
            except KeyboardInterrupt:
                self.stdout.write("Stopped.")
