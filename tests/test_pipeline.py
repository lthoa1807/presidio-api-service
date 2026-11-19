import asyncio
import dataclasses
import json

from app.config import load_settings
from app.models import Span
from app.pipeline import ProcessOptions, process_payload

S = load_settings(env={})


class FakeAnalyzer:
    def __init__(self, hits):
        self.hits, self.calls = hits, []

    async def analyze(self, text, *, min_score, entities, recognizers):
        self.calls.append(text)
        return [Span(s, e, t, sc) for s, e, t, sc in self.hits.get(text, [])]


async def test_dedupes_identical_values_but_emits_finding_per_path():
    a = FakeAnalyzer({"john@x.com": [(0, 11, "EMAIL_ADDRESS", 1.0)]})
    res = await process_payload(
        {"a": "john@x.com", "b": ["john@x.com"]}, ProcessOptions(), S, a
    )
    assert a.calls == ["john@x.com"]
    assert res.data == {"a": "<EMAIL_ADDRESS>", "b": ["<EMAIL_ADDRESS>"]}
    assert [f.path for f in res.findings] == ["$.a", "$.b[0]"]
    assert res.stats == {
        "strings_scanned": 2,
        "unique_strings": 1,
        "entities_found": 2,
        "by_type": {"EMAIL_ADDRESS": 2},
    }


async def test_chunk_boundary_does_not_duplicate_entity():
    # 20 filler chars + email, chunk_size=25 puts the cut inside "user@".
    text = "a" * 20 + "user@example.com"
    settings = dataclasses.replace(S, chunk_size=25, chunk_overlap=10)

    class ChunkFake:
        def __init__(self):
            self.calls = []

        async def analyze(self, text, *, min_score, entities, recognizers):
            self.calls.append(text)
            i = text.find("user@example.com")
            return [Span(i, i + 16, "EMAIL_ADDRESS", 1.0)] if i >= 0 else []

    a = ChunkFake()
    res = await process_payload({"x": text}, ProcessOptions(), settings, a)
    assert len(a.calls) == 2
    assert len(res.findings) == 1
    assert res.findings[0].path == "$.x"
    assert res.data["x"] == "a" * 20 + "<EMAIL_ADDRESS>"


async def test_chunks_of_one_string_are_analyzed_concurrently():
    text = "a" * 6000
    state = {"inflight": 0, "max": 0}

    class SlowFake:
        async def analyze(self, text, *, min_score, entities, recognizers):
            state["inflight"] += 1
            state["max"] = max(state["max"], state["inflight"])
            await asyncio.sleep(0.01)
            state["inflight"] -= 1
            return []

    res = await process_payload({"x": text}, ProcessOptions(), S, SlowFake())
    assert state["max"] == 2
    assert res.data["x"] == text


async def test_json_string_without_pii_is_byte_identical():
    raw = '{"a": 1,  "b": "ok"}'
    res = await process_payload({"x": raw}, ProcessOptions(), S, FakeAnalyzer({}))
    assert res.data["x"] == raw


async def test_json_string_with_pii_is_recursed_and_stays_valid_json():
    a = FakeAnalyzer({"0901234567": [(0, 10, "VN_PHONE_NUMBER", 0.85)]})
    res = await process_payload(
        {"x": '{"phone":"0901234567"}'}, ProcessOptions(), S, a
    )
    assert json.loads(res.data["x"]) == {"phone": "<VN_PHONE_NUMBER>"}
    assert res.findings[0].path == "$.x#$.phone"


async def test_numbers_untouched_by_default():
    res = await process_payload(
        {"n": 123, "f": 1.5}, ProcessOptions(), S, FakeAnalyzer({})
    )
    assert res.data == {"n": 123, "f": 1.5}
    assert res.findings == []
