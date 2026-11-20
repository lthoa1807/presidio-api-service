---
name: PII API Usage
description: Use when calling, integrating with, testing, or debugging this repo's PII API — POST /v1/anonymize, POST /v1/detect, GET /healthz, X-API-Key auth, modes replace/pseudonym/hash, findings/stats, payload limits, or error codes. Triggers include "gọi PII API", "ẩn danh dữ liệu", "detect PII", "viết client security-api", curl examples, 4xx/5xx from the service.
---

# PII API Usage

Service FastAPI bọc Presidio analyzer. Nhận JSON bất kỳ, thay giá trị PII bằng
token một chiều, giữ nguyên cấu trúc và key. Chạy tại `http://localhost:8000`
(Docker Compose map `8000:8000`).

**Nguyên tắc bắt buộc:**

- API là **fail-closed**. Mọi non-200 nghĩa là "chưa xử lý xong", tuyệt đối
  không được coi là "không có PII" và không được gửi payload gốc đi tiếp.
- Chỉ **giá trị** bị thay; key và cấu trúc JSON giữ nguyên.
- Không có endpoint khôi phục token. `replace`/`pseudonym`/`hash` đều một chiều.
- Query params nằm trên URL, không phải field trong body.

## Endpoint nhanh

| Endpoint | Mục đích | Body 200 |
| --- | --- | --- |
| `POST /v1/anonymize` | Ẩn danh payload | JSON đã xử lý; hoặc `{data, findings, stats}` nếu `include_findings=true` |
| `POST /v1/detect` | Audit, không trả data đã sửa | Luôn `{findings, stats}` |
| `GET /healthz` | Kiểm tra service + analyzer | `{"status":"ok","analyzer":"ok"}` hoặc 503 |

Header thành công: `X-Request-Id` (echo `X-Request-Id` client gửi, nếu không có
thì uuid4) và `X-PII-Entities` (số lần xuất hiện entity).

## Query params

| Param | Giá trị | Mặc định |
| --- | --- | --- |
| `mode` | `replace`, `pseudonym`, `hash` | `replace` |
| `min_score` | số thực `0.0`–`1.0` | `PII_DEFAULT_MIN_SCORE` (`0.5`) |
| `language` | chỉ `en` | `en` |
| `scan_numbers` | boolean | `false` |
| `include_findings` | boolean | `false` (chỉ có tác dụng với `/v1/anonymize`) |

Boolean nhận `1/true/yes/on` và `0/false/no/off`, không phân biệt hoa thường.
Param sai → `422 invalid_param`.

Mode:

- `replace`: `<EMAIL_ADDRESS>` — mọi lần xuất hiện giống nhau.
- `pseudonym`: `<EMAIL_ADDRESS_1>`, `<EMAIL_ADDRESS_2>` — cùng value + entity
  trong **cùng request** nhận cùng token; đánh số lại từ 1 ở request sau.
- `hash`: `<EMAIL_ADDRESS_<16 hex>>` — SHA-256 có salt
  (`PII_ANONYMIZE_SALT`). Salt rỗng → salt ngẫu nhiên mỗi lần khởi động, hash
  không ổn định qua restart.

## Curl mẫu

Ẩn danh, pseudonym, kèm findings, có API key:

```bash
curl -sS -X POST \
  'http://localhost:8000/v1/anonymize?mode=pseudonym&include_findings=true' \
  -H 'Content-Type: application/json' \
  -H 'X-API-Key: '"$PII_API_KEY" \
  -H 'X-Request-Id: demo-001' \
  -d '{
    "messages": [
      {"role": "user", "content": "Tôi tên Nguyễn Văn An, CCCD 079203012345, sđt 0901234567, email an.nv@example.com"}
    ]
  }'
```

Detect, hạ ngưỡng để audit:

```bash
curl -sS -X POST 'http://localhost:8000/v1/detect?min_score=0.3' \
  -H 'Content-Type: application/json' \
  -d '{"note": "gọi 0901234567, email an.nv@example.com"}'
```

`X-API-Key` chỉ cần khi server đặt `PII_API_KEY`. Health check không cần key.

## Response cần đọc đúng

Với `include_findings=true`, response là wrapper:

```json
{
  "data": {"...": "JSON đã ẩn danh"},
  "findings": [
    {"path": "$.messages[0].content", "entity_type": "VN_PHONE_NUMBER",
     "start": 4, "end": 14, "score": 0.85, "token": "<VN_PHONE_NUMBER_1>"}
  ],
  "stats": {"strings_scanned": 1, "unique_strings": 1,
            "entities_found": 1, "by_type": {"VN_PHONE_NUMBER": 1}}
}
```

- `path`: JSONPath tới đúng lần xuất hiện. Key đặc biệt dùng `$["a.b"]`.
  PII nằm trong string là JSON lồng nhau dùng
  `$.messages[0].tool_calls[0].function.arguments#$.phone`.
- `start`/`end`: offset trong **string gốc tại path**, không phải offset của
  body HTTP và không dùng để cắt `data` sau khi đã thay token.
- `stats.strings_scanned` đếm số lần xuất hiện; `unique_strings` đếm số giá trị
  duy nhất đã gọi analyzer; `entities_found` = `len(findings)` = số lần xuất
  hiện; `by_type` cũng đếm theo lần xuất hiện.

Với `/v1/detect`, response luôn là `{findings, stats}`; `include_findings`
không làm thay đổi shape và không có `data`.

## Xử lý lỗi

Envelope chuẩn:

```json
{"error": {"code": "invalid_param", "message": "min_score must be between 0 and 1", "request_id": "..."}}
```

| HTTP | `code` | Khi nào |
| --- | --- | --- |
| 400 | `invalid_json` | Body không phải JSON hợp lệ |
| 401 | `unauthorized` | Thiếu/sai `X-API-Key` khi bật auth |
| 413 | `payload_too_large` | Body > `PII_MAX_BODY_BYTES` |
| 413 | `too_many_strings` | > `PII_MAX_STRINGS` giá trị unique |
| 413 | `text_too_long` | Một string > `PII_MAX_TEXT_LEN` |
| 413 | `payload_too_deep` | JSON sâu > `PII_MAX_DEPTH` |
| 413 | `total_text_too_large` | Tổng ký tự > `PII_MAX_TOTAL_CHARS` |
| 415 | `unsupported_media_type` | `Content-Type` không phải `application/json` |
| 422 | `invalid_param` | Query param sai |
| 502 | `analyzer_error` | Analyzer trả lỗi/response không dùng được |
| 503 | `analyzer_unavailable` | Không kết nối được analyzer |
| 504 | `analyzer_timeout` | Một lần gọi analyzer quá hạn |
| 504 | `deadline_exceeded` | Toàn request quá hạn |

Chỉ retry `502`/`503`/`504` (có thể kèm backoff); không retry `4xx` nguyên
payload. Service không trả partial và không echo payload trong lỗi. `404`/`405`
của FastAPI có thể dùng `{"detail": "..."}` thay vì envelope.

## Python client tối thiểu

```python
import os
import httpx

BASE = os.environ.get("PII_API_URL", "http://localhost:8000").rstrip("/")
headers = {"Content-Type": "application/json"}
if key := os.environ.get("PII_API_KEY"):
    headers["X-API-Key"] = key

payload = {"messages": [{"role": "user", "content": "email an.nv@example.com"}]}
params = {"mode": "pseudonym", "include_findings": "true"}

with httpx.Client(timeout=35.0) as client:  # deadline service là 30 s
    resp = client.post(f"{BASE}/v1/anonymize", json=payload, params=params, headers=headers)

if resp.status_code != 200:
    err = resp.json().get("error", {})
    raise RuntimeError(f"{resp.status_code} {err.get('code')}: {err.get('message')}")

body = resp.json()
anonymized, findings, stats = body["data"], body["findings"], body["stats"]
```

## Bẫy thường gặp

- Thiếu `Content-Type: application/json` → `415`; curl `-d` mặc định là form.
- Đặt `mode`, `include_findings`... trong body → bị bỏ qua; phải đặt trên URL.
- `min_score` mặc định `0.5`: audit nên hạ (ví dụ `0.3`), production có thể tăng.
- `scan_numbers=false`: số JSON không được quét. Bật `true` nếu ID/SĐT có thể
  là number; field bị thay sẽ đổi thành string token.
- String < 3 ký tự, key JSON, và value dưới `exclude_keys` (mặc định `model`,
  `role`, `object`, `finish_reason`, `created`, `system_fingerprint`,
  `logprobs`) không được quét.
- `VN_CCCD`, `VN_CMND`, `VN_TAX_CODE`, `VN_BANK_ACCOUNT` cần từ khóa context
  trong ±60 ký tự; không có context thì không bị thay. `VN_NAME`/`VN_ADDRESS`
  là heuristic nên có thể sót hoặc false positive.
- String là JSON lồng nhau được đệ quy tối đa 3 tầng. Không có PII → giữ nguyên
  từng byte; có PII → serialize lại bằng `json.dumps(..., ensure_ascii=False)`.
- Không dùng token `pseudonym`/`hash` làm ID ổn định giữa các request. Hash chỉ
  ổn định khi đặt `PII_ANONYMIZE_SALT` cố định.
- Retry `502/503/504` an toàn về mặt dữ liệu vì service stateless, nhưng mỗi lần
  retry là một request mới nên token `pseudonym` có thể đổi. Đừng ghi đè output
  đã lưu nếu token cần ổn định.
- Không có batch/async endpoint. Payload lớn có thể chạm deadline 30 s; hãy gọi
  bất đồng bộ phía client và đặt read timeout > 30 s.
- String dài tới 64.000 ký tự được service tự chunk (4.000 ký tự, overlap 200);
  client không cần tự cắt.
- `/healthz` trả `503 {"status":"degraded","analyzer":"unreachable"}` khi
  analyzer chết; kết quả được cache 5 s.

## Chạy và kiểm chứng

```bash
docker compose up -d --build
curl -s http://localhost:8000/healthz
# {"status":"ok","analyzer":"ok"}

PII_LIVE_URL=http://localhost:8000 pytest tests/live -v
pytest -q                     # offline, dùng mock analyzer
```

Presidio phải chạy trong network `presidio_default`; nếu không, mọi request
anonymize/detect trả `503`. Xem `references/api-contract.md` khi cần contract
đầy đủ (path syntax, JSON behavior, toàn bộ env var và entity).
