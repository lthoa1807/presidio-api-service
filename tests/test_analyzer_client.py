import httpx
import pytest

from app.analyzer_client import (
    AnalyzerClient,
    AnalyzerError,
    AnalyzerTimeout,
    AnalyzerUnavailable,
)


def transport(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_success_maps_response():
    def h(req):
        return httpx.Response(
            200,
            json=[{"start": 0, "end": 4, "entity_type": "VN_NAME", "score": 0.9}],
        )

    c = AnalyzerClient("http://x", timeout_s=1, concurrency=2, client=transport(h))
    spans = await c.analyze("abcd", min_score=0.5, entities=["VN_NAME"], recognizers=[])
    assert spans[0].entity_type == "VN_NAME"
    assert spans[0].start == 0 and spans[0].end == 4


async def test_request_body_shape():
    seen = {}

    def h(req):
        seen["body"] = req.content
        return httpx.Response(200, json=[])

    c = AnalyzerClient("http://x", timeout_s=1, concurrency=1, client=transport(h))
    await c.analyze("abcd", min_score=0.7, entities=["VN_NAME"], recognizers=[{"a": 1}])
    body = seen["body"].decode()
    assert '"language":"en"' in body
    assert '"score_threshold":0.7' in body
    assert '"ad_hoc_recognizers":[{"a":1}]' in body


async def test_5xx_retries_once_then_unavailable():
    calls = []

    def h(req):
        calls.append(1)
        return httpx.Response(500)

    c = AnalyzerClient("http://x", timeout_s=1, concurrency=2, client=transport(h))
    with pytest.raises(AnalyzerUnavailable):
        await c.analyze("abcd", min_score=0.5, entities=[], recognizers=[])
    assert len(calls) == 2


async def test_4xx_does_not_retry():
    calls = []

    def h(req):
        calls.append(1)
        return httpx.Response(422)

    c = AnalyzerClient("http://x", timeout_s=1, concurrency=1, client=transport(h))
    with pytest.raises(AnalyzerError):
        await c.analyze("abcd", min_score=0.5, entities=[], recognizers=[])
    assert len(calls) == 1


async def test_timeout_maps_to_analyzer_timeout():
    def h(req):
        raise httpx.ReadTimeout("slow")

    c = AnalyzerClient("http://x", timeout_s=0.01, concurrency=1, client=transport(h))
    with pytest.raises(AnalyzerTimeout):
        await c.analyze("abcd", min_score=0.5, entities=[], recognizers=[])


async def test_health_false_on_error():
    def h(req):
        raise httpx.ConnectError("down")

    c = AnalyzerClient("http://x", timeout_s=1, concurrency=1, client=transport(h))
    assert await c.health() is False
