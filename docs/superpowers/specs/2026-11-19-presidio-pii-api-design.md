# PII API — thiết kế dịch vụ ẩn danh JSON dựa trên Presidio

- Ngày: 2026-11-19
- Trạng thái: đã duyệt (đã qua builder check + kiểm chứng trên stack thật)
- Tác giả: phiên brainstorming với người dùng

## 1. Mục tiêu và lý do

Cần một API độc lập nhận vào một payload JSON bất kỳ (thường là request/response
của một API khác, ví dụ chat LLM) và trả về JSON **đã ẩn danh hoàn toàn**, không
có khả năng khôi phục giá trị gốc. Dữ liệu có thể là tiếng Việt, chứa cả PII có
cấu trúc (CCCD, số điện thoại, email, số tài khoản…) và PII trong văn bản tự do
(tên người, địa chỉ).

Dịch vụ chạy tách biệt, chỉ **gọi tới stack Presidio đang chạy sẵn** (không tự
nuôi model, không sửa stack đó).

## 2. Mục tiêu (goals)

- Nhận JSON tuỳ ý ở `POST /v1/anonymize`, trả JSON cùng cấu trúc với dữ liệu đã
  được thay thế.
- Phát hiện PII tiếng Việt có cấu trúc bằng recognizer regex tuỳ chỉnh gửi kèm
  mỗi lần gọi analyzer (không cần build lại image Presidio).
- Phát hiện PII tiếng Việt trong văn bản tự do ở mức heuristic (tên theo họ phổ
  biến, địa chỉ theo từ khoá hành chính).
- Ẩn danh không thể đảo ngược: `replace` (`<TYPE>`), `pseudonym`
  (`<TYPE_1>`, `<TYPE_2>`), hoặc `hash` (SHA-256 có salt tuỳ chọn).
- Xử lý được payload lồng sâu, nhiều field, có dedupe, chunk chuỗi dài, và gọi
  analyzer tuần tự/song song hợp lý với năng lực thật của analyzer.
- Fail-closed: mọi lỗi phía Presidio đều trả lỗi, tuyệt đối không trả dữ liệu gốc.

## 3. Không nằm trong phạm vi (non-goals)

- Tokenize/khôi phục (reversible) — người dùng đã chọn ẩn danh hoàn toàn.
- Streaming request/response; chỉ nhận body JSON hoàn chỉnh.
- Ẩn danh ảnh, PDF, file nhị phân (image-redactor không dùng).
- NER tiếng Việt thật (PhoBERT, `vi_core_news_lg`) — để dành giai đoạn sau.
- **Ẩn danh tên key (key names)** trong v1. Chỉ ẩn danh giá trị; tên key giữ
  nguyên để bảo toàn schema. Xem mục 19.
- Job bất đồng bộ (202 + poll) — v1 đồng bộ, có deadline.
- UI, lưu trữ audit, quản lý mapping lâu dài.
- Proxy chặn giữa client và API khác.

## 4. Anti-goals (coi như thất bại nếu xảy ra)

- Trả dữ liệu gốc khi analyzer lỗi hoặc timeout.
- Ghi nội dung payload (kể cả trong log, traceback) ra bất kỳ đâu.
- Lưu mapping token ↔ giá trị gốc trên đĩa.
- Thay đổi cấu trúc JSON (số field, kiểu của field không bị PII, tên key) khi
  không có PII. Riêng field bị PII được phép đổi thành string token (kể cả khi
  đầu vào là số, nếu `scan_numbers=true`).

## 5. Ràng buộc và số đo thực tế

Đo trên stack đang chạy (`2026-11-19`):

- Presidio analyzer chạy `gunicorn -w 1` → **xử lý tuần tự**, tăng concurrency
  phía client chỉ làm request xếp hàng, không tăng throughput.
- Thời gian `/analyze` (model đã warm):

  | Độ dài text | Thời gian |
  |-------------|-----------|
  | 4 KB        | ~0.08 s   |
  | 20 KB       | ~0.5 s    |
  | 50 KB       | ~1.85 s   |
  | 100 KB      | ~7.4 s    |
  | 200 KB      | ~20 s     |

  Tăng superlinear theo độ dài → **phải chunk chuỗi dài**, không gửi nguyên cả
  message dài cho analyzer.
- 4 request 20 KB song song: wall ~3 s, từng request ~0.7/1.4/2.2/3.0 s → xác
  nhận server xử lý tuần tự.
- Cold start (load model) ~25 s; healthcheck phải có `start_period` đủ lớn.
- Analyzer chỉ có `en_core_web_lg`; **không có model tiếng Việt**.
- Analyzer **không có batch API**: mỗi chuỗi là một `POST /analyze`.
- Context booster của Presidio dựa trên token/lemma của model tiếng Anh nên với
  tiếng Việt **chỉ từ đơn may mắn mới boost** (`cccd` 0.40→0.75, `tài`
  0.30→0.65); cụm nhiều từ (`căn cước`, `số tài khoản`, `địa chỉ`) **không**
  boost. Vì vậy không được dựa vào context booster; phải có **context gate phía
  service** (mục 10.2).
- Analyzer gán nhãn sai với dữ liệu Việt Nam (`0901234567` từng bị gắn
  `UK_NHS` score 1.0). Vì vậy dùng allowlist entity + ưu tiên recognizer VN.
- Anonymizer resolve overlap không đáng tin (chọn span dài nhất vô điều kiện,
  có thể đè lên entity quan trọng). Vì vậy **không dùng anonymizer service**;
  service tự resolve overlap và tự thay thế.
- Stack Presidio: `presidio-analyzer` trong network `presidio_default`, truy cập
  nội bộ tại `http://presidio-analyzer:3000`; từ host `http://localhost:5002`.
- Image analyzer dùng Python 3.12, source `/app`, tôn trọng env `WORKERS`.
- Khối lượng dữ liệu test dự kiến **nhỏ** (vài chục KB/request). Vì vậy mô hình
  đồng bộ với deadline 30 s và các giới hạn ở mục 12 là phù hợp; không cần
  endpoint async/batch ở v1.

## 6. Các hướng đã cân nhắc

### A. Wrapper mỏng gọi stack Presidio sẵn có + recognizer gửi kèm — **được chọn**

- Ưu: không build image mới, tận dụng stack đang chạy, triển khai nhanh, image
  service nhỏ (~120MB).
- Nhược: không có NER tiếng Việt; mỗi chuỗi một HTTP call; recognizer lặp lại
  trong mỗi request.
- Giảm nhẹ: dedupe + chunking + context gate phía service + heuristic tên/địa
  chỉ VN.

### B. Build analyzer riêng có `vi_core_news_lg`

- Ưu: NER tiếng Việt thật, request nhẹ hơn.
- Nhược: image +~500MB, build lâu, chất lượng model tiếng Việt trung bình.
- Người dùng chọn chưa làm; thiết kế để có thể nâng cấp sau mà không đổi API.

### C. Self-contained (nhúng SDK Presidio)

- Ưu: một image, không phụ thuộc network.
- Nhược: ~3GB, build lâu, trùng lặp stack.
- Bị loại vì người dùng muốn service gọi stack Presidio hiện có.

## 7. Kiến trúc

```
Client ──JSON──> pii-api (FastAPI, :8000)
                    │  chunk + dedupe + context gate
                    │  POST /analyze (tuần tự/song song giới hạn)
                    ▼
              presidio-analyzer:3000  (stack có sẵn, 1 worker)
                    │  spans (offset theo chunk → offset gốc)
                    ▼
                 resolver ──> anonymizer (in-process) ──> JSON đã ẩn danh
```

- Service mới nằm trong `/home/hoalt/projects/security-api`, compose riêng.
- Join network ngoài `presidio_default` (external network) để gọi
  `presidio-analyzer:3000`; không mở port analyzer ra ngoài.
- Không phụ thuộc `presidio-anonymizer`.
- Stateless hoàn toàn: không DB, không cache liên request.
- Nếu cần throughput cao hơn: tăng `WORKERS` của analyzer (sửa stack Presidio)
  hoặc nâng lên hướng B; cả hai ngoài phạm vi v1.

## 8. API contract

### 8.1 `POST /v1/anonymize`

Body: JSON bất kỳ (`Content-Type: application/json`).

Query params:

| Param              | Mặc định | Ý nghĩa                                                     |
|--------------------|----------|-------------------------------------------------------------|
| `mode`             | `replace`| `replace` \| `pseudonym` \| `hash`                          |
| `include_findings` | `false`  | `true` → trả wrapper `{data, findings, stats}`              |
| `min_score`        | `0.5`    | Ngưỡng score tối thiểu (0–1); có thể tăng/giảm theo use case |
| `language`         | `en`     | Chỉ `en` được hỗ trợ (ràng buộc model); giá trị khác → 422  |
| `scan_numbers`     | `false`  | Quét cả giá trị số (int/float) dưới dạng chuỗi              |

`recurse_json_strings` là cấu hình env (mặc định `true`), không phải query param.

Response 200:

- Mặc định: chính JSON đã xử lý, giữ nguyên shape/kiểu của field không bị PII.
- `include_findings=true`:

```json
{
  "data": { "...": "..." },
  "findings": [
    {
      "path": "$.messages[0].content",
      "entity_type": "EMAIL_ADDRESS",
      "start": 35,
      "end": 57,
      "score": 1.0,
      "token": "<EMAIL_ADDRESS>"
    },
    {
      "path": "$.messages[0].tool_calls[0].function.arguments#$.phone",
      "entity_type": "VN_PHONE_NUMBER",
      "start": 8,
      "end": 18,
      "score": 0.85,
      "token": "<VN_PHONE_NUMBER>"
    }
  ],
  "stats": {
    "strings_scanned": 14,
    "unique_strings": 10,
    "entities_found": 3,
    "by_type": { "EMAIL_ADDRESS": 1, "VN_PHONE_NUMBER": 2 }
  }
}
```

Quy ước findings:

- **Một finding cho mỗi lần xuất hiện** (mỗi `path`), dù giá trị trùng nhau và
  chỉ được scan một lần. `path` là đường dẫn tới đúng vị trí xuất hiện.
- Với PII nằm trong chuỗi JSON lồng: `path` của chuỗi cha + `#` + JSONPath bên
  trong, ví dụ `$.messages[0].tool_calls[0].function.arguments#$.phone`.
- `start`/`end` là offset **trong chuỗi tại path đó** (không phải offset toàn
  body).
- `token` là chuỗi thay thế; **không bao giờ chứa giá trị gốc**.
- `stats.strings_scanned` đếm số lần xuất hiện; `unique_strings` là số giá trị
  duy nhất đã gọi analyzer; `entities_found` và `by_type` đếm theo số lần xuất
  hiện.
- Thứ tự findings: theo thứ tự duyệt (object giữ thứ tự trong JSON gốc, array
  theo index), rồi theo `start`.

Headers phản hồi: `X-Request-Id` (dùng lại header client gửi nếu có, ngược lại
uuid4), `X-PII-Entities` (tổng số lần xuất hiện).

### 8.2 `POST /v1/detect`

Cùng input/params, nhưng **không thay thế**; trả `{findings, stats}`. Dùng để
audit/đo chất lượng trước khi bật ẩn danh.

### 8.3 `GET /healthz`

Trả `{"status":"ok","analyzer":"ok|unreachable"}`; analyzer unreachable → 503.
Kết quả gọi analyzer được cache 5 s. Service warm up analyzer lúc startup
(không chặn nhận request nếu analyzer chưa sẵn sàng, nhưng log rõ).

### 8.4 Mã lỗi

| HTTP | `code`                  | Khi nào                                             |
|------|-------------------------|-----------------------------------------------------|
| 400  | `invalid_json`          | Body không phải JSON hợp lệ                         |
| 401  | `unauthorized`          | Thiếu/sai `X-API-Key` khi bật auth                  |
| 413  | `payload_too_large`     | Body > `MAX_BODY_BYTES`                             |
| 413  | `too_many_strings`      | > `MAX_STRINGS` giá trị duy nhất                    |
| 413  | `text_too_long`         | Một string > `MAX_TEXT_LEN`                         |
| 413  | `payload_too_deep`      | Depth > `MAX_DEPTH`                                 |
| 413  | `total_text_too_large`  | Tổng ký tự text > `MAX_TOTAL_CHARS`                 |
| 415  | `unsupported_media_type`| `Content-Type` không phải `application/json`        |
| 422  | `invalid_param`         | Query param không hợp lệ                            |
| 502  | `analyzer_error`        | Analyzer trả lỗi/không parse được                   |
| 503  | `analyzer_unavailable`  | Không kết nối được analyzer (fail-closed)           |
| 504  | `analyzer_timeout`      | Một lần gọi analyzer quá `ANALYZER_TIMEOUT_MS`      |
| 504  | `deadline_exceeded`     | Toàn request quá `REQUEST_DEADLINE_MS`              |

Body lỗi: `{"error":{"code":"...","message":"...","request_id":"..."}}`; không
echo payload.

## 9. Xử lý JSON

### 9.1 Duyệt cây

- Chỉ xử lý **giá trị string** mặc định. `scan_numbers=true` thì thêm int/float;
  khi bị thay thế, giá trị trở thành string (ghi rõ trong README).
- Không đổi tên key (non-goal v1). `exclude_keys` áp dụng cho **giá trị** nằm
  dưới key đó (so khớp theo tên key ở bất kỳ cấp nào, không phân biệt hoa thường).
- Bỏ qua string ngắn hơn `MIN_TEXT_LEN` (mặc định 3).
- `exclude_keys` mặc định: `model`, `role`, `object`, `finish_reason`,
  `created`, `system_fingerprint`, `logprobs`.
- `include_paths` / `exclude_paths` (glob kiểu `$.messages[*].content`) tuỳ chọn,
  áp dụng trước khi scan.
- Giới hạn: `MAX_DEPTH=32`, `MAX_STRINGS=500` giá trị duy nhất,
  `MAX_TOTAL_CHARS=512000`, `MAX_TEXT_LEN=64000` mỗi string.
- Path dùng JSONPath đơn giản: `$`, `.key`, `[n]`; key có ký tự đặc biệt dùng
  `["key"]`.
- Thứ tự duyệt xác định: object theo thứ tự xuất hiện trong JSON gốc (Python
  `dict` giữ thứ tự), array theo index tăng dần. Pseudonym và findings dựa trên
  thứ tự này.

### 9.2 Chuỗi chứa JSON lồng (LLM tool-call `arguments`)

Khi `RECURSE_JSON_STRINGS=true` (mặc định):

- Nếu string (sau khi strip) bắt đầu bằng `{` hoặc `[` và `json.loads` thành
  `dict`/`list`, thì đệ quy xử lý như một payload JSON con.
- Nếu không có PII bên trong → **giữ nguyên string gốc từng byte**.
- Nếu có PII → serialize lại bằng `json.dumps(..., ensure_ascii=False)`.
- Giới hạn đệ quy lồng nhau: 3 cấp. Vượt → coi như string thường.
- Nhờ parse, các escape như `\u1ec7` được giải mã trước khi matching.
- `path` của finding bên trong dùng tiền tố `#` như mục 8.1.
- Nếu parse lỗi → coi như string thường (scan như văn bản).

### 9.3 Chunk chuỗi dài

- String dài hơn `CHUNK_SIZE=4000` ký tự được cắt thành các chunk 4000 ký tự,
  overlap `CHUNK_OVERLAP=200`, ưu tiên cắt ở ranh giới câu/xuống dòng gần nhất
  trong 20% cuối chunk.
- Mỗi chunk gọi analyzer riêng; offset kết quả cộng `chunk_start` để về offset
  gốc của string.
- Sau khi gộp, chạy lại resolver (mục 10.4) trên toàn string để loại span trùng
  ở vùng overlap.
- Context gate (mục 10.2) tìm trên **toàn string gốc**, không chỉ trong chunk.

### 9.4 Dedupe và gọi analyzer

- Gom string theo giá trị (sau khi đã tính cả chunk); mỗi giá trị duy nhất +
  mỗi chunk gọi analyzer **một lần**.
- Concurrency mặc định 2 (`ANALYZER_CONCURRENCY`) vì server 1 worker; tăng chỉ
  khi analyzer được scale.
- Timeout 10 s/lần gọi (`ANALYZER_TIMEOUT_MS`), retry 1 lần cho lỗi mạng/5xx
  (không retry 4xx).
- **Deadline toàn request** `REQUEST_DEADLINE_MS=30000`; vượt → hủy các call
  đang chờ, trả 504 `deadline_exceeded`. Không bao giờ trả partial.
- Request analyzer: `POST {ANALYZER_URL}/analyze` với:
  - `text`: chunk
  - `language`: `en`
  - `score_threshold`: `min_score`
  - `ad_hoc_recognizers`: bộ recognizer VN (mục 10.1)
  - `entities`: allowlist đang bật (mục 10.3) để analyzer bỏ nhãn nhiễu
- Lỗi một chuỗi → toàn request fail-closed.

## 10. Phát hiện PII

### 10.1 Recognizer gửi kèm (ad-hoc, mỗi request)

Mỗi recognizer có `name`, `supported_language: "en"`, `supported_entity`,
`patterns[{name, regex, score}]`, `context[]`. Ngoài ra config service có
`require_context` cho các entity dễ nhầm.

Score đặt **đủ cao để tự vượt `min_score`**, vì context booster không đáng tin
với tiếng Việt; việc lọc nhiễu do context gate phía service đảm nhận.

| Entity              | Regex (tóm tắt)                                                        | Score | Require context | Context |
|---------------------|-------------------------------------------------------------------------|-------|-----------------|---------|
| `VN_CCCD`           | `(?<!\d)\d{12}(?!\d)`                                                   | 0.60  | có              | cccd, căn cước, cmnd, chứng minh nhân dân, định danh, số định danh |
| `VN_CMND`           | `(?<!\d)\d{9}(?!\d)`                                                    | 0.60  | có              | cmnd, chứng minh nhân dân |
| `VN_PHONE_NUMBER`   | `(?<!\d)(?:\+84\|0)(?:2\d{9}\|[35789]\d{8})(?!\d)`                       | 0.85  | không           | — |
| `VN_TAX_CODE`       | `(?<!\d)\d{10}(?:[-\u2013]\d{3})?(?!\d)`                                | 0.60  | có              | mst, mã số thuế, tax |
| `VN_BANK_ACCOUNT`   | `(?<!\d)\d{9,16}(?!\d)`                                                 | 0.60  | có              | stk, tài khoản, ngân hàng, bank |
| `VN_PASSPORT`       | `(?<![A-Z0-9])[A-Z]\d{7,8}(?!\d)`                                       | 0.60  | không           | — |
| `VN_VEHICLE_PLATE`  | `(?<!\w)\d{2}[A-Z]-?\d{4,5}(?:\.\d{2})?(?!\w)`                          | 0.60  | không           | — |
| `VN_NAME`           | `\b(?:Nguyễn\|Trần\|Lê\|Phạm\|Hoàng\|Huỳnh\|Phan\|Vũ\|Võ\|Đặng\|Bùi\|Đỗ\|Hồ\|Ngô\|Dương\|Lý\|Đinh\|Trịnh\|Đoàn\|Tô\|Mai\|Lâm\|Phùng\|Vương\|Cao\|Đào\|Tạ\|Hà\|Trương\|Lương)\b(?:\s+[A-ZÀ-Ỹ][a-zà-ỹ]+){1,3}` | 0.60 | không | — |
| `VN_ADDRESS` (đường) | `(?:số\s+)?\d+[A-Za-z]?(?:[/-]\d+)?\s+(?:đường\|phố\|ngõ\|ngách\|hẻm)\s+[^,;\n]{1,60}` | 0.55 | không | — |
| `VN_ADDRESS` (hành chính) | `(?:phường\|xã\|quận\|huyện\|tỉnh\|thành phố\|thị trấn\|thôn\|xóm\|ấp)\s+[A-ZÀ-Ỹ0-9][^,;.\n]{0,40}` | 0.55 | không | — |

Ghi chú:

- Đã kiểm chứng trên stack thật: bộ pattern trên xử lý đúng các ca mẫu (CCCD,
  SĐT, email, STK, MST có `-001`, passport, biển số, tên, địa chỉ nhiều đoạn).
- `VN_CCCD`, `VN_CMND`, `VN_TAX_CODE`, `VN_BANK_ACCOUNT` **bắt buộc có context**
  trong cửa sổ ±60 ký tự quanh match; nếu không → loại. Mục đích: tránh biến mã
  đơn hàng/mã vận đơn thành PII.
- `VN_NAME`, `VN_ADDRESS` là heuristic, có thể false positive/sót (tên không
  dấu, họ lạ, địa chỉ không theo mẫu). Bật mặc định, tắt được qua config.
- Regex dùng Python `re`. Context matching phía service là so khớp substring
  không phân biệt hoa thường trên text gốc.

### 10.2 Context gate phía service

Với mỗi span của entity có `require_context: true`:

1. Lấy cửa sổ `[start-60, end+60]` trên **string gốc**.
2. Lowercase và tìm bất kỳ từ khoá context nào (substring).
3. Không thấy → loại span.

Không dựa vào context booster của Presidio (đã đo: chỉ boost từ đơn, không boost
cụm tiếng Việt).

### 10.3 Recognizer built-in dùng lại

`EMAIL_ADDRESS`, `CREDIT_CARD` (Presidio đã kiểm Luhn), `IP_ADDRESS`, `URL`.

Không dùng mặc định: `PERSON`, `LOCATION`, `ORGANIZATION`, `NRP`, `DATE_TIME`
(model tiếng Anh gán nhãn sai trên văn bản Việt Nam; ví dụ "Vietcombank" bị gắn
`PERSON`, "0312345678-001" bị gắn `DATE_TIME`). Có thể bật lại qua config
`entity_allowlist`.

Allowlist mặc định:

```
VN_CCCD, VN_CMND, VN_PHONE_NUMBER, VN_TAX_CODE, VN_BANK_ACCOUNT,
VN_PASSPORT, VN_VEHICLE_PLATE, VN_NAME, VN_ADDRESS,
EMAIL_ADDRESS, CREDIT_CARD, IP_ADDRESS, URL
```

### 10.4 Resolve overlap (bắt buộc, làm trong service)

1. Bỏ entity không nằm trong allowlist, score < `min_score`, hoặc không đạt
   context gate.
2. Sắp xếp ứng viên theo: **độ dài span giảm dần** → score giảm dần → thứ tự
   ưu tiên trong allowlist → `start` tăng dần.
3. Duyệt lần lượt, chấp nhận span nếu không giao với span đã chấp nhận; ngược
   lại bỏ.
4. Kết quả là danh sách span không chồng lấn, sắp theo `start`.

Ưu tiên span dài trước để thay thế trọn vẹn nhất:

- `VN_TAX_CODE` (13 ký tự, gồm `-001`) thắng `VN_PHONE_NUMBER` (10 ký tự) khớp
  bên trong.
- `VN_ADDRESS` (chứa tên đường) thắng `VN_NAME` lồng bên trong, tránh cảnh chỉ
  che mỗi tên đường mà bỏ phần còn lại.
- Cùng span (ví dụ 12 chữ số vừa CCCD vừa STK) → score bằng nhau, xét thứ tự
  allowlist: `VN_CCCD` thắng `VN_BANK_ACCOUNT`.

## 11. Ẩn danh (in-process)

Sau khi có span cuối cùng, thay thế từ cuối về đầu để không lệch offset.

### 11.1 `mode=replace` (mặc định)

- Thay span bằng `<ENTITY_TYPE>`.
- Ví dụ: `john@x.com` → `<EMAIL_ADDRESS>`, `0901234567` → `<VN_PHONE_NUMBER>`.

### 11.2 `mode=pseudonym`

- Trong phạm vi một request, cùng một giá trị + entity → cùng token.
- Đánh số theo **lần xuất hiện đầu tiên** trong thứ tự duyệt (mục 9.1):
  `<VN_NAME_1>`, `<VN_NAME_2>`, `<VN_PHONE_NUMBER_1>`…
- Không lưu mapping; request sau đánh số lại từ 1.

### 11.3 `mode=hash`

- `sha256(salt + "\x00" + value)`, lấy 16 hex đầu.
- Token: `<ENTITY_TYPE_ab12cd34ef56ab78>`.
- `PII_ANONYMIZE_SALT` lấy từ env; nếu không đặt, dùng salt ngẫu nhiên khởi tạo
  lúc start và log cảnh báo rằng hash không ổn định giữa các lần restart.

Token không bao giờ chứa giá trị gốc. Không hỗ trợ `mask` (giữ ký tự cuối) vì
người dùng yêu cầu ẩn danh hoàn toàn.

## 12. Cấu hình

Tiền tố canonical là `PII_`. Loader chấp nhận cả tên không tiền tố (ví dụ
`ANALYZER_URL`) để tương thích compose/README cũ, nhưng **tên có tiền tố thắng**
và loader log cảnh báo khi gặp tên không tiền tố. Riêng `ANONYMIZE_SALT` không
có tiền tố sẽ bị cảnh báo rõ vì ảnh hưởng tính ổn định của hash.

| Biến                        | Mặc định                        |
|-----------------------------|---------------------------------|
| `PII_ANALYZER_URL`          | `http://presidio-analyzer:3000` |
| `PII_ANALYZER_TIMEOUT_MS`   | `10000`                         |
| `PII_ANALYZER_CONCURRENCY`  | `2`                             |
| `PII_REQUEST_DEADLINE_MS`   | `30000`                         |
| `PII_MAX_BODY_BYTES`        | `5242880` (5MB)                 |
| `PII_MAX_STRINGS`           | `500`                           |
| `PII_MAX_TOTAL_CHARS`       | `512000`                        |
| `PII_MAX_TEXT_LEN`          | `64000`                         |
| `PII_CHUNK_SIZE`            | `4000`                          |
| `PII_CHUNK_OVERLAP`         | `200`                           |
| `PII_MIN_TEXT_LEN`          | `3`                             |
| `PII_MAX_DEPTH`             | `32`                            |
| `PII_DEFAULT_MIN_SCORE`     | `0.5`                           |
| `PII_RECURSE_JSON_STRINGS`  | `true`                          |
| `PII_ANONYMIZE_SALT`        | rỗng (random mỗi lần start)     |
| `PII_API_KEY`               | rỗng = không bật auth           |
| `PII_LOG_LEVEL`             | `info`                          |
| `PII_CONFIG_PATH`           | `app/config/entities.yaml`      |

File `app/config/entities.yaml` chứa: recognizer patterns/context/score,
`require_context`, allowlist, `exclude_keys`, bật/tắt heuristic.

## 13. Bảo mật và vận hành

- Không log payload, không log giá trị gốc, kể cả trong exception. Chỉ log:
  request id, số string, số entity theo loại, thời gian xử lý, mã lỗi.
- Nếu `PII_API_KEY` được đặt: yêu cầu header `X-API-Key`, so sánh constant-time;
  sai → 401.
- Container chạy non-root, chỉ expose cổng 8000.
- Giới hạn body/depth/string/tổng ký tự/deadline để chống payload độc hại và
  bảo vệ analyzer 1 worker.
- Không có persistence → không có dữ liệu PII tồn tại sau request.
- `/healthz` gọi analyzer với timeout ngắn (1 s), cache 5 s.
- Compose healthcheck cho `pii-api`: `start_period: 90s` (analyzer cold start
  ~25 s), `interval: 15s`.

## 14. Kiểm thử

Unit (không cần Presidio):

- Walker: nested object/array, skip keys, path glob, depth/quá số string/quá
  tổng ký tự, number coercion, giữ nguyên kiểu field không bị PII.
- Chunking: cắt đúng, offset sau khi gộp đúng, span ở vùng overlap không bị
  nhân đôi.
- JSON-string recursion: parse/recurse/serialize, giữ nguyên byte khi không có
  PII, path `#`, giới hạn 3 cấp, escape `\u1ec7`.
- Context gate: multi-word tiếng Việt (`căn cước`, `số tài khoản`) hoạt động
  dù Presidio không boost; không context → loại CCCD/STK.
- Resolver: SĐT vs `UK_NHS` vs `PERSON`; MST 13 ký tự thắng SĐT 10 ký tự;
  ADDRESS thắng NAME lồng; cùng span 12 số → CCCD thắng STK.
- Anonymizer: thay thế đúng offset, pseudonym cùng giá trị cùng token, đánh số
  theo lần xuất hiện đầu, hash ổn định với salt cố định, không lộ giá trị gốc.
- Findings: một finding mỗi lần xuất hiện, `stats` đếm occurrence, thứ tự xác
  định.
- Config: parse YAML, override env, cảnh báo tên env không tiền tố.

Integration (mock analyzer bằng `respx`/`httpx.MockTransport`):

- 200 happy path, analyzer 500/503/timeout → fail-closed đúng mã lỗi.
- Dedupe: cùng text chỉ gọi analyzer một lần.
- Deadline: analyzer chậm → 504 `deadline_exceeded`, không partial.
- Concurrency không vượt `PII_ANALYZER_CONCURRENCY`.

Live test (đánh dấu, chạy tay khi stack Presidio bật):

- Payload mẫu tiếng Việt có CCCD, SĐT, email, STK, tên, địa chỉ.
- Ca âm: mã đơn hàng 12 số không có context → **không** bị thay thế.
- Ca MST `0312345678-001` → thay cả cụm, không sót `-001`.
- Ca tool-call `arguments` là chuỗi JSON → PII bên trong bị thay, JSON vẫn hợp lệ.
- Khẳng định: không còn giá trị gốc nào trong output; cấu trúc JSON không đổi.

## 15. Cấu trúc project

```
security-api/
├─ app/
│  ├─ main.py               # FastAPI app, routes, auth, error handling
│  ├─ walker.py             # duyệt JSON, path, chunk, JSON-string recursion
│  ├─ analyzer_client.py    # client httpx, concurrency, retry, timeout
│  ├─ recognizers.py        # sinh ad_hoc_recognizers từ config
│  ├─ context_gate.py       # lọc theo context phía service
│  ├─ resolver.py           # allowlist + overlap resolution
│  ├─ anonymizer.py         # replace / pseudonym / hash
│  ├─ config.py             # load env (PII_ + alias) + YAML
│  └─ config/entities.yaml
├─ tests/
│  ├─ unit/...
│  └─ integration/...
├─ pyproject.toml
├─ Dockerfile
├─ docker-compose.yml
└─ README.md
```

## 16. Triển khai

`docker-compose.yml`:

- service `pii-api`, build từ `Dockerfile`, port `8000:8000`.
- network ngoài: `presidio_default` (external).
- `PII_ANALYZER_URL=http://presidio-analyzer:3000`.
- healthcheck gọi `/healthz`, `start_period: 90s`.

Chạy không dùng Docker: `uvicorn app.main:app --port 8000` với
`PII_ANALYZER_URL=http://localhost:5002`.

`README.md` ghi: cách chạy, ví dụ curl, danh sách entity, giới hạn, cảnh báo
heuristic tiếng Việt, hành vi với chuỗi JSON lồng, và cách bật thêm entity
built-in.

## 17. Tiêu chí hoàn thành

- `docker compose up -d` trong `security-api/` → `GET /healthz` trả 200.
- `POST /v1/anonymize` với payload mẫu tiếng Việt trả JSON đã ẩn danh, không còn
  giá trị PII gốc.
- Mã đơn hàng 12 số không context không bị thay; MST 13 ký tự bị thay trọn cụm.
- Tool-call `arguments` chứa JSON có PII: PII bị thay, JSON con vẫn parse được.
- Analyzer tắt → API trả 503, không trả dữ liệu gốc.
- Request vượt deadline → 504, không partial.
- Unit test + integration test (mock) pass; live test pass khi stack bật.
- Log không chứa nội dung payload.

## 18. Việc để builder tự quyết

- Cấu trúc module cụ thể trong `app/` miễn giữ các ranh giới ở mục 15.
- Thư viện test, style code, cách đóng gói YAML vào image.
- Có dùng `pydantic-settings` hay đọc env thủ công.
- Chi tiết logging format (miễn không log payload).
- Cách hiển thị findings trong README.
- Tối ưu nhỏ về hiệu năng (ví dụ `orjson`), miễn giữ contract.
- Cách chọn điểm cắt chunk trong 20% cuối (theo dấu câu hay xuống dòng).

## 19. Nâng cấp tương lai (không làm bây giờ)

- Thêm `vi_core_news_lg` vào analyzer (image riêng) để có NER tiếng Việt thật;
  API không đổi, chỉ bật `PERSON/LOCATION` trong allowlist.
- Ẩn danh cả tên key, kèm quy tắc xử lý key trùng sau khi thay thế.
- Endpoint batch/async (202 + poll) cho payload lớn.
- Batch endpoint nếu Presidio bổ sung.
- Hỗ trợ file/PDF (qua image-redactor).
