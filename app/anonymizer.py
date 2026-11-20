"""In-process, irreversible replacement of PII spans."""

from __future__ import annotations

import hashlib
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass

from app.models import Span


@dataclass(frozen=True)
class AppliedSpan:
    start: int
    end: int
    entity_type: str
    score: float
    token: str


class PseudonymState:
    """Per-request pseudonym map: same value + entity get the same token."""

    def __init__(self) -> None:
        self._tokens: dict[tuple[str, str], str] = {}
        self._counts: dict[str, int] = defaultdict(int)

    def token(self, entity_type: str, value: str) -> str:
        key = (entity_type, value)
        existing = self._tokens.get(key)
        if existing is not None:
            return existing
        self._counts[entity_type] += 1
        token = f"<{entity_type}_{self._counts[entity_type]}>"
        self._tokens[key] = token
        return token


def _token_for(
    entity_type: str, value: str, mode: str, salt: str, state: PseudonymState
) -> str:
    if mode == "replace":
        return f"<{entity_type}>"
    if mode == "pseudonym":
        return state.token(entity_type, value)
    if mode == "hash":
        digest = hashlib.sha256((salt + "\x00" + value).encode("utf-8")).hexdigest()[
            :16
        ]
        return f"<{entity_type}_{digest}>"
    raise ValueError(f"unsupported mode: {mode}")


def anonymize_text(
    text: str,
    spans: Sequence[Span],
    *,
    mode: str,
    salt: str,
    state: PseudonymState,
) -> tuple[str, list[AppliedSpan]]:
    applied: list[AppliedSpan] = []
    result = text
    # Assign tokens left-to-right so pseudonym numbering follows first
    # occurrence, then apply replacements right-to-left so offsets stay valid.
    ordered = sorted(spans, key=lambda s: s.start)
    prepared = [
        (
            span,
            _token_for(
                span.entity_type,
                span.value_key or text[span.start : span.end],
                mode,
                salt,
                state,
            ),
        )
        for span in ordered
    ]
    for span, token in reversed(prepared):
        result = result[: span.start] + token + result[span.end :]
        applied.append(
            AppliedSpan(
                start=span.start,
                end=span.end,
                entity_type=span.entity_type,
                score=span.score,
                token=token,
            )
        )

    applied.sort(key=lambda item: item.start)
    return result, applied
