"""Recursive JSON walker that collects string leaves under payload limits."""

from __future__ import annotations

import fnmatch
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from app.config import Settings


class PayloadLimitError(Exception):
    def __init__(self, code: str, message: str | None = None) -> None:
        super().__init__(message or code)
        self.code = code


@dataclass(frozen=True)
class Leaf:
    path: str
    tokens: tuple[str | int, ...]
    text: str
    kind: str


def _child_path(base: str, key: str) -> str:
    if key.isidentifier():
        return f"{base}.{key}"
    escaped = key.replace("\\", "\\\\").replace('"', '\\"')
    return f'{base}["{escaped}"]'


def _is_scannable(value: Any, scan_numbers: bool) -> str | None:
    if isinstance(value, str):
        return "str"
    if scan_numbers and isinstance(value, int) and not isinstance(value, bool):
        return "int"
    if scan_numbers and isinstance(value, float):
        return "float"
    return None


def collect_leaves(
    data: Any,
    settings: Settings,
    *,
    scan_numbers: bool = False,
    include_paths: Sequence[str] = (),
    exclude_paths: Sequence[str] = (),
) -> list[Leaf]:
    leaves: list[Leaf] = []
    seen: set[str] = set()
    total_chars = 0
    excluded = {key.lower() for key in settings.exclude_keys}

    def allowed(path: str) -> bool:
        if any(fnmatch.fnmatchcase(path, pattern) for pattern in exclude_paths):
            return False
        return not include_paths or any(
            fnmatch.fnmatchcase(path, pattern) for pattern in include_paths
        )

    def visit(value: Any, tokens: tuple[str | int, ...], path: str, depth: int) -> None:
        nonlocal total_chars

        if isinstance(value, dict):
            if depth > settings.max_depth:
                raise PayloadLimitError("payload_too_deep")
            for key, child in value.items():
                if str(key).lower() in excluded:
                    continue
                visit(child, tokens + (key,), _child_path(path, str(key)), depth + 1)
            return

        if isinstance(value, list):
            if depth > settings.max_depth:
                raise PayloadLimitError("payload_too_deep")
            for index, child in enumerate(value):
                visit(child, tokens + (index,), f"{path}[{index}]", depth + 1)
            return

        kind = _is_scannable(value, scan_numbers)
        if kind is None:
            return

        text = value if isinstance(value, str) else str(value)
        if len(text) < settings.min_text_len:
            return
        if not allowed(path):
            return
        if len(text) > settings.max_text_len:
            raise PayloadLimitError("text_too_long")

        if text not in seen:
            if len(seen) + 1 > settings.max_strings:
                raise PayloadLimitError("too_many_strings")
            total_chars += len(text)
            if total_chars > settings.max_total_chars:
                raise PayloadLimitError("total_text_too_large")
            seen.add(text)

        leaves.append(Leaf(path=path, tokens=tokens, text=text, kind=kind))

    visit(data, (), "$", 1)
    return leaves


def set_value(data: Any, tokens: Sequence[str | int], value: Any) -> None:
    current = data
    for token in tokens[:-1]:
        current = current[token]
    if tokens:
        current[tokens[-1]] = value
