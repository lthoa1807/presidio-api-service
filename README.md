# PII API

Dịch vụ FastAPI nhận JSON bất kỳ (payload API, log, hội thoại LLM...), phát hiện
thông tin cá nhân (PII) — bao gồm dữ liệu Việt Nam — và trả về JSON đã được
anonymize **không thể khôi phục**.

Service này chỉ là lớp wrapper mỏng, gọi Presidio analyzer đang chạy sẵn. Nó
không tự train model, không sửa stack Presidio hiện có, và không dùng
`presidio-anonymizer` (việc thay thế được thực hiện trong tiến trình để xử lý
overlap chính xác).

## Chạy service

Service cần network `presidio_default` (stack Presidio đang chạy) và analyzer
truy cập được tại `http://presidio-analyzer:3000`.

```bash
docker compose pull && docker compose up -d
curl -s http://localhost:8000/healthz
# {"status":"ok","analyzer":"ok"}
```

Image được build và publish tự động bởi GitHub Actions
(`.github/workflows/docker.yml`) lên GHCR mỗi khi push `master`, tag `latest`
+ `sha-<short>`. Compose chỉ pull image, không build tại chỗ.

| Việc cần làm | Lệnh |
| --- | --- |
| Sửa code → push `master`, đợi CI xong | `docker compose pull && docker compose up -d` |
| Chạy đúng một bản đã build | `PII_TAG=sha-<short> docker compose up -d` |
| Chỉ đổi biến môi trường (`.env`, `docker-compose.yml`) | `docker compose up -d` (recreate) |
| Container treo, không đổi code | `docker compose restart pii-api` |

Kiểm tra bản đang chạy đúng code mới:

```bash
docker compose logs --tail=50 pii-api          # xem log khởi động
curl -s http://localhost:8000/healthz          # {"status":"ok","analyzer":"ok"}
docker exec security-api-pii-api-1 grep -c known_given_names /app/app/resolver.py
```

Nếu đổi network/port trong compose: `docker compose down && docker compose up -d`.
Thiếu network sẽ báo lỗi ngay khi `up`:

```bash
docker network ls | grep presidio_default
```

Chạy dev trực tiếp (auto-reload, không Docker), trỏ analyzer ra host port 5002:

```bash
PII_ANALYZER_URL=http://localhost:5002 .venv/bin/uvicorn app.main:app --reload --port 8000
```

Nếu Presidio chưa chạy, khởi động stack đó trước:

```bash
cd /home/hoalt/dockercomposer/Presidio
docker compose up -d
```

Chạy test live (cần service đã lên):

```bash
PII_LIVE_URL=http://localhost:8000 pytest tests/live -v
```

Chạy test offline (không cần Docker):

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e '.[dev]'
pytest -q
```

## API

### `POST /v1/anonymize`

Trả về đúng cấu trúc JSON đầu vào, các giá trị PII được thay bằng token.

```bash
curl -s -X POST http://localhost:8000/v1/anonymize \
  -H 'content-type: application/json' \
  -d '{
    "messages": [
      {"role": "user", "content": "Tôi tên Nguyễn Văn An, CCCD 079203012345, sđt 0901234567, email an.nv@example.com"}
    ]
  }'
```

Kết quả:

```json
{
  "messages": [
    {"role": "user", "content": "Tôi tên <VN_NAME>, CCCD <VN_CCCD>, sđt <VN_PHONE_NUMBER>, email <EMAIL_ADDRESS>"}
  ]
}
```

Thêm `?include_findings=true` để nhận wrapper:

```json
{
  "data": { "...": "..." },
  "findings": [
    {"path": "$.messages[0].content", "entity_type": "VN_NAME", "start": 7, "end": 19, "score": 0.6, "token": "<VN_NAME>"}
  ],
  "stats": {"strings_scanned": 1, "unique_strings": 1, "entities_found": 4, "by_type": {"VN_NAME": 1, "VN_CCCD": 1, "VN_PHONE_NUMBER": 1, "EMAIL_ADDRESS": 1}}
}
```

### `POST /v1/detect`

Chỉ phát hiện, không trả `data`, không làm thay đổi gì:

```bash
curl -s -X POST http://localhost:8000/v1/detect \
  -H 'content-type: application/json' \
  -d '{"note": "gọi 0901234567"}'
```

### `GET /healthz`

`200` khi analyzer OK, `503` khi không kết nối được analyzer.

### Query params

| Param | Giá trị | Mặc định |
| --- | --- | --- |
| `mode` | `replace`, `pseudonym`, `hash` | `replace` |
| `min_score` | `0.0` – `1.0` | `0.5` |
| `language` | `en` (chỉ hỗ trợ) | `en` |
| `scan_numbers` | `true`/`false` — quét cả số JSON | `false` |
| `include_findings` | `true`/`false` — wrapper cho `/v1/anonymize` | `false` |

Chế độ thay thế:

- `replace`: `<EMAIL_ADDRESS>` — mọi lần xuất hiện giống nhau.
- `pseudonym`: `<EMAIL_ADDRESS_1>`, `<EMAIL_ADDRESS_2>` — cùng giá trị trong
  cùng request nhận cùng token, khác request thì khác token.
- `hash`: `<EMAIL_ADDRESS_<16 hex>>` — HMAC-SHA256 với salt
  `PII_ANONYMIZE_SALT`. Nếu salt rỗng, service dùng salt ngẫu nhiên mỗi lần
  khởi động (log warning); hash sẽ không ổn định qua restart.

## Entity hỗ trợ

Cấu hình nằm tại `app/config/entities.yaml`.

| Entity | Nguồn | Ghi chú |
| --- | --- | --- |
| `VN_CCCD` | regex + context | 12 số, cần từ khoá như "cccd", "căn cước" trong ±60 ký tự |
| `VN_CMND` | regex + context | 9 số, cần "cmnd", "chứng minh nhân dân" |
| `VN_PHONE_NUMBER` | regex | Đầu `0`/`+84`, không cần context |
| `VN_TAX_CODE` | regex + context | MST `1234567890` hoặc `1234567890-001` |
| `VN_BANK_ACCOUNT` | regex + context | 9–16 số, cần "stk", "số tài khoản", "ngân hàng" |
| `VN_PASSPORT` | regex | 1 chữ + 7–8 số |
| `VN_DATE_OF_BIRTH` | regex + context hẹp | Chỉ thay phần ngày/năm, giữ nhãn "sinh ngày"; context ±20 ký tự phía trước |
| `VN_VEHICLE_PLATE` | regex | Biển số xe |
| `VN_NAME` | heuristic | Họ phổ biến + 1–3 từ viết hoa; tự ẩn tên gọi đứng riêng (vd. "Khang") khi đã thấy họ tên đầy đủ, kể cả khi tên đầy đủ nằm ở message khác (dùng chung pseudonym) |
| `VN_ADDRESS` | heuristic | Số nhà + đường/phố, hoặc phường/xã/quận/huyện... |
| `EMAIL_ADDRESS`, `CREDIT_CARD`, `IP_ADDRESS`, `URL` | Presidio built-in | Bật/tắt trong `entities.yaml` |

Entity mặc định **không** dùng: `PERSON`, `LOCATION`, `ORGANIZATION`,
`DATE_TIME` của model tiếng Anh vì chúng gán nhãn sai cho tiếng Việt.

### Thêm entity built-in

Thêm vào `entities.yaml`:

```yaml
entities:
  - name: PHONE_NUMBER
    regex: '\+?\d[\d .\-()]{7,}\d'
    score: 0.4
    require_context: false
    context: []
```

hoặc bật entity có sẵn của Presidio bằng cách thêm tên vào `entity_priority` và
không khai báo regex (chỉ áp dụng với entity built-in của Presidio). Nếu cần,
khai báo regex như trên để dùng ad-hoc recognizer.

## Hành vi với JSON

- Cấu trúc và thứ tự key được giữ nguyên; chỉ giá trị string bị thay.
- Số (`int`, `float`), bool, `null` giữ nguyên trừ khi `scan_numbers=true`.
  Khi bật, số được quét như chuỗi và có thể bị thay bằng token string.
- Key không bị quét (trừ khi key đó nằm trong `exclude_keys` — khi đó value bị
  bỏ qua).
- String là JSON lồng nhau (ví dụ `arguments` của tool call LLM) được đệ quy
  tối đa 3 tầng. Nếu bên trong không có PII, string được giữ **nguyên byte**;
  nếu có, string được serialize lại bằng `json.dumps(..., ensure_ascii=False)`.
- Finding có path dạng `$.a`, `$.a[0]`, key đặc biệt là `$["a.b"]`. Với JSON
  lồng nhau: `$.x#$.phone`.

## Giới hạn

| Giới hạn | Mặc định | Env |
| --- | --- | --- |
| Body tối đa | 5 MB | `PII_MAX_BODY_BYTES` |
| Số string unique / request | 500 | `PII_MAX_STRINGS` |
| Tổng ký tự quét | 512.000 | `PII_MAX_TOTAL_CHARS` |
| Độ dài 1 string | 64.000 | `PII_MAX_TEXT_LEN` |
| Độ sâu JSON | 32 | `PII_MAX_DEPTH` |
| Deadline toàn request | 30 s | `PII_REQUEST_DEADLINE_MS` |
| Timeout 1 lần gọi analyzer | 10 s | `PII_ANALYZER_TIMEOUT_MS` |
| Concurrency gọi analyzer | 2 | `PII_ANALYZER_CONCURRENCY` |

Chuỗi dài hơn `chunk_size` (4000) được cắt thành nhiều đoạn overlap 200 ký tự
rồi ghép span lại, nên entity nằm vắt qua ranh giới vẫn được phát hiện một lần.

## Biến môi trường

Tất cả dùng tiền tố `PII_`; tên không tiền tố vẫn được chấp nhận nhưng log
warning và bị `PII_*` ghi đè.

| Biến | Mặc định |
| --- | --- |
| `PII_ANALYZER_URL` | `http://presidio-analyzer:3000` |
| `PII_API_KEY` | rỗng (không auth). Nếu đặt, client phải gửi `X-API-Key` |
| `PII_ANONYMIZE_SALT` | rỗng |
| `PII_LOG_LEVEL` | `info` |
| `PII_CONFIG_PATH` | `app/config/entities.yaml` |

## Cảnh báo

- Nhận diện tên và địa chỉ tiếng Việt là **heuristic**, không phải NER. Tên
  hiếm, không dấu, hoặc viết thường có thể bị bỏ sót. Nếu cần độ chính xác cao
  hơn, dùng thêm model NER tiếng Việt trong Presidio.
- Service không log payload hay giá trị gốc; chỉ log `request_id`, số string
  quét, số entity và thời gian xử lý.
- Anonymize là một chiều. Với `replace`/`hash`, không có cách khôi phục. Với
  `pseudonym`, mapping chỉ tồn tại trong bộ nhớ của một request.
- `mode=hash` với salt rỗng không ổn định qua restart. Đặt
  `PII_ANONYMIZE_SALT` cố định nếu cần hash ổn định giữa các lần chạy.
- Analyzer hiện chạy `gunicorn -w 1` nên xử lý tuần tự. `PII_ANALYZER_CONCURRENCY`
  giới hạn số lần gọi analyzer đồng thời: nó áp dụng giữa các request, và giữa
  các chunk của **một chuỗi dài** (các chunk được gửi song song). Các string
  khác nhau trong cùng một payload vẫn được xử lý tuần tự để giữ thứ tự
  findings và token ổn định, nên payload gồm nhiều string ngắn không được
  song song hóa bên trong request. Nếu cần throughput cao hơn, tăng số worker
  của `presidio-analyzer` (ví dụ `WORKERS=4`) rồi tăng
  `PII_ANALYZER_CONCURRENCY` tương ứng; với payload lớn (hàng trăm KB), nên
  gọi bất đồng bộ và chấp nhận độ trễ vài giây.
