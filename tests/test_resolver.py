from app.config import EntityConfig
from app.models import Span
from app.resolver import resolve

ALLOW = [
    "VN_CCCD",
    "VN_PHONE_NUMBER",
    "VN_TAX_CODE",
    "VN_ADDRESS",
    "VN_NAME",
    "EMAIL_ADDRESS",
]
E = {}


def test_filters_not_allowlisted_and_low_score():
    out = resolve(
        "x",
        [Span(0, 1, "UK_NHS", 1.0), Span(0, 1, "VN_CCCD", 0.4)],
        allowlist=ALLOW,
        min_score=0.5,
        entities_by_name=E,
    )
    assert out == []


def test_longest_span_wins_tax_code_over_phone():
    text = "MST 0312345678-001"
    out = resolve(
        text,
        [Span(4, 14, "VN_PHONE_NUMBER", 0.85), Span(4, 17, "VN_TAX_CODE", 0.6)],
        allowlist=ALLOW,
        min_score=0.5,
        entities_by_name=E,
    )
    assert [s.entity_type for s in out] == ["VN_TAX_CODE"]


def test_address_beats_nested_name():
    text = "số 12 đường Nguyễn Huệ"
    out = resolve(
        text,
        [Span(0, 21, "VN_ADDRESS", 0.55), Span(13, 21, "VN_NAME", 0.6)],
        allowlist=ALLOW,
        min_score=0.5,
        entities_by_name=E,
    )
    assert [s.entity_type for s in out] == ["VN_ADDRESS"]


def test_same_span_uses_allowlist_order():
    out = resolve(
        "x",
        [Span(0, 12, "VN_PHONE_NUMBER", 0.6), Span(0, 12, "VN_CCCD", 0.6)],
        allowlist=ALLOW,
        min_score=0.5,
        entities_by_name=E,
    )
    assert [s.entity_type for s in out] == ["VN_CCCD"]


def test_non_overlapping_sorted_by_start():
    out = resolve(
        "x",
        [Span(5, 10, "VN_NAME", 0.6), Span(0, 3, "VN_NAME", 0.6)],
        allowlist=ALLOW,
        min_score=0.5,
        entities_by_name=E,
    )
    assert [s.start for s in out] == [0, 5]


def test_infers_standalone_given_name_from_full_name():
    text = "Nguyễn Văn An và anh An"
    name = EntityConfig("VN_NAME", r"\S+", 0.6, False, ())
    out = resolve(
        text,
        [Span(0, 13, "VN_NAME", 0.6)],
        allowlist=["VN_NAME"],
        min_score=0.5,
        entities_by_name={"VN_NAME": name},
    )
    assert [(s.start, s.end, s.entity_type) for s in out] == [
        (0, 13, "VN_NAME"),
        (21, 23, "VN_NAME"),
    ]


def test_given_name_inference_is_case_sensitive():
    text = "Nguyễn Văn An và an toàn"
    name = EntityConfig("VN_NAME", r"\S+", 0.6, False, ())
    out = resolve(
        text,
        [Span(0, 13, "VN_NAME", 0.6)],
        allowlist=["VN_NAME"],
        min_score=0.5,
        entities_by_name={"VN_NAME": name},
    )
    assert [(s.start, s.end) for s in out] == [(0, 13)]


def test_known_given_names_from_other_strings_share_the_full_name_key():
    text = "anh Khang đã đến"
    name = EntityConfig("VN_NAME", r"\S+", 0.6, False, ())
    out = resolve(
        text,
        [],
        allowlist=["VN_NAME"],
        min_score=0.5,
        entities_by_name={"VN_NAME": name},
        known_given_names={"Khang": "Nguyễn Hoàng Minh Khang"},
    )
    assert [(s.start, s.end, s.value_key) for s in out] == [
        (4, 9, "Nguyễn Hoàng Minh Khang")
    ]
