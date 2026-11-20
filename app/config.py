"""Configuration loading for the PII API.

Canonical environment variables use the ``PII_`` prefix. Bare names are
accepted for backwards compatibility with earlier compose files but are
always overridden by the prefixed form and log a warning.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

DEFAULT_CONFIG_PATH = "app/config/entities.yaml"

_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}


@dataclass(frozen=True)
class EntityConfig:
    name: str
    regex: str
    score: float
    require_context: bool
    context: tuple[str, ...]
    enabled: bool = True
    context_window: int = 60
    context_after: bool = True


@dataclass(frozen=True)
class Settings:
    analyzer_url: str = "http://presidio-analyzer:3000"
    analyzer_timeout_s: float = 10.0
    analyzer_concurrency: int = 2
    request_deadline_s: float = 30.0
    max_body_bytes: int = 5_242_880
    max_strings: int = 500
    max_total_chars: int = 512_000
    max_text_len: int = 64_000
    chunk_size: int = 4_000
    chunk_overlap: int = 200
    min_text_len: int = 3
    max_depth: int = 32
    default_min_score: float = 0.5
    recurse_json_strings: bool = True
    anonymize_salt: str = ""
    api_key: str = ""
    log_level: str = "info"
    config_path: Path = Path(DEFAULT_CONFIG_PATH)
    exclude_keys: frozenset[str] = frozenset()
    entity_priority: tuple[str, ...] = ()
    entities: tuple[EntityConfig, ...] = ()


def _read_env(env: Mapping[str, str], name: str) -> str | None:
    prefixed = f"PII_{name}"
    if prefixed in env:
        return env[prefixed]
    if name in env:
        logger.warning(
            "environment variable %s is deprecated; use %s instead", name, prefixed
        )
        return env[name]
    return None


def _as_int(value: str, name: str) -> int:
    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(f"invalid integer for {name}: {value!r}") from exc


def _as_float(value: str, name: str) -> float:
    try:
        return float(value)
    except ValueError as exc:
        raise ValueError(f"invalid float for {name}: {value!r}") from exc


def _as_bool(value: str, name: str) -> bool:
    lowered = value.strip().lower()
    if lowered in _TRUE:
        return True
    if lowered in _FALSE:
        return False
    raise ValueError(f"invalid boolean for {name}: {value!r}")


def load_entities(
    path: Path,
) -> tuple[tuple[EntityConfig, ...], tuple[str, ...], frozenset[str]]:
    with Path(path).open("r", encoding="utf-8") as handle:
        data: dict[str, Any] = yaml.safe_load(handle) or {}

    entities = tuple(
        EntityConfig(
            name=item["name"],
            regex=item["regex"],
            score=float(item.get("score", 0.5)),
            require_context=bool(item.get("require_context", False)),
            context=tuple(item.get("context") or ()),
            enabled=bool(item.get("enabled", True)),
            context_window=int(item.get("context_window", 60)),
            context_after=bool(item.get("context_after", True)),
        )
        for item in data.get("entities", [])
    )
    priority = tuple(data.get("entity_priority", []))
    exclude_keys = frozenset(data.get("exclude_keys", []))
    return entities, priority, exclude_keys


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    source: Mapping[str, str] = os.environ if env is None else env

    def text(name: str, default: str) -> str:
        value = _read_env(source, name)
        return default if value is None else value

    config_path = Path(text("CONFIG_PATH", DEFAULT_CONFIG_PATH))
    entities, priority, exclude_keys = load_entities(config_path)

    timeout_ms = text("ANALYZER_TIMEOUT_MS", "10000")
    deadline_ms = text("REQUEST_DEADLINE_MS", "30000")
    min_score = text("DEFAULT_MIN_SCORE", "0.5")

    settings = Settings(
        analyzer_url=text("ANALYZER_URL", "http://presidio-analyzer:3000"),
        analyzer_timeout_s=_as_float(timeout_ms, "ANALYZER_TIMEOUT_MS") / 1000.0,
        analyzer_concurrency=_as_int(
            text("ANALYZER_CONCURRENCY", "2"), "ANALYZER_CONCURRENCY"
        ),
        request_deadline_s=_as_float(deadline_ms, "REQUEST_DEADLINE_MS") / 1000.0,
        max_body_bytes=_as_int(text("MAX_BODY_BYTES", "5242880"), "MAX_BODY_BYTES"),
        max_strings=_as_int(text("MAX_STRINGS", "500"), "MAX_STRINGS"),
        max_total_chars=_as_int(text("MAX_TOTAL_CHARS", "512000"), "MAX_TOTAL_CHARS"),
        max_text_len=_as_int(text("MAX_TEXT_LEN", "64000"), "MAX_TEXT_LEN"),
        chunk_size=_as_int(text("CHUNK_SIZE", "4000"), "CHUNK_SIZE"),
        chunk_overlap=_as_int(text("CHUNK_OVERLAP", "200"), "CHUNK_OVERLAP"),
        min_text_len=_as_int(text("MIN_TEXT_LEN", "3"), "MIN_TEXT_LEN"),
        max_depth=_as_int(text("MAX_DEPTH", "32"), "MAX_DEPTH"),
        default_min_score=_as_float(min_score, "DEFAULT_MIN_SCORE"),
        recurse_json_strings=_as_bool(
            text("RECURSE_JSON_STRINGS", "true"), "RECURSE_JSON_STRINGS"
        ),
        anonymize_salt=text("ANONYMIZE_SALT", ""),
        api_key=text("API_KEY", ""),
        log_level=text("LOG_LEVEL", "info"),
        config_path=config_path,
        exclude_keys=exclude_keys,
        entity_priority=priority,
        entities=entities,
    )
    return settings
