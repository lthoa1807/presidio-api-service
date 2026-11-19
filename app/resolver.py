"""Filter analyzer output and resolve overlapping spans.

Presidio's anonymizer picks spans poorly (longest span unconditionally and
in list order), so we choose deterministically here: longest span first,
then score, then allowlist priority.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence

from app.config import EntityConfig
from app.context_gate import context_ok
from app.models import Span


def resolve(
    text: str,
    spans: Iterable[Span],
    *,
    allowlist: Sequence[str],
    min_score: float,
    entities_by_name: Mapping[str, EntityConfig],
) -> list[Span]:
    order = {name: index for index, name in enumerate(allowlist)}

    candidates: list[Span] = []
    for span in spans:
        if span.entity_type not in order:
            continue
        if span.score < min_score:
            continue
        entity = entities_by_name.get(span.entity_type)
        if entity is not None and not context_ok(text, span.start, span.end, entity):
            continue
        candidates.append(span)

    candidates.sort(
        key=lambda s: (
            -(s.end - s.start),
            -s.score,
            order[s.entity_type],
            s.start,
        )
    )

    accepted: list[Span] = []
    for span in candidates:
        if all(
            span.end <= kept.start or span.start >= kept.end for kept in accepted
        ):
            accepted.append(span)

    return sorted(accepted, key=lambda s: s.start)
