import dataclasses

import httpx
from fastapi.testclient import TestClient

import app.main as m
from app.analyzer_client import AnalyzerClient, AnalyzerUnavailable
from app.config import load_settings
from app.models import Span


class FakeAnalyzer:
    def __init__(self, hits=None, fail=False):
        self.hits, self.fail = hits or {}, fail

    async def health(self):
        return True

    async def analyze(self, text, *, min_score, entities, recognizers):
        if self.fail:
            raise AnalyzerUnavailable("down")
        return [Span(s, e, t, sc) for s, e, t, sc in self.hits.get(text, [])]


def client(analyzer, settings=None):
    return TestClient(
        m.create_app(settings=settings or load_settings(env={}), analyzer=analyzer)
    )


def test_healthz_ok():
    assert client(FakeAnalyzer()).get("/healthz").json() == {
        "status": "ok",
        "analyzer": "ok",
    }


def test_anonymize_returns_raw_json():
    r = client(FakeAnalyzer({"john@x.com": [(0, 11, "EMAIL_ADDRESS", 1.0)]})).post(
        "/v1/anonymize", json={"m": "john@x.com"}
    )
    assert r.status_code == 200 and r.json() == {"m": "<EMAIL_ADDRESS>"}


def test_include_findings_wrapper():
    r = client(FakeAnalyzer({"john@x.com": [(0, 11, "EMAIL_ADDRESS", 1.0)]})).post(
        "/v1/anonymize?include_findings=true", json={"m": "john@x.com"}
    )
    body = r.json()
    assert body["data"] == {"m": "<EMAIL_ADDRESS>"}
    assert body["findings"][0]["path"] == "$.m"
    assert body["stats"]["entities_found"] == 1


def test_anonymize_bare_returns_only_marked_up_data():
    hits = {"john@x.com": [(0, 11, "EMAIL_ADDRESS", 1.0)]}
    r = client(FakeAnalyzer(hits)).post(
        "/v1/anonymize/bare", json={"m": "john@x.com", "n": 1}
    )
    assert r.status_code == 200
    assert r.json() == {"m": "<EMAIL_ADDRESS>", "n": 1}
    assert set(r.json()) == {"m", "n"}
    assert "findings" not in r.json() and "stats" not in r.json()
    assert "x-pii-entities" not in {k.lower() for k in r.headers}
    assert "x-request-id" not in {k.lower() for k in r.headers}


def test_anonymize_bare_ignores_include_findings():
    hits = {"john@x.com": [(0, 11, "EMAIL_ADDRESS", 1.0)]}
    r = client(FakeAnalyzer(hits)).post(
        "/v1/anonymize/bare?include_findings=true", json={"m": "john@x.com"}
    )
    assert r.json() == {"m": "<EMAIL_ADDRESS>"}


def test_anonymize_bare_keeps_validation_and_auth():
    assert (
        client(FakeAnalyzer()).post("/v1/anonymize/bare?mode=mask", json={}).status_code
        == 422
    )
    s = dataclasses.replace(load_settings(env={}), api_key="secret")
    c = client(FakeAnalyzer(), s)
    assert c.post("/v1/anonymize/bare", json={}).status_code == 401
    assert (
        c.post(
            "/v1/anonymize/bare", json={}, headers={"X-API-Key": "secret"}
        ).status_code
        == 200
    )


def test_anonymize_bare_analyzer_down_is_503():
    r = client(FakeAnalyzer(fail=True)).post("/v1/anonymize/bare", json={"s": "abc"})
    assert r.status_code == 503 and "abc" not in r.text


def test_detect_does_not_modify_data():
    r = client(FakeAnalyzer({"john@x.com": [(0, 11, "EMAIL_ADDRESS", 1.0)]})).post(
        "/v1/detect", json={"m": "john@x.com"}
    )
    body = r.json()
    assert "data" not in body and body["findings"][0]["entity_type"] == "EMAIL_ADDRESS"


def test_invalid_json_media_type_and_params():
    c = client(FakeAnalyzer())
    assert (
        c.post(
            "/v1/anonymize", content=b"{", headers={"content-type": "application/json"}
        ).status_code
        == 400
    )
    assert (
        c.post(
            "/v1/anonymize", content=b"{}", headers={"content-type": "text/plain"}
        ).status_code
        == 415
    )
    assert c.post("/v1/anonymize?mode=mask", json={}).status_code == 422
    assert c.post("/v1/anonymize?language=vi", json={}).status_code == 422
    assert c.post("/v1/anonymize?min_score=2", json={}).status_code == 422


def test_body_limit():
    s = dataclasses.replace(load_settings(env={}), max_body_bytes=10)
    assert (
        client(FakeAnalyzer(), s)
        .post("/v1/anonymize", json={"x": "long value"})
        .status_code
        == 413
    )


async def test_chunked_body_stops_reading_past_limit():
    s = dataclasses.replace(load_settings(env={}), max_body_bytes=10)

    class FakeRequest:
        def __init__(self):
            self.headers = {"content-type": "application/json"}

        async def stream(self):
            yield b'{"x": "'
            yield b"a" * 50
            raise AssertionError("body read past the limit")

        async def body(self):
            raise AssertionError("body must not be buffered before the limit check")

    data, error = await m._read_body(FakeRequest(), s, "rid")
    assert data is None
    assert error.status_code == 413


def test_api_key_auth():
    s = dataclasses.replace(load_settings(env={}), api_key="secret")
    c = client(FakeAnalyzer(), s)
    assert c.post("/v1/anonymize", json={}).status_code == 401
    assert (
        c.post("/v1/anonymize", json={}, headers={"X-API-Key": "secret"}).status_code
        == 200
    )


def test_analyzer_down_is_503_without_payload():
    r = client(FakeAnalyzer(fail=True)).post("/v1/anonymize", json={"secret": "abc"})
    assert r.status_code == 503
    assert "abc" not in r.text


def test_real_client_connect_error_maps_to_503():
    def h(req):
        raise httpx.ConnectError("refused")

    analyzer = AnalyzerClient(
        "http://x",
        timeout_s=1,
        concurrency=1,
        client=httpx.AsyncClient(transport=httpx.MockTransport(h)),
    )
    r = client(analyzer).post("/v1/anonymize", json={"secret": "abc"})
    assert r.status_code == 503
    assert "abc" not in r.text
