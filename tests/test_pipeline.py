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
    text = "x" * 3995 + "a@b.com"

    class ChunkFake:
        def __init__(self):
            self.calls = []

        async def analyze(self, text, *, min_score, entities, recognizers):
            self.calls.append(text)
            i = text.find("a@b.com")
            return [Span(i, i + 7, "EMAIL_ADDRESS", 1.0)] if i >= 0 else []

    a = ChunkFake()
    res = await process_payload({"x": text}, ProcessOptions(), S, a)
    assert len(res.findings) == 1
    assert res.findings[0].path == "$.x"
    assert res.data["x"] == "x" * 3995 + "<EMAIL_ADDRESS>"


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
