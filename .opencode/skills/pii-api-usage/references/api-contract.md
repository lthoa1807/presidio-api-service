# PII API — contract chi tiết

Tài liệu tham chiếu cho skill `pii-api-usage`. Nguồn sự thật là code trong
`app/main.py`, `app/pipeline.py`, `app/walker.py`, `app/anonymizer.py` và
`app/config/entities.yaml`.

## 1. Endpoints

### `POST /v1/anonymize`

- Body: JSON bất kỳ (object, array, string, number...). Khuyến nghị object/array.
- `Content-Type` phải là `application/json` (cho phép có `; charset=...`);
  thiếu/sai → `415 unsupported_media_type`.
- Trả JSON đã ẩn danh, giữ nguyên shape/thứ tự key/kiểu của field không bị PII.
- `include_findings=true` → wrapper `{data, findings, stats}`.
- `include_findings=false` (mặc định) → chỉ JSON đã xử lý (không có wrapper).

### `POST /v1/detect`

- Cùng body, auth, query params và giới hạn với `/v1/anonymize`.
- Không trả `data`, không echo payload gốc; luôn trả `{findings, stats}`.
- `mode` chỉ ảnh hưởng tới giá trị `token` trong finding; detection giữ nguyên.
- `include_findings` vẫn được validate nhưng không đổi response shape.

### `GET /healthz`

- Không cần `X-API-Key`.
- Analyzer OK: `200 {"status":"ok","analyzer":"ok"}`.
- Analyzer không kết nối được: `503 {"status":"degraded","analyzer":"unreachable"}`.
- Kết quả gọi analyzer được cache 5 giây. Startup warm up analyzer không chặn
  nhận request.

### Headers của response 200

| Header | Giá trị |
| --- | --- |
| `X-Request-Id` | `X-Request-Id` client gửi, nếu không có thì uuid4 |
| `X-PII-Entities` | `stats.entities_found` dạng chuỗi |

Response lỗi do service tạo (`_error`) **không** gắn `X-Request-Id`; dùng
`error.request_id` trong body.

## 2. Query params

| Param | Giá trị hợp lệ | Mặc định | Ghi chú |
| --- | --- | --- | --- |
| `mode` | `replace`, `pseudonym`, `hash` | `replace` | Case-sensitive |
| `min_score` | float `0.0`–`1.0` | `PII_DEFAULT_MIN_SCORE` = `0.5` | Lọc cả phía service |
| `language` | `en` | `en` | Giá trị khác → 422 |
| `scan_numbers` | boolean | `false` | Quét `int`/`float`, trừ `bool` |
| `include_findings` | boolean | `false` | Chỉ đổi shape của anonymize |

Boolean: `1/true/yes/on` → true; `0/false/no/off` → false; giá trị khác (kể cả
`?include_findings` không có `=`) → `422 invalid_param`.

Thứ tự xử lý: auth → validate query → đọc body. Vì vậy thiếu API key trả `401`
trước cả khi body sai.

### Thông báo lỗi param thực tế

- `mode must be one of ['hash', 'pseudonym', 'replace']`
- `min_score must be a number`
- `min_score must be between 0 and 1`
- `only language=en is supported`
- `scan_numbers must be a boolean`
- `include_findings must be a boolean`

Tất cả trả HTTP `422`, code `invalid_param`.

## 3. Auth

- Chỉ bật khi env `PII_API_KEY` khác rỗng.
- Client gửi `X-API-Key: <key>`; so sánh constant-time.
- Thiếu/sai → `401 {"error":{"code":"unauthorized",...}}`.

## 4. Findings

Mỗi finding:

```json
{
  "path": "$.messages[0].content",
  "entity_type": "VN_PHONE_NUMBER",
  "start": 0,
  "end": 10,
  "score": 0.85,
  "token": "<VN_PHONE_NUMBER>"
}
```

- **Một finding cho mỗi lần xuất hiện**, kể cả khi cùng value xuất hiện ở nhiều
  path. Analyzer chỉ được gọi một lần cho mỗi value unique (dedupe).
- `start`/`end`: offset Python trong string gốc tại `path`, `end` exclusive.
  Không phải offset trong body HTTP; không dùng để slice output đã thay token.
- `score`: score sau khi analyzer trả về; span bị lọc bởi `min_score`, allowlist,
  context gate và overlap resolution.
- `token`: token sẽ được dùng (hoặc đã dùng) để thay; không bao giờ chứa giá trị
  gốc.
- Thứ tự: traversal object (thứ tự key gốc) → array index → `start` trong string.
- Token `replace`: `<ENTITY>`; `pseudonym`: `<ENTITY_1>`, `<ENTITY_2>`; `hash`:
  `<ENTITY_<16 hex>>`.

### Path syntax

- Object key là identifier: `$.a`, `$.messages[0].content`.
- Key đặc biệt (có dấu chấm, khoảng trắng...): `$["a.b"]`; `\` và `"` được escape.
- Array: `$.items[0]`.
- PII trong string là JSON lồng nhau:
  `<path chuỗi cha>#<JSONPath bên trong>`.
  Ví dụ `$.messages[0].tool_calls[0].function.arguments#$.phone`.

Với finding lồng nhau, `start`/`end` tính trên **string leaf bên trong**, không
phải trên chuỗi JSON cha đã serialize.

## 5. Stats

```json
{
  "strings_scanned": 14,
  "unique_strings": 10,
  "entities_found": 3,
  "by_type": {"EMAIL_ADDRESS": 1, "VN_PHONE_NUMBER": 2}
}
```

| Field | Ý nghĩa |
| --- | --- |
| `strings_scanned` | Số **lần xuất hiện** string đã đưa vào xử lý |
| `unique_strings` | Số **giá trị text unique** đã gọi analyzer (kể cả không có PII) |
| `entities_found` | Số finding được chấp nhận = `len(findings)` |
| `by_type` | Số finding theo từng entity type |

`strings_scanned` không đếm: key JSON, value dưới `exclude_keys`, string ngắn
hơn `MIN_TEXT_LEN`, number khi `scan_numbers=false`, và string JSON cha đã
parse + đệ quy thành công (chỉ các leaf bên trong được đếm).

`unique_strings` không phải số giá trị PII. Một string unique có thể sinh nhiều
finding; nhiều lần xuất hiện của cùng một value chỉ đóng góp 1 vào
`unique_strings` nhưng nhiều vào `strings_scanned` và `entities_found`.

## 6. Hành vi với JSON

- Chỉ value được quét; **key không bao giờ bị quét**.
- Mặc định chỉ string. `scan_numbers=true` thêm `int`/`float` (không thêm
  `bool`); nếu bị thay, giá trị number trở thành string token.
- String ngắn hơn `MIN_TEXT_LEN` (mặc định 3) bị bỏ qua.
- `exclude_keys`: so khớp tên key lowercase ở **mọi cấp**; value dưới key đó bị
  bỏ qua. Mặc định: `model`, `role`, `object`, `finish_reason`, `created`,
  `system_fingerprint`, `logprobs`.
- `include_paths`/`exclude_paths` chỉ tồn tại trong pipeline nội bộ, **không**
  expose qua HTTP.
- String là JSON lồng nhau (sau `strip()` bắt đầu bằng `{` hoặc `[`) được parse
  và đệ quy tối đa **3 tầng** khi `PII_RECURSE_JSON_STRINGS=true` (mặc định).
  - Không có PII bên trong → giữ nguyên string gốc từng byte.
  - Có PII → serialize lại bằng `json.dumps(..., ensure_ascii=False)`, nên
    whitespace/format có thể đổi.
  - Parse lỗi → coi như string thường và quét như text.
  - Escape như `\u1ec7` được giải mã trước khi matching.
- String dài hơn `CHUNK_SIZE` (4000) được cắt thành chunk 4000 ký tự, overlap
  200, ưu tiên ranh giới câu/xuống dòng trong 20% cuối. Offset được ghép lại;
  entity vắt qua ranh giới vẫn được báo một lần.
- Dedupe theo value unique; các chunk của cùng một string dài được gọi analyzer
  song song (giới hạn bởi `PII_ANALYZER_CONCURRENCY`). Các string khác nhau
  trong cùng payload xử lý tuần tự.

## 7. Anonymization modes

| Mode | Token | Tính chất |
| --- | --- | --- |
| `replace` | `<EMAIL_ADDRESS>` | Mọi lần xuất hiện giống nhau |
| `pseudonym` | `<EMAIL_ADDRESS_1>` | Cùng value + entity trong cùng request → cùng token; đánh số theo lần xuất hiện đầu; reset mỗi request |
| `hash` | `<EMAIL_ADDRESS_ab12cd34ef56ab78>` | Ổn định nếu salt cố định; 16 hex đầu |

`hash` dùng `sha256(salt + "\x00" + value).hexdigest()[:16]`. README ghi
"HMAC-SHA256"; implementation hiện tại là salted SHA-256. `PII_ANONYMIZE_SALT`
rỗng → dùng salt ngẫu nhiên tạo lúc import, log warning; hash chỉ ổn định tới
lần restart tiếp theo.

Không có `mask` và không có endpoint khôi phục. Mapping pseudonym chỉ nằm trong
bộ nhớ của một request. Retry một request `pseudonym` sẽ tạo mapping mới, nên
token có thể khác lần trước.

## 8. Entity mặc định

Cấu hình: `app/config/entities.yaml`.

| Entity | Nguồn | Context bắt buộc |
| --- | --- | --- |
| `VN_CCCD` | regex 12 số | Có: `cccd`, `căn cước`, `cmnd`, `chứng minh nhân dân`, `định danh`... |
| `VN_CMND` | regex 9 số | Có: `cmnd`, `chứng minh nhân dân` |
| `VN_PHONE_NUMBER` | regex đầu `0`/`+84` | Không |
| `VN_TAX_CODE` | `1234567890` / `1234567890-001` | Có: `mst`, `mã số thuế`, `tax` |
| `VN_BANK_ACCOUNT` | 9–16 số | Có: `stk`, `số tài khoản`, `tài khoản`, `ngân hàng`, `bank` |
| `VN_PASSPORT` | 1 chữ + 7–8 số | Không |
| `VN_VEHICLE_PLATE` | biển số xe | Không |
| `VN_NAME` | heuristic họ phổ biến + 1–3 từ | Không |
| `VN_ADDRESS` | heuristic số nhà/đường hoặc đơn vị hành chính | Không |
| `EMAIL_ADDRESS`, `CREDIT_CARD`, `IP_ADDRESS`, `URL` | Presidio built-in | Không |

Context gate tìm từ khóa trong cửa sổ ±60 ký tự quanh span trên string gốc.
`VN_NAME`/`VN_ADDRESS` là heuristic, không phải NER; tên hiếm/không dấu/viết
thường và địa chỉ không theo mẫu có thể bị sót.

Allowlist mặc định: `VN_CCCD`, `VN_CMND`, `VN_PHONE_NUMBER`, `VN_TAX_CODE`,
`VN_BANK_ACCOUNT`, `VN_PASSPORT`, `VN_VEHICLE_PLATE`, `VN_NAME`, `VN_ADDRESS`,
`EMAIL_ADDRESS`, `CREDIT_CARD`, `IP_ADDRESS`, `URL`.

Không dùng mặc định `PERSON`, `LOCATION`, `ORGANIZATION`, `DATE_TIME`... vì model
tiếng Anh gán nhãn sai cho tiếng Việt. Có thể bật bằng `entity_priority` trong
YAML, hoặc thêm regex ad-hoc theo mẫu:

```yaml
entities:
  - name: PHONE_NUMBER
    regex: '\+?\d[\d .\-()]{7,}\d'
    score: 0.4
    require_context: false
    context: []
```

## 9. Giới hạn

| Giới hạn | Mặc định | Env |
| --- | --- | --- |
| Body | 5 MiB (5.242.880 bytes) | `PII_MAX_BODY_BYTES` |
| String unique / request | 500 | `PII_MAX_STRINGS` |
| Tổng ký tự text unique | 512.000 | `PII_MAX_TOTAL_CHARS` |
| Độ dài 1 string | 64.000 | `PII_MAX_TEXT_LEN` |
| Độ sâu JSON | 32 | `PII_MAX_DEPTH` |
| Deadline toàn request | 30 s | `PII_REQUEST_DEADLINE_MS` |
| Timeout 1 lần gọi analyzer | 10 s | `PII_ANALYZER_TIMEOUT_MS` |
| Concurrency gọi analyzer | 2 | `PII_ANALYZER_CONCURRENCY` |
| Chunk size / overlap | 4000 / 200 | `PII_CHUNK_SIZE` / `PII_CHUNK_OVERLAP` |
| String tối thiểu | 3 | `PII_MIN_TEXT_LEN` |
| Min score mặc định | 0.5 | `PII_DEFAULT_MIN_SCORE` |
| Đệ quy JSON string | bật | `PII_RECURSE_JSON_STRINGS` |

`too_many_strings` và `total_text_too_large` tính trên **value unique**;
`text_too_long` áp dụng cho từng lần xuất hiện string.

Body được kiểm tra `Content-Length` trước, rồi kiểm tra khi stream; không buffer
quá giới hạn.

## 10. Mã lỗi

Envelope:

```json
{"error": {"code": "payload_too_large", "message": "body too large", "request_id": "..."}}
```

| HTTP | `code` | Nguyên nhân |
| --- | --- | --- |
| 400 | `invalid_json` | Body không parse được hoặc `Content-Length` sai |
| 401 | `unauthorized` | Thiếu/sai API key khi bật auth |
| 413 | `payload_too_large` | Body vượt giới hạn |
| 413 | `too_many_strings` | Vượt `MAX_STRINGS` value unique |
| 413 | `text_too_long` | Một string vượt `MAX_TEXT_LEN` |
| 413 | `payload_too_deep` | JSON vượt `MAX_DEPTH` |
| 413 | `total_text_too_large` | Tổng text unique vượt `MAX_TOTAL_CHARS` |
| 415 | `unsupported_media_type` | Content-Type không phải JSON |
| 422 | `invalid_param` | Query param sai |
| 502 | `analyzer_error` | Analyzer trả 4xx hoặc response không parse được |
| 503 | `analyzer_unavailable` | Không kết nối được analyzer / 5xx sau retry |
| 504 | `analyzer_timeout` | Một lần gọi analyzer quá hạn |
| 504 | `deadline_exceeded` | Toàn request quá hạn |

Analyzer client retry 1 lần cho lỗi mạng/timeout/5xx; không retry 4xx. Lỗi của
một string làm hỏng toàn request (fail-closed), không trả partial.

## 11. Chạy service

```bash
docker compose up -d --build
curl -s http://localhost:8000/healthz
```

- Port `8000:8000`; network ngoài `presidio_default`.
- Analyzer nội bộ: `http://presidio-analyzer:3000`.
- Healthcheck có `start_period: 90s` vì analyzer cold start ~25 s.
- Chạy không Docker: `uvicorn app.main:app --port 8000` với
  `PII_ANALYZER_URL=http://localhost:5002`.
- Test offline: `pytest -q`. Test live: `PII_LIVE_URL=http://localhost:8000
  pytest tests/live -v`.

## 12. Bảo mật / vận hành

- Service không log payload hay giá trị gốc; chỉ log request id, số string, số
  entity, thời gian và mã lỗi.
- Không persistence: không DB, không lưu mapping token.
- Fail-closed: analyzer lỗi/timeout → lỗi HTTP, không bao giờ trả dữ liệu gốc.
- Container chạy non-root, chỉ expose cổng 8000.
