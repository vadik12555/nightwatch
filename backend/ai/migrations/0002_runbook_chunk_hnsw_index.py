"""HNSW index for cosine similarity search over runbook chunks (PostgreSQL + pgvector).

HNSW gives fast approximate nearest-neighbour search without a training step
(unlike IVFFlat), so it works well from the very first inserted row.
On SQLite (local hacking) the index is skipped and search falls back to Python.
"""

from django.db import migrations

INDEX = "ai_runbookchunk_embedding_hnsw"


def create_index(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    schema_editor.execute(
        f"CREATE INDEX IF NOT EXISTS {INDEX} ON ai_runbookchunk "
        "USING hnsw (embedding vector_cosine_ops)"
    )


def drop_index(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    schema_editor.execute(f"DROP INDEX IF EXISTS {INDEX}")


class Migration(migrations.Migration):
    dependencies = [
        ("ai", "0001_initial"),
        ("metrics", "0002_enable_pgvector"),  # the vector extension must exist first
    ]

    operations = [migrations.RunPython(create_index, drop_index)]
