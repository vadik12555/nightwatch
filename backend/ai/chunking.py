"""Markdown → chunks for RAG.

Runbooks are split along their headings first (a section usually answers one
question), long sections are split by paragraphs with a small overlap so that a
sentence on the border is not lost. Fenced code blocks are never cut in half.
Every chunk remembers its heading path ("PostgreSQL / Диск переполнен"), which is
prepended to the text before embedding — it carries most of the meaning.
"""

import re
from dataclasses import dataclass

HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
FENCE = re.compile(r"^\s*(```|~~~)")

MAX_CHARS = 1200
OVERLAP_CHARS = 200


@dataclass(frozen=True)
class Chunk:
    heading: str
    text: str

    @property
    def embed_text(self) -> str:
        return f"{self.heading}\n\n{self.text}" if self.heading else self.text


def _sections(markdown: str) -> list[tuple[str, str]]:
    """[(heading path, body)] in document order."""
    sections: list[tuple[str, str]] = []
    stack: list[tuple[int, str]] = []
    body: list[str] = []
    in_code = False

    def flush() -> None:
        text = "\n".join(body).strip()
        if text:
            sections.append((" / ".join(title for _, title in stack), text))
        body.clear()

    for line in markdown.splitlines():
        if FENCE.match(line):
            in_code = not in_code
        match = None if in_code else HEADING.match(line)
        if match:
            flush()
            level = len(match.group(1))
            stack = [(lvl, title) for lvl, title in stack if lvl < level]
            stack.append((level, match.group(2).strip()))
        else:
            body.append(line)
    flush()
    return sections


def _paragraphs(text: str) -> list[str]:
    """Blank-line separated blocks; a fenced code block stays one block."""
    blocks: list[str] = []
    current: list[str] = []
    in_code = False
    for line in text.splitlines():
        if FENCE.match(line):
            in_code = not in_code
        if not line.strip() and not in_code:
            if current:
                blocks.append("\n".join(current))
                current = []
            continue
        current.append(line)
    if current:
        blocks.append("\n".join(current))
    return blocks


def _hard_split(text: str, max_chars: int, overlap: int) -> list[str]:
    step = max_chars - overlap
    return [text[i : i + max_chars] for i in range(0, len(text), step)]


def split_markdown(
    markdown: str, max_chars: int = MAX_CHARS, overlap: int = OVERLAP_CHARS
) -> list[Chunk]:
    chunks: list[Chunk] = []
    for heading, body in _sections(markdown):
        if len(body) <= max_chars:
            chunks.append(Chunk(heading, body))
            continue

        pieces: list[str] = []
        for block in _paragraphs(body):
            pieces.extend(
                _hard_split(block, max_chars, overlap) if len(block) > max_chars else [block]
            )

        current: list[str] = []
        for piece in pieces:
            if current and len("\n\n".join([*current, piece])) > max_chars:
                chunks.append(Chunk(heading, "\n\n".join(current)))
                # Overlap: carry the last short paragraph into the next chunk.
                tail = current[-1]
                current = [tail] if len(tail) <= overlap else []
            current.append(piece)
        if current:
            chunks.append(Chunk(heading, "\n\n".join(current)))
    return chunks
