"""Shared data models."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Span:
    start: int
    end: int
    entity_type: str
    score: float
    # Value used as the pseudonym/hash key when it differs from the text at
    # ``start:end`` (e.g. a standalone given name that belongs to a full name).
    value_key: str | None = None
