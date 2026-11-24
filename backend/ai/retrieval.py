"""Vector search over runbook chunks.

PostgreSQL: `ORDER BY embedding <=> query LIMIT k` — pgvector cosine distance, served by
the HNSW index. SQLite (local hacking only): the same ranking computed in Python.
"""

import math
from dataclasses import dataclass

from django.conf import settings
from django.db import connection
from pgvector.django import CosineDistance

from .models import JobStatus, RunbookChunk
from .providers import LLMProvider


@dataclass(frozen=True)
class Hit:
    chunk: RunbookChunk
    distance: float  # cosine distance: 0 = same direction, 1 = unrelated, 2 = opposite


def cosine_distance(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return 1.0 - dot / norm if norm else 1.0


def search_chunks(
    query_vector: list[float], *, k: int | None = None, max_distance: float | None = None
) -> list[Hit]:
    k = k or settings.RAG_TOP_K
    max_distance = settings.RAG_MAX_DISTANCE if max_distance is None else max_distance
    chunks = RunbookChunk.objects.select_related("runbook").filter(runbook__status=JobStatus.READY)

    if connection.vendor == "postgresql":
        rows = chunks.annotate(distance=CosineDistance("embedding", query_vector)).order_by(
            "distance"
        )[:k]
        hits = [Hit(chunk, float(chunk.distance)) for chunk in rows]
    else:
        hits = sorted(
            (Hit(chunk, cosine_distance(chunk.embedding, query_vector)) for chunk in chunks),
            key=lambda hit: hit.distance,
        )[:k]
    # Nearest neighbours always exist; only reasonably close ones are useful context.
    return [hit for hit in hits if hit.distance <= max_distance]


def search(query: str, provider: LLMProvider, **kwargs) -> list[Hit]:
    if not RunbookChunk.objects.exists():
        return []  # no runbooks yet: skip the embedding call entirely
    [vector] = provider.embed([query])
    return search_chunks(vector, **kwargs)
