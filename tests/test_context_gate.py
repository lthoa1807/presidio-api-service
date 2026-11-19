from app.config import EntityConfig
from app.context_gate import context_ok


def test_multi_word_vietnamese_context_matches():
    e = EntityConfig(
        "VN_CCCD",
        r"(?<!\d)\d{12}(?!\d)",
        0.6,
        True,
        ("cccd", "căn cước", "chứng minh nhân dân"),
    )
    assert context_ok("Số căn cước là 079203012345", 16, 28, e) is True


def test_missing_context_rejected():
    e = EntityConfig("VN_CCCD", r"\d{12}", 0.6, True, ("cccd", "căn cước"))
    assert context_ok("Mã đơn 079203012345 đã giao", 8, 20, e) is False


def test_entity_without_require_context_always_ok():
    e = EntityConfig("VN_PHONE_NUMBER", r"\d{10}", 0.85, False, ())
    assert context_ok("0901234567", 0, 10, e) is True
