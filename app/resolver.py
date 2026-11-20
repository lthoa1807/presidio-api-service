"""Filter analyzer output and resolve overlapping spans.

Presidio's anonymizer picks spans poorly (longest span unconditionally and
in list order), so we choose deterministically here: longest span first,
then score, then allowlist priority.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence

from app.config import EntityConfig
from app.context_gate import context_ok
from app.models import Span


def _overlaps(span: Span, kept: Iterable[Span]) -> bool:
    return any(span.end > other.start and span.start < other.end for other in kept)


def given_name(text: str, span: Span) -> str | None:
    """Return the given name (last token) of a Vietnamese full-name span."""
    if span.entity_type != "VN_NAME":
        return None
    tokens = text[span.start : span.end].split()
    if len(tokens) < 2:
        return None
    given = tokens[-1]
    return given if len(given) >= 2 else None


def harvest_given_names(
    text: str, spans: Iterable[Span], known: Mapping[str, str] | None = None
) -> dict[str, str]:
    """Map each given name seen in ``spans`` to its full-name spelling."""
    names = dict(known or {})
    for span in spans:
        name = given_name(text, span)
        if name is not None:
            names.setdefault(name, text[span.start : span.end])
    return names


def _given_name_spans(
    text: str,
    accepted: Sequence[Span],
    given_names: Mapping[str, str],
    name_entity: EntityConfig,
) -> list[Span]:
    """Redact standalone occurrences of a given name seen in a full name.

    ``VN_NAME`` needs a surname, so a later reference such as "anh Khang"
    is otherwise left untouched. Matching is case sensitive so that common
    words ("an toàn") are not caught by names like "An".
    """
    inferred: list[Span] = []
    for given, full_name in given_names.items():
        if len(given) < 2:
            continue
        pattern = re.compile(rf"(?<!\w){re.escape(given)}(?!\w)")
        for match in pattern.finditer(text):
            candidate = Span(
                match.start(),
                match.end(),
                "VN_NAME",
                name_entity.score,
                value_key=full_name,
            )
            if _overlaps(candidate, (*accepted, *inferred)):
                continue
            inferred.append(candidate)
    return inferred


def resolve(
    text: str,
    spans: Iterable[Span],
    *,
    allowlist: Sequence[str],
    min_score: float,
    entities_by_name: Mapping[str, EntityConfig],
    known_given_names: Mapping[str, str] | None = None,
) -> list[Span]:
    order = {name: index for index, name in enumerate(allowlist)}

    candidates: list[Span] = []
    for span in spans:
        if span.entity_type not in order:
            continue
        if span.score < min_score:
            continue
        entity = entities_by_name.get(span.entity_type)
        if entity is not None and not context_ok(
            text,
            span.start,
            span.end,
            entity,
            window=entity.context_window,
            after=entity.context_after,
        ):
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
        if not _overlaps(span, accepted):
            accepted.append(span)

    name_entity = entities_by_name.get("VN_NAME")
    if name_entity is not None and "VN_NAME" in order:
        accepted.extend(
            _given_name_spans(
                text,
                accepted,
                harvest_given_names(text, accepted, known_given_names),
                name_entity,
            )
        )

    return sorted(accepted, key=lambda s: s.start)
