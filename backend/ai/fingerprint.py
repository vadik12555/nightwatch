"""Error fingerprints: one cache entry for all log lines that differ only in variable parts.

"Worker with pid 4242 was terminated" and "Worker with pid 7 was terminated" are the
same problem, so they share one AI explanation (and one LLM call).
"""

import hashlib
import re

_REPLACEMENTS = [
    (
        re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I),
        "<uuid>",
    ),
    (re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}(?::\d+)?\b"), "<ip>"),
    (re.compile(r"\b0x[0-9a-f]+\b", re.I), "<hex>"),
    (re.compile(r"\b(?=[0-9a-f]*\d)[0-9a-f]{8,}\b", re.I), "<hex>"),
    (re.compile(r"\d+"), "<n>"),
    (re.compile(r"\s+"), " "),
]


def normalize(message: str) -> str:
    text = message.strip()[:2000]
    for pattern, replacement in _REPLACEMENTS:
        text = pattern.sub(replacement, text)
    return text.strip()


def fingerprint(source: str, message: str) -> str:
    key = f"{source.strip().lower()}|{normalize(message)}"
    return hashlib.sha256(key.encode()).hexdigest()
