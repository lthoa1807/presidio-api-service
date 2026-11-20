"""Service-side context gate.

Presidio's context enhancer relies on English lemmas, so it does not boost
multi-word Vietnamese terms. We check the raw text ourselves instead.
"""

from __future__ import annotations

from app.config import EntityConfig


def context_ok(
    text: str,
    start: int,
    end: int,
    entity: EntityConfig,
    *,
    window: int | None = None,
    after: bool | None = None,
) -> bool:
    if not entity.require_context:
        return True
    if not entity.context:
        return False
    if window is None:
        window = entity.context_window
    if after is None:
        after = entity.context_after
    stop = end + window if after else end
    snippet = text[max(0, start - window) : stop].lower()
    return any(keyword.lower() in snippet for keyword in entity.context)
