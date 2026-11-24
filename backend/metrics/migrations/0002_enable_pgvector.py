"""Enable the pgvector extension up front.

Stage 3 (AI log analysis + RAG over runbooks) stores embeddings in PostgreSQL,
so the extension is created now, while the schema is young. The migration is a
no-op on SQLite, which is only used for quick local hacking.
"""

from django.db import migrations


def enable_pgvector(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    schema_editor.execute("CREATE EXTENSION IF NOT EXISTS vector")


class Migration(migrations.Migration):
    dependencies = [
        ("metrics", "0001_initial"),
    ]

    operations = [
        # Reverse is a no-op on purpose: other apps may already depend on the extension.
        migrations.RunPython(enable_pgvector, migrations.RunPython.noop),
    ]
