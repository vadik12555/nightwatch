"""Import Markdown runbooks from a directory and queue their indexing.

    python manage.py load_runbooks                 # bundled examples (ai/sample_runbooks)
    python manage.py load_runbooks /path/to/docs   # your own *.md files

A runbook with the same file name is updated (and re-indexed) only if its text changed.
"""

from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from ai import services, tasks
from ai.models import JobStatus, Runbook

SAMPLES_DIR = Path(__file__).resolve().parents[2] / "sample_runbooks"


class Command(BaseCommand):
    help = "Load Markdown runbooks from a directory and index them for the AI chat."

    def add_arguments(self, parser):
        parser.add_argument("directory", nargs="?", default=str(SAMPLES_DIR))

    def handle(self, *args, directory, **options):
        path = Path(directory)
        if not path.is_dir():
            raise CommandError(f"Not a directory: {path}")

        created = updated = 0
        for file in sorted(path.glob("*.md")):
            content = file.read_text(encoding="utf-8")
            title = services.title_from_markdown(content, file.stem)
            runbook = Runbook.objects.filter(source_name=file.name).first()
            if runbook is None:
                runbook = services.create_runbook(
                    title=title, content=content, source_name=file.name
                )
                created += 1
            elif runbook.content != content or runbook.status == JobStatus.FAILED:
                runbook.title, runbook.content, runbook.status = title, content, JobStatus.PENDING
                runbook.save()
                updated += 1
            else:
                continue
            tasks.index_runbook.delay(runbook.pk)
            self.stdout.write(f"  queued: {file.name}")

        self.stdout.write(self.style.SUCCESS(f"Runbooks: {created} created, {updated} updated."))
