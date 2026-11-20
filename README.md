# PII API

A FastAPI service that accepts arbitrary JSON (API payloads, logs, LLM
conversations, ...), detects personally identifiable information (PII) —
including Vietnamese data — and returns JSON with the PII anonymized in a way
that **cannot be reversed**.

This service is a thin wrapper that calls an already running Presidio analyzer.
It does not train models, does not modify the existing Presidio stack, and does
not use `presidio-anonymizer` (replacement happens in-process so overlaps are
resolved precisely).

## Running the service

The service needs the `presidio_default` network (the running Presidio stack)
and an analyzer reachable at `http://presidio-analyzer:3000`.

```bash
docker compose pull && docker compose up -d
curl -s http://localhost:8000/healthz
# {"status":"ok","analyzer":"ok"}
```

The image is built and published automatically by GitHub Actions
(`.github/workflows/docker.yml`) to GHCR on every push to `master`, tagging
`latest` + `sha-<short>`. Compose only pulls the image; it never builds locally.

| What you need to do | Command |
| --- | --- |
| Changed code → push `master`, wait for CI to finish | `docker compose pull && docker compose up -d` |
| Run exactly one built revision | `PII_TAG=sha-<short> docker compose up -d` |
| Only changed env vars (`.env`, `docker-compose.yml`) | `docker compose up -d` (recreate) |
| Container hung, no code change | `docker compose restart pii-api` |

Verify the running container has the new code:

```bash
docker compose logs --tail=50 pii-api          # startup logs
curl -s http://localhost:8000/healthz          # {"status":"ok","analyzer":"ok"}
docker exec security-api-pii-api-1 grep -c known_given_names /app/app/resolver.py
```

If you change the network/port in compose: `docker compose down && docker compose up -d`.
A missing network fails immediately on `up`:

```bash
docker network ls | grep presidio_default
```

Run dev directly (auto-reload, no Docker) with the analyzer pointed at host port 5002:

```bash
PII_ANALYZER_URL=http://localhost:5002 .venv/bin/uvicorn app.main:app --reload --port 8000
```

If Presidio is not running yet, start that stack first:

```bash
cd /path/to/Presidio
docker compose up -d
```

Run live tests (service must be up):

```bash
PII_LIVE_URL=http://localhost:8000 pytest tests/live -v
```

Run offline tests (no Docker needed):

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e '.[dev]'
pytest -q
```

## API

### `POST /v1/anonymize`

Returns exactly the input JSON structure with PII values replaced by tokens.

```bash
curl -s -X POST http://localhost:8000/v1/anonymize \
  -H 'content-type: application/json' \
  -d '{
    "messages": [
      {"role": "user", "content": "Tôi tên Nguyễn Văn An, CCCD 079203012345, sđt 0901234567, email an.nv@example.com"}
    ]
  }'
```

Result:

```json
{
  "messages": [
    {"role": "user", "content": "Tôi tên <VN_NAME>, CCCD <VN_CCCD>, sđt <VN_PHONE_NUMBER>, email <EMAIL_ADDRESS>"}
  ]
}
```

Add `?include_findings=true` to get a wrapper:

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

Detect only: no `data`, no modification.

```bash
curl -s -X POST http://localhost:8000/v1/detect \
  -H 'content-type: application/json' \
  -d '{"note": "gọi 0901234567"}'
```

### `GET /healthz`

`200` when the analyzer is OK, `503` when it cannot be reached.

### Query params

| Param | Values | Default |
| --- | --- | --- |
| `mode` | `replace`, `pseudonym`, `hash` | `replace` |
| `min_score` | `0.0` – `1.0` | `0.5` |
| `language` | `en` (the only supported value) | `en` |
| `scan_numbers` | `true`/`false` — also scan JSON numbers | `false` |
| `include_findings` | `true`/`false` — wrapper for `/v1/anonymize` | `false` |

Replacement modes:

- `replace`: `<EMAIL_ADDRESS>` — every occurrence is identical.
- `pseudonym`: `<EMAIL_ADDRESS_1>`, `<EMAIL_ADDRESS_2>` — the same value within
  one request gets the same token; a different request gets different tokens.
- `hash`: `<EMAIL_ADDRESS_<16 hex>>` — HMAC-SHA256 with the salt
  `PII_ANONYMIZE_SALT`. If the salt is empty, the service uses a random salt per
  startup (logs a warning), so hashes are not stable across restarts.

## Supported entities

Configuration lives in `app/config/entities.yaml`.

| Entity | Source | Notes |
| --- | --- | --- |
| `VN_CCCD` | regex + context | 12 digits, needs a keyword such as "cccd" or "căn cước" within ±60 chars |
| `VN_CMND` | regex + context | 9 digits, needs "cmnd" or "chứng minh nhân dân" |
| `VN_PHONE_NUMBER` | regex | Starts with `0`/`+84`, no context needed |
| `VN_TAX_CODE` | regex + context | Tax code `1234567890` or `1234567890-001` |
| `VN_BANK_ACCOUNT` | regex + context | 9–16 digits, needs "stk", "số tài khoản", or "ngân hàng" |
| `VN_PASSPORT` | regex | 1 letter + 7–8 digits |
| `VN_DATE_OF_BIRTH` | regex + narrow context | Replaces only the date/year part, keeps the label "sinh ngày"; context within ±20 chars before it |
| `VN_VEHICLE_PLATE` | regex | Vehicle license plate |
| `VN_NAME` | heuristic | Common surname + 1–3 capitalized words; also masks standalone given names (e.g. "Khang") once a full name has been seen, including when the full name is in another message (shared pseudonym) |
| `VN_ADDRESS` | heuristic | House number + street/road, or ward/commune/district/... |
| `EMAIL_ADDRESS`, `CREDIT_CARD`, `IP_ADDRESS`, `URL` | Presidio built-in | Toggled in `entities.yaml` |

These entities are **not** used by default: the English model's `PERSON`,
`LOCATION`, `ORGANIZATION`, and `DATE_TIME`, because they mislabel Vietnamese
text.

### Adding a built-in entity

Add it to `entities.yaml`:

```yaml
entities:
  - name: PHONE_NUMBER
    regex: '\+?\d[\d .\-()]{7,}\d'
    score: 0.4
    require_context: false
    context: []
```

Or enable a built-in Presidio entity by adding its name to `entity_priority`
without declaring a regex (this only applies to Presidio built-in entities). If
needed, declare a regex as above to use an ad-hoc recognizer.

## Behavior with JSON

- Structure and key order are preserved; only string values are replaced.
- Numbers (`int`, `float`), bools, and `null` are preserved unless
  `scan_numbers=true`. When enabled, numbers are scanned as strings and may be
  replaced by a string token.
- Keys are not scanned (unless a key appears in `exclude_keys` — then its value
  is skipped).
- Strings containing nested JSON (e.g. a tool call's `arguments`) are recursed
  up to 3 levels. If there is no PII inside, the string is kept **byte for byte**;
  if there is, the string is re-serialized with `json.dumps(..., ensure_ascii=False)`.
- Finding paths look like `$.a`, `$.a[0]`, with special keys as `$["a.b"]`. For
  nested JSON: `$.x#$.phone`.

## Limits

| Limit | Default | Env |
| --- | --- | --- |
| Max body | 5 MB | `PII_MAX_BODY_BYTES` |
| Unique strings per request | 500 | `PII_MAX_STRINGS` |
| Total characters scanned | 512,000 | `PII_MAX_TOTAL_CHARS` |
| Length of a single string | 64,000 | `PII_MAX_TEXT_LEN` |
| JSON depth | 32 | `PII_MAX_DEPTH` |
| Whole-request deadline | 30 s | `PII_REQUEST_DEADLINE_MS` |
| Timeout for one analyzer call | 10 s | `PII_ANALYZER_TIMEOUT_MS` |
| Analyzer call concurrency | 2 | `PII_ANALYZER_CONCURRENCY` |

Strings longer than `chunk_size` (4000) are split into chunks with 200-character
overlap and the spans are merged back, so an entity spanning a boundary is still
detected once.

## Environment variables

All use the `PII_` prefix; unprefixed names are still accepted but log a warning
and are overridden by `PII_*`.

| Variable | Default |
| --- | --- |
| `PII_ANALYZER_URL` | `http://presidio-analyzer:3000` |
| `PII_API_KEY` | empty (no auth). If set, clients must send `X-API-Key` |
| `PII_ANONYMIZE_SALT` | empty |
| `PII_LOG_LEVEL` | `info` |
| `PII_CONFIG_PATH` | `app/config/entities.yaml` |

## Caveats

- Vietnamese name and address detection is **heuristic**, not NER. Rare,
  unaccented, or lowercase names can be missed. If you need higher accuracy, add
  a Vietnamese NER model to Presidio.
- The service does not log payloads or original values; it only logs
  `request_id`, the number of strings scanned, the number of entities, and the
  processing time.
- Anonymization is one-way. With `replace`/`hash` there is no way to recover the
  original. With `pseudonym`, the mapping exists only in the memory of a single
  request.
- `mode=hash` with an empty salt is not stable across restarts. Set a fixed
  `PII_ANONYMIZE_SALT` if you need stable hashes between runs.
- The analyzer currently runs `gunicorn -w 1`, so it processes sequentially.
  `PII_ANALYZER_CONCURRENCY` caps the number of concurrent analyzer calls: it
  applies across requests and across the chunks of **one long string** (chunks
  are sent in parallel). Different strings within the same payload are still
  processed sequentially so findings order and tokens stay stable, so a payload
  of many short strings is not parallelized inside a request. If you need more
  throughput, raise the `presidio-analyzer` worker count (e.g. `WORKERS=4`) and
  raise `PII_ANALYZER_CONCURRENCY` accordingly; for large payloads (hundreds of
  KB), call it asynchronously and accept a few seconds of latency.
