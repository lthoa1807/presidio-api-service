"""Orchestrate walking, chunked analysis, resolution and anonymization."""

from __future__ import annotations

import json
import logging
import secrets
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from app.anonymizer import PseudonymState, anonymize_text
from app.chunker import chunk_text
from app.config import EntityConfig, Settings
from app.models import Span
from app.recognizers import to_ad_hoc_recognizers
from app.resolver import resolve
from app.walker import Leaf, collect_leaves, set_value

logger = logging.getLogger(__name__)

_RANDOM_SALT = secrets.token_hex(16)
_MAX_JSON_DEPTH = 3


@dataclass
class ProcessOptions:
    mode: str = "replace"
    min_score: float = 0.5
    scan_numbers: bool = False


@dataclass(frozen=True)
class Finding:
    path: str
    entity_type: str
    start: int
    end: int
    score: float
    token: str


@dataclass
class ProcessResult:
    data: Any
    findings: list[Finding]
    stats: dict[str, Any]


@dataclass
class _Context:
    settings: Settings
    options: ProcessOptions
    analyzer: Any
    allowlist: Sequence[str]
    recognizers: list[dict[str, Any]]
    entities_by_name: dict[str, EntityConfig]
    salt: str
    state: PseudonymState = field(default_factory=PseudonymState)
    cache: dict[str, list[Span]] = field(default_factory=dict)
    findings: list[Finding] = field(default_factory=list)
    by_type: Counter = field(default_factory=Counter)
    scanned: int = 0
    excluded_keys: frozenset[str] = frozenset()


def _child_path(base: str, key: str) -> str:
    if key.isidentifier():
        return f"{base}.{key}"
    escaped = key.replace("\\", "\\\\").replace('"', '\\"')
    return f'{base}["{escaped}"]'


async def _analyze_value(ctx: _Context, text: str) -> list[Span]:
    cached = ctx.cache.get(text)
    if cached is not None:
        return cached

    collected: list[Span] = []
    for start, chunk in chunk_text(
        text,
        chunk_size=ctx.settings.chunk_size,
        overlap=ctx.settings.chunk_overlap,
    ):
        spans = await ctx.analyzer.analyze(
            chunk,
            min_score=ctx.options.min_score,
            entities=ctx.allowlist,
            recognizers=ctx.recognizers,
        )
        collected.extend(
            Span(
                start=span.start + start,
                end=span.end + start,
                entity_type=span.entity_type,
                score=span.score,
            )
            for span in spans
        )

    resolved = resolve(
        text,
        collected,
        allowlist=ctx.allowlist,
        min_score=ctx.options.min_score,
        entities_by_name=ctx.entities_by_name,
    )
    ctx.cache[text] = resolved
    return resolved


async def _transform_text(ctx: _Context, text: str, path: str, depth: int) -> str:
    stripped = text.strip()
    if (
        ctx.settings.recurse_json_strings
        and depth < _MAX_JSON_DEPTH
        and stripped[:1] in "{["
    ):
        try:
            parsed = json.loads(text)
        except ValueError:
            parsed = None
        if isinstance(parsed, (dict, list)):
            changed = await _process_inner(ctx, parsed, path, "$", depth + 1)
            if changed:
                return json.dumps(parsed, ensure_ascii=False)
            return text

    ctx.scanned += 1
    spans = await _analyze_value(ctx, text)
    if not spans:
        return text

    new_text, applied = anonymize_text(
        text,
        spans,
        mode=ctx.options.mode,
        salt=ctx.salt,
        state=ctx.state,
    )
    for item in applied:
        ctx.findings.append(
            Finding(
                path=path,
                entity_type=item.entity_type,
                start=item.start,
                end=item.end,
                score=item.score,
                token=item.token,
            )
        )
        ctx.by_type[item.entity_type] += 1
    return new_text


async def _process_inner(
    ctx: _Context, value: Any, outer_path: str, inner_path: str, depth: int
) -> bool:
    settings = ctx.settings
    changed = False

    if isinstance(value, dict):
        for key, child in list(value.items()):
            if str(key).lower() in ctx.excluded_keys:
                continue
            child_path = _child_path(inner_path, str(key))
            if isinstance(child, (dict, list)):
                if await _process_inner(ctx, child, outer_path, child_path, depth):
                    changed = True
            elif isinstance(child, str) and len(child) >= settings.min_text_len:
                new_value = await _transform_text(
                    ctx, child, f"{outer_path}#{child_path}", depth
                )
                if new_value != child:
                    value[key] = new_value
                    changed = True
        return changed

    if isinstance(value, list):
        for index, child in enumerate(list(value)):
            child_path = f"{inner_path}[{index}]"
            if isinstance(child, (dict, list)):
                if await _process_inner(ctx, child, outer_path, child_path, depth):
                    changed = True
            elif isinstance(child, str) and len(child) >= settings.min_text_len:
                new_value = await _transform_text(
                    ctx, child, f"{outer_path}#{child_path}", depth
                )
                if new_value != child:
                    value[index] = new_value
                    changed = True
    return changed


async def process_payload(
    data: Any,
    options: ProcessOptions,
    settings: Settings,
    analyzer: Any,
    *,
    include_paths: Sequence[str] = (),
    exclude_paths: Sequence[str] = (),
) -> ProcessResult:
    salt = settings.anonymize_salt
    if options.mode == "hash" and not salt:
        salt = _RANDOM_SALT
        logger.warning(
            "PII_ANONYMIZE_SALT is empty; hashes are stable only until restart"
        )

    ctx = _Context(
        settings=settings,
        options=options,
        analyzer=analyzer,
        allowlist=settings.entity_priority,
        recognizers=to_ad_hoc_recognizers(settings.entities),
        entities_by_name={entity.name: entity for entity in settings.entities},
        salt=salt,
        excluded_keys=frozenset(key.lower() for key in settings.exclude_keys),
    )

    leaves: list[Leaf] = collect_leaves(
        data,
        settings,
        scan_numbers=options.scan_numbers,
        include_paths=include_paths,
        exclude_paths=exclude_paths,
    )

    for leaf in leaves:
        new_value = await _transform_text(ctx, leaf.text, leaf.path, 0)
        if new_value != leaf.text:
            set_value(data, leaf.tokens, new_value)

    stats = {
        "strings_scanned": ctx.scanned,
        "unique_strings": len(ctx.cache),
        "entities_found": len(ctx.findings),
        "by_type": dict(ctx.by_type),
    }
    return ProcessResult(data=data, findings=ctx.findings, stats=stats)
