"""FastAPI surface for the PII anonymization service."""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import asdict
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.analyzer_client import (
    AnalyzerClient,
    AnalyzerError,
    AnalyzerTimeout,
    AnalyzerUnavailable,
)
from app.config import Settings, load_settings
from app.pipeline import ProcessOptions, process_payload
from app.walker import PayloadLimitError

logger = logging.getLogger(__name__)

_MODES = {"replace", "pseudonym", "hash"}
_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}


def _error(
    status: int, code: str, message: str, request_id: str | None = None
) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"error": {"code": code, "message": message, "request_id": request_id}},
    )


def _bool_param(raw: str | None, default: bool) -> bool | None:
    if raw is None:
        return default
    lowered = raw.strip().lower()
    if lowered in _TRUE:
        return True
    if lowered in _FALSE:
        return False
    return None


def _request_id(request: Request) -> str:
    return request.headers.get("x-request-id") or str(uuid.uuid4())


async def _read_body(request: Request, settings: Settings, request_id: str):
    content_type = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if content_type != "application/json":
        return None, _error(
            415, "unsupported_media_type", "content-type must be application/json", request_id
        )

    declared = request.headers.get("content-length")
    if declared is not None:
        try:
            if int(declared) > settings.max_body_bytes:
                return None, _error(413, "payload_too_large", "body too large", request_id)
        except ValueError:
            return None, _error(400, "invalid_json", "invalid content-length", request_id)

    body = await request.body()
    if len(body) > settings.max_body_bytes:
        return None, _error(413, "payload_too_large", "body too large", request_id)

    try:
        return json.loads(body), None
    except ValueError:
        return None, _error(400, "invalid_json", "body is not valid JSON", request_id)


def create_app(
    settings: Settings | None = None, *, analyzer: Any | None = None
) -> FastAPI:
    settings = settings or load_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        owned = app.state.analyzer is None
        if owned:
            app.state.analyzer = AnalyzerClient(
                settings.analyzer_url,
                timeout_s=settings.analyzer_timeout_s,
                concurrency=settings.analyzer_concurrency,
            )
        warmup = asyncio.create_task(app.state.analyzer.health())
        try:
            yield
        finally:
            if not warmup.done():
                warmup.cancel()
            if owned:
                await app.state.analyzer.aclose()

    app = FastAPI(title="PII API", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings
    app.state.analyzer = analyzer
    health_cache: dict[str, Any] = {"at": 0.0, "ok": False}

    def _options(request: Request):
        query = request.query_params
        mode = query.get("mode", "replace")
        if mode not in _MODES:
            return None, f"mode must be one of {sorted(_MODES)}"

        raw_score = query.get("min_score")
        try:
            min_score = (
                settings.default_min_score if raw_score is None else float(raw_score)
            )
        except ValueError:
            return None, "min_score must be a number"
        if not 0.0 <= min_score <= 1.0:
            return None, "min_score must be between 0 and 1"

        if query.get("language", "en") != "en":
            return None, "only language=en is supported"

        scan_numbers = _bool_param(query.get("scan_numbers"), False)
        if scan_numbers is None:
            return None, "scan_numbers must be a boolean"

        include_findings = _bool_param(query.get("include_findings"), False)
        if include_findings is None:
            return None, "include_findings must be a boolean"

        return (
            ProcessOptions(
                mode=mode, min_score=min_score, scan_numbers=scan_numbers
            ),
            include_findings,
        )

    async def _handle(request: Request, detect_only: bool) -> JSONResponse:
        request_id = _request_id(request)

        if settings.api_key:
            provided = request.headers.get("x-api-key", "")
            if not hmac.compare_digest(provided, settings.api_key):
                return _error(401, "unauthorized", "invalid api key", request_id)

        parsed = _options(request)
        if parsed[0] is None:
            return _error(422, "invalid_param", parsed[1], request_id)
        options, include_findings = parsed

        data, error = await _read_body(request, settings, request_id)
        if error is not None:
            return error

        started = time.monotonic()
        try:
            result = await asyncio.wait_for(
                process_payload(data, options, settings, request.app.state.analyzer),
                timeout=settings.request_deadline_s,
            )
        except asyncio.TimeoutError:
            return _error(504, "deadline_exceeded", "request deadline exceeded", request_id)
        except PayloadLimitError as exc:
            return _error(413, exc.code, "payload limit exceeded", request_id)
        except AnalyzerTimeout:
            return _error(504, "analyzer_timeout", "analyzer timeout", request_id)
        except AnalyzerUnavailable:
            return _error(
                503, "analyzer_unavailable", "analyzer unavailable", request_id
            )
        except AnalyzerError:
            return _error(502, "analyzer_error", "analyzer error", request_id)

        elapsed_ms = int((time.monotonic() - started) * 1000)
        logger.info(
            "request_id=%s scanned=%s entities=%s elapsed_ms=%s",
            request_id,
            result.stats["strings_scanned"],
            result.stats["entities_found"],
            elapsed_ms,
        )

        headers = {
            "X-Request-Id": request_id,
            "X-PII-Entities": str(result.stats["entities_found"]),
        }
        if detect_only:
            return JSONResponse(
                content={
                    "findings": [asdict(finding) for finding in result.findings],
                    "stats": result.stats,
                },
                headers=headers,
            )
        if include_findings:
            return JSONResponse(
                content={
                    "data": result.data,
                    "findings": [asdict(finding) for finding in result.findings],
                    "stats": result.stats,
                },
                headers=headers,
            )
        return JSONResponse(content=result.data, headers=headers)

    @app.post("/v1/anonymize")
    async def anonymize(request: Request) -> JSONResponse:
        return await _handle(request, detect_only=False)

    @app.post("/v1/detect")
    async def detect(request: Request) -> JSONResponse:
        return await _handle(request, detect_only=True)

    @app.get("/healthz")
    async def healthz(request: Request) -> JSONResponse:
        now = time.monotonic()
        if now - health_cache["at"] > 5.0:
            health_cache["ok"] = await request.app.state.analyzer.health()
            health_cache["at"] = now
        if health_cache["ok"]:
            return JSONResponse(content={"status": "ok", "analyzer": "ok"})
        return JSONResponse(
            status_code=503,
            content={"status": "degraded", "analyzer": "unreachable"},
        )

    return app


app = create_app()
