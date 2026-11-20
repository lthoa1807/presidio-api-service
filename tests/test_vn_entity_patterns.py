"""Regression tests for the Vietnamese entity patterns against a real payload.

Presidio compiles ad-hoc patterns with ``IGNORECASE | DOTALL | MULTILINE``;
we mirror that here so these tests exercise the same regex behaviour.
"""

from __future__ import annotations

import re
from pathlib import Path

from app.anonymizer import PseudonymState, anonymize_text
from app.config import EntityConfig, load_entities
from app.models import Span
from app.resolver import resolve

CFG = Path("app/config/entities.yaml")
FLAGS = re.IGNORECASE | re.DOTALL | re.MULTILINE

SAMPLE = (
    "Vào lúc 14h30 ngày 15/08/2026, anh Nguyễn Hoàng Minh Khang "
    "(sinh ngày 22/04/1992, giới tính Nam) đã đến chi nhánh ngân hàng "
    "Vietcombank tại địa chỉ số 128/45B đường Nguyễn Thị Minh Khai, "
    "Phường Võ Thị Sáu, Quận 3, TP. Hồ Chí Minh để làm thủ tục mở thẻ tín "
    "dụng. Tại đây, anh Khang đã cung cấp số căn cước công dân (CCCD) là "
    "079092004581, mã số thuế cá nhân 8364920174 và số hộ chiếu C8492015. "
    "Để nhận thông báo giao dịch, anh đăng ký số điện thoại di động "
    "0912.345.678 (hoặc số dự phòng +84 987 654 321) cùng địa chỉ email cá "
    "nhân khang.nguyen92@gmail.com. Ngoài ra, anh cũng kê khai biển số xe "
    "máy 59-T1 456.78, số tài khoản ngân hàng 0071000849201 và địa chỉ IP "
    "truy cập ứng dụng là 113.161.72.104. Người liên hệ khẩn cấp của anh là "
    "chị Trần Thị Thu Thảo (số điện thoại 0388-999-111)."
)

# Extra patterns Presidio provides and the service allowlists.
BUILTIN_PATTERNS = {
    "EMAIL_ADDRESS": r"[\w.+-]+@[\w-]+\.[\w.]+",
    "IP_ADDRESS": r"\b(?:\d{1,3}\.){3}\d{1,3}\b",
}

SECRETS = (
    "Nguyễn Hoàng Minh Khang",
    "Trần Thị Thu Thảo",
    "22/04/1992",
    "079092004581",
    "8364920174",
    "C8492015",
    "0912.345.678",
    "+84 987 654 321",
    "0388-999-111",
    "khang.nguyen92@gmail.com",
    "59-T1 456.78",
    "0071000849201",
    "113.161.72.104",
)


def _entities() -> list[EntityConfig]:
    return list(load_entities(CFG)[0])


def _matches(name: str) -> list[re.Match[str]]:
    hits: list[re.Match[str]] = []
    if name in BUILTIN_PATTERNS:
        return list(re.finditer(BUILTIN_PATTERNS[name], SAMPLE, FLAGS))
    for entity in _entities():
        if entity.name == name:
            hits.extend(re.finditer(entity.regex, SAMPLE, FLAGS))
    return hits


def _texts(name: str) -> list[str]:
    return [match.group() for match in _matches(name)]


def _resolved_texts(name: str) -> list[str]:
    entities = _entities()
    by_name = {entity.name: entity for entity in entities}
    spans = [
        Span(
            match.start(),
            match.end(),
            name,
            by_name[name].score if name in by_name else 1.0,
        )
        for match in _matches(name)
    ]
    resolved = resolve(
        SAMPLE,
        spans,
        allowlist=[name],
        min_score=0.5,
        entities_by_name=by_name,
    )
    return [SAMPLE[span.start : span.end] for span in resolved]


def test_cccd_is_detected_and_not_swallowed_by_an_address():
    assert "079092004581" in _texts("VN_CCCD")
    for text in _texts("VN_ADDRESS"):
        assert "căn cước" not in text
        assert "0790920" not in text


def test_address_keyword_does_not_match_inside_another_word():
    addresses = _texts("VN_ADDRESS")
    assert not any(text.startswith("ấp") for text in addresses)
    assert not any("Trần Thị Thu Thảo" in text for text in addresses)
    assert not any("số điện" in text for text in addresses)


def test_full_street_and_city_addresses_are_kept_together():
    addresses = _texts("VN_ADDRESS")
    assert "số 128/45B đường Nguyễn Thị Minh Khai" in addresses
    assert "TP. Hồ Chí Minh" in addresses


def test_phone_numbers_with_separators_are_detected():
    phones = _texts("VN_PHONE_NUMBER")
    assert "0912.345.678" in phones
    assert "+84 987 654 321" in phones
    assert "0388-999-111" in phones


def test_vehicle_plate_is_detected():
    assert "59-T1 456.78" in _texts("VN_VEHICLE_PLATE")


def test_name_does_not_absorb_the_following_lowercase_word():
    names = _texts("VN_NAME")
    assert "Nguyễn Hoàng Minh Khang" in names
    assert not any("để" in name for name in names)


def test_date_of_birth_is_detected_but_transaction_time_is_not():
    dobs = _resolved_texts("VN_DATE_OF_BIRTH")
    assert dobs == ["22/04/1992"]


def test_resolved_spans_remove_all_secrets_without_corrupting_text():
    entities = _entities()
    by_name = {entity.name: entity for entity in entities}
    allowlist = [
        "VN_CCCD",
        "VN_CMND",
        "VN_PHONE_NUMBER",
        "VN_TAX_CODE",
        "VN_BANK_ACCOUNT",
        "VN_PASSPORT",
        "VN_VEHICLE_PLATE",
        "VN_DATE_OF_BIRTH",
        "VN_NAME",
        "VN_ADDRESS",
        "EMAIL_ADDRESS",
        "CREDIT_CARD",
        "IP_ADDRESS",
        "URL",
    ]

    spans: list[Span] = []
    for name in allowlist:
        for match in _matches(name):
            score = by_name[name].score if name in by_name else 1.0
            spans.append(Span(match.start(), match.end(), name, score))

    resolved = resolve(
        SAMPLE,
        spans,
        allowlist=allowlist,
        min_score=0.5,
        entities_by_name=by_name,
    )
    anonymized, _ = anonymize_text(
        SAMPLE,
        resolved,
        mode="pseudonym",
        salt="",
        state=PseudonymState(),
    )

    for secret in SECRETS:
        assert secret not in anonymized

    # Words around the redacted spans must survive intact.
    assert "cung cấp số căn cước" in anonymized
    assert "Người liên hệ khẩn cấp" in anonymized
    assert "128/45B" not in anonymized

    # Only the date is replaced; the label and the transaction time stay.
    assert "sinh ngày <VN_DATE_OF_BIRTH_1>" in anonymized
    assert "15/08/2026" in anonymized

    # Given-name-only references are inferred from the full name.
    assert "Khang" not in anonymized
    assert "Tại đây, anh <VN_NAME_" in anonymized
