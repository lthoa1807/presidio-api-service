"""HTTP client for the running Presidio analyzer service."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from typing import Any

import httpx

from app.models import Span


class AnalyzerError(Exception):
    """Analyzer rejected the request or returned an unusable response."""


class AnalyzerUnavailable(AnalyzerError):
    """Analyzer could not be reached or kept failing after a retry."""


class AnalyzerTimeout(AnalyzerError):
    """Analyzer did not answer in time."""


class AnalyzerClient:
    def __init__(
        self,
        base_url: str,
        *,
        timeout_s: float,
        concurrency: int,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout_s
        self._semaphore = asyncio.Semaphore(max(1, concurrency))
        self._client = client
        self._owns_client = client is None

    async def analyze(
        self,
        text: str,
        *,
        min_score: float,
        entities: Sequence[str],
        recognizers: Sequence[dict[str, Any]],
    ) -> list[Span]:
        async with self._semaphore:
            return await self._analyze(text, min_score, entities, recognizers)

    async def _analyze(
        self,
        text: str,
        min_score: float,
        entities: Sequence[str],
        recognizers: Sequence[dict[str, Any]],
    ) -> list[Span]:
        payload = {
            "text": text,
            "language": "en",
            "score_threshold": min_score,
            "entities": list(entities),
            "ad_hoc_recognizers": list(recognizers),
        }

        last_error: Exception | None = None
        for _ in range(2):
            try:
                response = await self._post("/analyze", payload)
            except httpx.TimeoutException as exc:
                last_error = exc
                continue
            except httpx.TransportError as exc:
                last_error = exc
                continue

            if response.status_code >= 500:
                last_error = AnalyzerUnavailable(
                    f"analyzer returned {response.status_code}"
                )
                continue
            if response.status_code >= 400:
                raise AnalyzerError(f"analyzer returned {response.status_code}")

            try:
                payload_json = response.json()
                return [
                    Span(
                        start=int(item["start"]),
                        end=int(item["end"]),
                        entity_type=str(item["entity_type"]),
                        score=float(item["score"]),
                    )
                    for item in payload_json
                ]
            except (ValueError, KeyError, TypeError) as exc:
                raise AnalyzerError("invalid analyzer response") from exc

        if isinstance(last_error, (httpx.TimeoutException, httpx.TransportError)):
            raise AnalyzerTimeout("analyzer request failed") from last_error
        raise AnalyzerUnavailable("analyzer unavailable") from last_error

    async def _post(self, path: str, payload: dict[str, Any]) -> httpx.Response:
        if self._client is None:
            self._client = httpx.AsyncClient()
        return await self._client.post(
            f"{self._base_url}{path}", json=payload, timeout=self._timeout
        )

    async def health(self) -> bool:
        client = self._client
        temporary = client is None
        if client is None:
            client = httpx.AsyncClient()
        try:
            response = await client.get(f"{self._base_url}/health", timeout=1.0)
            return 200 <= response.status_code < 300
        except httpx.HTTPError:
            return False
        finally:
            if temporary:
                await client.aclose()

    async def aclose(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None
