import os

import httpx
import pytest

pytestmark = pytest.mark.live

SECRETS = (
    "Nguyễn Văn An",
    "079203012345",
    "0901234567",
    "an.nv@example.com",
    "1234567890123",
)


@pytest.mark.skipif(not os.getenv("PII_LIVE_URL"), reason="set PII_LIVE_URL")
def test_vietnamese_sample_has_no_original_pii():
    payload = {
        "messages": [
            {
                "role": "user",
                "content": (
                    "Tôi tên Nguyễn Văn An, CCCD 079203012345, sđt 0901234567, "
                    "email an.nv@example.com, STK 1234567890123."
                ),
            }
        ]
    }
    url = os.environ["PII_LIVE_URL"].rstrip("/") + "/v1/anonymize"
    response = httpx.post(url, json=payload, timeout=60)
    assert response.status_code == 200
    for secret in SECRETS:
        assert secret not in response.text


@pytest.mark.skipif(not os.getenv("PII_LIVE_URL"), reason="set PII_LIVE_URL")
def test_order_code_without_context_is_untouched():
    payload = {"note": "Đơn hàng 123456789012 đã giao"}
    url = os.environ["PII_LIVE_URL"].rstrip("/") + "/v1/anonymize"
    response = httpx.post(url, json=payload, timeout=60)
    assert response.status_code == 200
    assert "123456789012" in response.text
