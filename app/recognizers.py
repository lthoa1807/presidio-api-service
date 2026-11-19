"""Build Presidio ad-hoc recognizers from the local entity configuration."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from app.config import EntityConfig


def _recognizer_name(entity_name: str) -> str:
    return "Vn" + entity_name.title().replace("_", "")


def to_ad_hoc_recognizers(entities: Sequence[EntityConfig]) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for entity in entities:
        if not entity.enabled:
            continue
        recognizer = grouped.get(entity.name)
        if recognizer is None:
            recognizer = {
                "name": _recognizer_name(entity.name),
                "supported_language": "en",
                "supported_entity": entity.name,
                "patterns": [],
                "context": [],
            }
            grouped[entity.name] = recognizer

        recognizer["patterns"].append(
            {
                "name": f"{entity.name.lower()}_{len(recognizer['patterns'])}",
                "regex": entity.regex,
                "score": entity.score,
            }
        )
        for keyword in entity.context:
            if keyword not in recognizer["context"]:
                recognizer["context"].append(keyword)

    return list(grouped.values())
