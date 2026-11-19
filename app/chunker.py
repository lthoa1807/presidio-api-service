"""Split long text into analyzer-sized chunks on sentence-ish boundaries."""

from __future__ import annotations

_BOUNDARIES = (".", "!", "?", "\n")
# Sentence ends often land just before the last fifth of a chunk, so search a
# slightly wider tail than the spec's 20% to actually prefer them. See ledger
# ruling for Task 3.
_TAIL_RATIO = 0.3


def chunk_text(text: str, *, chunk_size: int, overlap: int) -> list[tuple[int, str]]:
    if not text:
        return []
    if len(text) <= chunk_size:
        return [(0, text)]

    chunks: list[tuple[int, str]] = []
    start = 0
    length = len(text)
    tail = max(1, int(chunk_size * _TAIL_RATIO))

    while start < length:
        end = min(start + chunk_size, length)
        if end < length:
            window_start = max(start, end - tail)
            window = text[window_start:end]
            cut = max(window.rfind(mark) for mark in _BOUNDARIES)
            if cut >= 0:
                end = window_start + cut + 1

        chunks.append((start, text[start:end]))
        if end >= length:
            break

        next_start = end - overlap
        if next_start <= start:
            next_start = start + 1
        start = next_start

    return chunks
