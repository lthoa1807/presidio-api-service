# PII API Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Xây API FastAPI nhận JSON bất kỳ, gọi Presidio analyzer đang chạy để phát hiện PII (có cấu trúc + heuristic tiếng Việt) và trả JSON đã ẩn danh không thể khôi phục.

**Architecture:** Service độc lập, không dùng `presidio-anonymizer`. Mỗi payload được duyệt đệ quy thành các leaf string, dedupe, chunk chuỗi dài, gọi analyzer qua HTTP (tuần tự/giới hạn concurrency), lọc context phía service, tự resolve overlap, rồi thay thế in-process. Fail-closed khi analyzer lỗi.

**Tech Stack:** Python 3.11, FastAPI, httpx, Pydantic v2, PyYAML, uvicorn, pytest, pytest-asyncio, respx.

**Spec:** `docs/superpowers/specs/2026-11-19-presidio-pii-api-design.md`

## Global Constraints

- Không sửa stack Presidio, không gọi `presidio-anonymizer`.
- Analyzer URL mặc định `http://presidio-analyzer:3000`; giả định 1 worker, xử lý tuần tự.
- Không log payload hoặc giá trị gốc ở bất kỳ đâu.
- Fail-closed: mọi lỗi analyzer → HTTP 5xx, không trả dữ liệu gốc/partial.
- Env canonical tiền tố `PII_`; chấp nhận alias không tiền tố nhưng tên có tiền tố thắng và phải log warning.
- `mode` chỉ nhận `replace`, `pseudonym`, `hash`; `language` chỉ nhận `en`.
- Giới hạn mặc định: body 5 MB, 500 string duy nhất, 512 000 ký tự text, 64 000 ký tự/string, depth 32, chunk 4 000/overlap 200, deadline 30 s, analyzer timeout 10 s, concurrency 2.
- Findings: một entry cho mỗi lần xuất hiện; `stats.strings_scanned` và `by_type` đếm occurrence; `stats.unique_strings` đếm giá trị duy nhất.
- Không đổi tên key; không ẩn danh key trong v1.
- `json.dumps(..., ensure_ascii=False)` khi phải serialize lại JSON lồng.

## Review Focus

- Số điện thoại gửi dạng số (`0901234567` là int) với `scan_numbers=false`: giữ nguyên kiểu và giá trị.
- Cùng một giá trị xuất hiện ở nhiều path: pseudonym phải cùng token, findings phải có đủ entry theo từng path.
- Entity nằm vắt qua ranh giới chunk (email/điện thoại): không được nhân đôi hoặc mất.
- String là JSON lồng không chứa PII: phải giữ nguyên từng byte, không reformat.
- Analyzer trả nhãn chồng nhau/sai (`UK_NHS` cho SĐT, `PERSON` phủ lên): resolver phải chọn đúng entity allowlist, ưu tiên span dài.
- Analyzer lỗi giữa chừng: request phải fail toàn bộ, không trả partial.
- Key/chuỗi có ký tự đặc biệt hoặc Unicode: path trong findings và offset thay thế phải đúng.

---

### Task 1: Scaffold project + config loader

**Files:**
- Create: `pyproject.toml`, `.gitignore`, `app/__init__.py`, `app/config/entities.yaml`
- Create: `app/config.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `@dataclass(frozen=True) EntityConfig(name: str, regex: str, score: float, require_context: bool, context: tuple[str, ...], enabled: bool = True)`
  - `@dataclass(frozen=True) Settings` với các field: `analyzer_url: str`, `analyzer_timeout_s: float`, `analyzer_concurrency: int`, `request_deadline_s: float`, `max_body_bytes: int`, `max_strings: int`, `max_total_chars: int`, `max_text_len: int`, `chunk_size: int`, `chunk_overlap: int`, `min_text_len: int`, `max_depth: int`, `default_min_score: float`, `recurse_json_strings: bool`, `anonymize_salt: str`, `api_key: str`, `log_level: str`, `config_path: Path`, `exclude_keys: frozenset[str]`, `entity_priority: tuple[str, ...]`, `entities: tuple[EntityConfig, ...]`
  - `load_settings(env: Mapping[str, str] | None = None) -> Settings`
  - `load_entities(path: Path) -> tuple[tuple[EntityConfig, ...], tuple[str, ...], frozenset[str]]` trả `(entities, entity_priority, exclude_keys)`

- [ ] **Step 0: Khởi tạo repo và dependency**

```bash
cd /home/hoalt/projects/security-api
git init
```

`pyproject.toml` khai báo `requires-python = ">=3.11"`, dependencies: `fastapi>=0.115`, `uvicorn[standard]>=0.30`, `httpx>=0.27`, `pydantic>=2.7`, `pyyaml>=6.0`; dev: `pytest>=8`, `pytest-asyncio>=0.23`, `respx>=0.21`, `ruff>=0.5`. Cấu hình `[tool.pytest.ini_options] asyncio_mode = "auto"` và `markers = ["live: needs running Presidio stack"]`.

- [ ] **Step 1: Viết test config thất bại**

`tests/test_config.py`:

```python
from pathlib import Path
from app.config import load_settings, load_entities

def test_env_prefix_and_alias_prefixed_wins(monkeypatch):
    monkeypatch.setenv("PII_ANALYZER_CONCURRENCY", "5")
    monkeypatch.setenv("ANALYZER_CONCURRENCY", "9")
    assert load_settings().analyzer_concurrency == 5

def test_defaults():
    s = load_settings(env={})
    assert s.analyzer_url == "http://presidio-analyzer:3000"
    assert s.chunk_size == 4000 and s.chunk_overlap == 200
    assert s.max_strings == 500 and s.request_deadline_s == 30.0
    assert s.recurse_json_strings is True

def test_entities_yaml_loads_priority_and_excludes():
    entities, priority, exclude = load_entities(Path("app/config/entities.yaml"))
    names = {e.name for e in entities}
    assert {"VN_CCCD", "VN_PHONE_NUMBER", "VN_ADDRESS"} <= names
    assert priority[0] == "VN_CCCD"
    assert "role" in exclude
```

- [ ] **Step 2: Chạy test để chắc chắn fail**

Run: `pytest tests/test_config.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app'` / `app.config`.

- [ ] **Step 3: Viết `app/config.py` + `app/config/entities.yaml`**

`load_settings` đọc `PII_<NAME>` trước; nếu không có thì đọc `<NAME>` và `logger.warning` (đặc biệt `ANONYMIZE_SALT`). Cast kiểu cho int/float/bool (`"true"/"1"/"yes"`). `load_entities` parse YAML với các section `entities`, `entity_priority`, `exclude_keys`; regex giữ nguyên chuỗi, không compile.

`entities.yaml` chứa đúng bảng recognizer ở spec mục 10.1 (9 pattern, gồm 2 pattern `VN_ADDRESS` cùng entity), `require_context` cho `VN_CCCD`/`VN_CMND`/`VN_TAX_CODE`/`VN_BANK_ACCOUNT`, `entity_priority` đúng thứ tự allowlist spec 10.3, `exclude_keys` đúng spec 9.1.

- [ ] **Step 4: Chạy test pass**

Run: `pytest tests/test_config.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml .gitignore app tests
git commit -m "feat: scaffold project and config loader"
```

---

### Task 2: JSON walker và giới hạn payload

**Files:**
- Create: `app/walker.py`
- Test: `tests/test_walker.py`

**Interfaces:**
- Consumes: `Settings`, `EntityConfig` từ Task 1.
- Produces:
  - `@dataclass(frozen=True) Leaf(path: str, tokens: tuple[str | int, ...], text: str, kind: str)`
  - `class PayloadLimitError(Exception)` với attribute `code: str`
  - `collect_leaves(data: Any, settings: Settings, *, scan_numbers: bool = False, include_paths: Sequence[str] = (), exclude_paths: Sequence[str] = ()) -> list[Leaf]`
  - `set_value(data: Any, tokens: Sequence[str | int], value: Any) -> None`
  - Path dạng `$`, `$.a`, `$.a[0]`; key đặc biệt dùng `$["a.b"]`.

- [ ] **Step 1: Viết test thất bại**

`tests/test_walker.py`:

```python
import dataclasses
import pytest
from app.config import load_settings
from app.walker import collect_leaves, set_value, PayloadLimitError

S = load_settings(env={})

def test_collects_nested_strings_in_order_with_paths():
    data = {"a": "xin chào", "b": ["một", {"c": "hai"}], "n": 123}
    leaves = collect_leaves(data, S)
    assert [(l.path, l.text) for l in leaves] == [
        ("$.a", "xin chào"), ("$.b[0]", "một"), ("$.b[1].c", "hai")]
    assert leaves[0].tokens == ("a",)
    assert leaves[0].kind == "str"

def test_numbers_only_when_scan_numbers_true():
    assert collect_leaves({"n": 123}, S) == []
    leaves = collect_leaves({"n": 123}, S, scan_numbers=True)
    assert leaves[0].kind == "int" and leaves[0].text == "123"

def test_skip_excluded_keys_and_min_length():
    leaves = collect_leaves({"role": "user", "x": "ab", "y": "abc"}, S)
    assert [l.text for l in leaves] == ["abc"]

def test_exclude_path_glob():
    leaves = collect_leaves({"a": {"secret": "abc"}, "b": "xyz"}, S,
                            exclude_paths=("$.a.*",))
    assert [l.path for l in leaves] == ["$.b"]

def test_special_key_uses_bracket_path():
    data = {"a.b": "abc", "tên": "xyz"}
    leaves = collect_leaves(data, S)
    assert [l.path for l in leaves] == ['$["a.b"]', "$.tên"]
    set_value(data, ("a.b",), "<X>")
    assert data == {"a.b": "<X>", "tên": "xyz"}

def test_depth_limit_raises_payload_too_deep():
    data = {"a": {"b": {"c": {"d": {"e": "x"}}}}}
    with pytest.raises(PayloadLimitError) as e:
        collect_leaves(data, dataclasses.replace(S, max_depth=3))
    assert e.value.code == "payload_too_deep"

def test_too_many_unique_strings_raises():
    data = {str(i): f"value-{i}" for i in range(5)}
    with pytest.raises(PayloadLimitError) as e:
        collect_leaves(data, dataclasses.replace(S, max_strings=3))
    assert e.value.code == "too_many_strings"

def test_set_value_nested():
    data = {"a": [{"b": "x"}]}
    set_value(data, ("a", 0, "b"), "<X>")
    assert data == {"a": [{"b": "<X>"}]}
```

- [ ] **Step 2: Run fail**

Run: `pytest tests/test_walker.py -v`
Expected: FAIL — `ModuleNotFoundError: app.walker`.

- [ ] **Step 3: Implement `app/walker.py`**

Duyệt `dict` theo thứ tự insertion, `list` theo index. Chỉ lấy `str` có `len >= min_text_len`, trừ key trong `exclude_keys` và string dưới path bị exclude. Đếm **giá trị duy nhất** để áp `max_strings`; đếm tổng ký tự để áp `max_total_chars`; mỗi string vượt `max_text_len` → `text_too_long`; depth vượt → `payload_too_deep`. `set_value` đi theo `tokens`, mutate tại chỗ.

- [ ] **Step 4: Run pass**

Run: `pytest tests/test_walker.py -v`
Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add app/walker.py tests/test_walker.py
git commit -m "feat: add JSON walker with payload limits"
```

---

### Task 3: Chunking chuỗi dài

**Files:**
- Create: `app/chunker.py`
- Test: `tests/test_chunker.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `chunk_text(text: str, *, chunk_size: int, overlap: int) -> list[tuple[int, str]]` — trả `(start_offset, chunk)`; `start_offset` tăng dần; chunk cuối kết thúc đúng `len(text)`.

- [ ] **Step 1: Viết test thất bại**

```python
from app.chunker import chunk_text

def test_short_text_is_one_chunk():
    assert chunk_text("abc", chunk_size=10, overlap=2) == [(0, "abc")]

def test_long_text_covers_everything_and_respects_overlap():
    text = "a" * 25
    chunks = chunk_text(text, chunk_size=10, overlap=3)
    assert chunks[0][0] == 0
    assert chunks[-1][0] + len(chunks[-1][1]) == len(text)
    for (s1, c1), (s2, c2) in zip(chunks, chunks[1:]):
        assert s2 <= s1 + len(c1)          # overlap
        assert s2 + len(c2) > s1 + len(c1) # no gap
        assert len(c2) <= 10

def test_prefers_sentence_boundary_near_end():
    text = "mot hai ba bon. " * 3   # 48 chars
    chunks = chunk_text(text, chunk_size=20, overlap=4)
    assert chunks[0][1].endswith(".")
```

- [ ] **Step 2: Run fail**

Run: `pytest tests/test_chunker.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement**

Cắt `chunk_size`; trong 20% cuối chunk, lùi về dấu `.`/`!`/`?`/`\n` gần nhất; không lùi quá 20% để tránh chunk quá ngắn. Chunk sau bắt đầu ở `prev_end - overlap`, clamp `>= previous_start + 1`.

- [ ] **Step 4: Run pass**

Run: `pytest tests/test_chunker.py -v`
Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add app/chunker.py tests/test_chunker.py
git commit -m "feat: add sentence-aware text chunking"
```

---

### Task 4: Ad-hoc recognizers + context gate

**Files:**
- Create: `app/recognizers.py`, `app/context_gate.py`
- Test: `tests/test_recognizers.py`, `tests/test_context_gate.py`

**Interfaces:**
- Consumes: `EntityConfig`.
- Produces:
  - `to_ad_hoc_recognizers(entities: Sequence[EntityConfig]) -> list[dict[str, Any]]` — bỏ entity `enabled=False`, gộp pattern cùng entity thành một recognizer.
  - `context_ok(text: str, start: int, end: int, entity: EntityConfig, *, window: int = 60) -> bool` — `True` nếu `require_context=False` hoặc có từ khoá trong cửa sổ.

- [ ] **Step 1: Viết test thất bại**

```python
from pathlib import Path
from app.config import EntityConfig, load_entities
from app.recognizers import to_ad_hoc_recognizers
from app.context_gate import context_ok

CFG = Path("app/config/entities.yaml")

def test_groups_patterns_by_entity():
    recs = to_ad_hoc_recognizers(load_entities(CFG)[0])
    addr = next(r for r in recs if r["supported_entity"] == "VN_ADDRESS")
    assert len(addr["patterns"]) == 2
    assert addr["supported_language"] == "en"

def test_multi_word_vietnamese_context_matches():
    e = EntityConfig("VN_CCCD", r"(?<!\d)\d{12}(?!\d)", 0.6, True,
                     ("cccd", "căn cước", "chứng minh nhân dân"))
    assert context_ok("Số căn cước là 079203012345", 16, 28, e) is True

def test_missing_context_rejected():
    e = EntityConfig("VN_CCCD", r"\d{12}", 0.6, True, ("cccd", "căn cước"))
    assert context_ok("Mã đơn 079203012345 đã giao", 8, 20, e) is False

def test_entity_without_require_context_always_ok():
    e = EntityConfig("VN_PHONE_NUMBER", r"\d{10}", 0.85, False, ())
    assert context_ok("0901234567", 0, 10, e) is True
```

- [ ] **Step 2: Run fail**

Run: `pytest tests/test_recognizers.py tests/test_context_gate.py -v`
Expected: FAIL — modules missing.

- [ ] **Step 3: Implement**

`to_ad_hoc_recognizers` xuất đúng schema Presidio: `{"name": "Vn" + entity.title().replace("_",""), "supported_language": "en", "supported_entity": entity, "patterns": [{"name": ..., "regex": ..., "score": ...}], "context": list(context)}`. `context_ok` lowercases cả hai phía, so khớp substring trong `text[max(0,start-window):end+window]`.

- [ ] **Step 4: Run pass**

Run: `pytest tests/test_recognizers.py tests/test_context_gate.py -v`
Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add app/recognizers.py app/context_gate.py tests/test_recognizers.py tests/test_context_gate.py
git commit -m "feat: add VN recognizers and service-side context gate"
```

---

### Task 5: Resolver overlap

**Files:**
- Create: `app/resolver.py`
- Test: `tests/test_resolver.py`

**Interfaces:**
- Consumes: `Span` (Task 6 sẽ định nghĩa; trong task này tạo `app/models.py` với `@dataclass(frozen=True) Span(start: int, end: int, entity_type: str, score: float)`), `context_ok`.
- Produces: `resolve(text: str, spans: Iterable[Span], *, allowlist: Sequence[str], min_score: float, entities_by_name: Mapping[str, EntityConfig]) -> list[Span]`.

- [ ] **Step 1: Viết test thất bại**

```python
from app.models import Span
from app.resolver import resolve

ALLOW = ["VN_CCCD", "VN_PHONE_NUMBER", "VN_TAX_CODE", "VN_ADDRESS", "VN_NAME", "EMAIL_ADDRESS"]
E = {}  # entities_by_name rỗng = không gate

def test_filters_not_allowlisted_and_low_score():
    out = resolve("x", [Span(0, 1, "UK_NHS", 1.0), Span(0, 1, "VN_CCCD", 0.4)], 
                  allowlist=ALLOW, min_score=0.5, entities_by_name=E)
    assert out == []

def test_longest_span_wins_tax_code_over_phone():
    text = "MST 0312345678-001"
    out = resolve(text, [Span(4, 14, "VN_PHONE_NUMBER", 0.85),
                         Span(4, 17, "VN_TAX_CODE", 0.6)],
                  allowlist=ALLOW, min_score=0.5, entities_by_name=E)
    assert [s.entity_type for s in out] == ["VN_TAX_CODE"]

def test_address_beats_nested_name():
    text = "số 12 đường Nguyễn Huệ"
    out = resolve(text, [Span(0, 21, "VN_ADDRESS", 0.55),
                         Span(13, 21, "VN_NAME", 0.6)],
                  allowlist=ALLOW, min_score=0.5, entities_by_name=E)
    assert [s.entity_type for s in out] == ["VN_ADDRESS"]

def test_same_span_uses_allowlist_order():
    out = resolve("x", [Span(0, 12, "VN_PHONE_NUMBER", 0.6),
                        Span(0, 12, "VN_CCCD", 0.6)],
                  allowlist=ALLOW, min_score=0.5, entities_by_name=E)
    assert [s.entity_type for s in out] == ["VN_CCCD"]

def test_non_overlapping_sorted_by_start():
    out = resolve("x", [Span(5, 10, "VN_NAME", 0.6), Span(0, 3, "VN_NAME", 0.6)],
                  allowlist=ALLOW, min_score=0.5, entities_by_name=E)
    assert [s.start for s in out] == [0, 5]
```

- [ ] **Step 2: Run fail**

Run: `pytest tests/test_resolver.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement**

Lọc allowlist/score/context (chỉ gate khi entity có trong `entities_by_name`). Sort key: `(-(end-start), -score, allowlist.index(entity), start)`. Nhận span nếu không giao span đã nhận; trả sort theo `start`.

- [ ] **Step 4: Run pass**

Run: `pytest tests/test_resolver.py -v`
Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add app/models.py app/resolver.py tests/test_resolver.py
git commit -m "feat: add span resolver with longest-first overlap policy"
```

---

### Task 6: Analyzer client

**Files:**
- Create: `app/analyzer_client.py`
- Test: `tests/test_analyzer_client.py`

**Interfaces:**
- Consumes: `Span` từ `app/models.py`.
- Produces:
  - `class AnalyzerError(Exception)` (lỗi 4xx/parse), `class AnalyzerUnavailable(AnalyzerError)`, `class AnalyzerTimeout(AnalyzerError)`
  - `class AnalyzerClient` với `__init__(self, base_url: str, *, timeout_s: float, concurrency: int, client: httpx.AsyncClient | None = None)`, `async analyze(text, *, min_score, entities, recognizers) -> list[Span]`, `async health() -> bool`, `async aclose() -> None`

- [ ] **Step 1: Viết test thất bại**

```python
import httpx, pytest
from app.analyzer_client import AnalyzerClient, AnalyzerUnavailable, AnalyzerTimeout

def transport(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))

async def test_success_maps_response():
    def h(req):
        return httpx.Response(200, json=[{"start": 0, "end": 4, "entity_type": "VN_NAME", "score": 0.9}])
    c = AnalyzerClient("http://x", timeout_s=1, concurrency=2, client=transport(h))
    assert (await c.analyze("abcd", min_score=0.5, entities=["VN_NAME"], recognizers=[]))[0].entity_type == "VN_NAME"

async def test_5xx_retries_once_then_unavailable():
    calls = []
    def h(req):
        calls.append(1); return httpx.Response(500)
    c = AnalyzerClient("http://x", timeout_s=1, concurrency=2, client=transport(h))
    with pytest.raises(AnalyzerUnavailable):
        await c.analyze("abcd", min_score=0.5, entities=[], recognizers=[])
    assert len(calls) == 2

async def test_timeout_maps_to_analyzer_timeout():
    def h(req): raise httpx.ReadTimeout("slow")
    c = AnalyzerClient("http://x", timeout_s=0.01, concurrency=1, client=transport(h))
    with pytest.raises(AnalyzerTimeout):
        await c.analyze("abcd", min_score=0.5, entities=[], recognizers=[])
```

- [ ] **Step 2: Run fail**

Run: `pytest tests/test_analyzer_client.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement**

`POST {base_url}/analyze` body `{"text","language":"en","score_threshold","entities","ad_hoc_recognizers"}`. `asyncio.Semaphore(concurrency)`. Retry 1 lần cho `httpx.TransportError` và status >= 500; không retry 4xx. Timeout/transport → `AnalyzerTimeout`; 4xx/JSON lỗi → `AnalyzerError`; 5xx sau retry → `AnalyzerUnavailable`. `health()` gọi `GET {base_url}/health` timeout 1 s, trả bool.

- [ ] **Step 4: Run pass**

Run: `pytest tests/test_analyzer_client.py -v`
Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add app/analyzer_client.py tests/test_analyzer_client.py
git commit -m "feat: add analyzer HTTP client with retry and semaphore"
```

---

### Task 7: Anonymizer

**Files:**
- Create: `app/anonymizer.py`
- Test: `tests/test_anonymizer.py`

**Interfaces:**
- Consumes: `Span`.
- Produces:
  - `@dataclass(frozen=True) AppliedSpan(start: int, end: int, entity_type: str, score: float, token: str)`
  - `class PseudonymState` với `token(self, entity_type: str, value: str) -> str`
  - `anonymize_text(text: str, spans: Sequence[Span], *, mode: str, salt: str, state: PseudonymState) -> tuple[str, list[AppliedSpan]]`

- [ ] **Step 1: Viết test thất bại**

```python
def test_replace():
    out, applied = anonymize_text("mail a@b.com!", [Span(5, 12, "EMAIL_ADDRESS", 1.0)],
                                  mode="replace", salt="", state=PseudonymState())
    assert out == "mail <EMAIL_ADDRESS>!"
    assert applied[0].token == "<EMAIL_ADDRESS>"

def test_pseudonym_same_value_same_token():
    st = PseudonymState()
    t = "A A B"
    out, _ = anonymize_text(t, [Span(0, 1, "VN_NAME", .6), Span(2, 3, "VN_NAME", .6),
                                Span(4, 5, "VN_NAME", .6)],
                            mode="pseudonym", salt="", state=st)
    assert out == "<VN_NAME_1> <VN_NAME_1> <VN_NAME_2>"

def test_hash_stable_with_salt_and_hides_value():
    a, _ = anonymize_text("0901234567", [Span(0, 10, "VN_PHONE_NUMBER", .85)],
                          mode="hash", salt="s3", state=PseudonymState())
    b, _ = anonymize_text("0901234567", [Span(0, 10, "VN_PHONE_NUMBER", .85)],
                          mode="hash", salt="s3", state=PseudonymState())
    assert a == b and "0901234567" not in a and a.startswith("<VN_PHONE_NUMBER_")

def test_offsets_applied_right_to_left():
    out, _ = anonymize_text("ab cd", [Span(0, 2, "VN_NAME", .6), Span(3, 5, "VN_NAME", .6)],
                            mode="replace", salt="", state=PseudonymState())
    assert out == "<VN_NAME> <VN_NAME>"
```

- [ ] **Step 2: Run fail**

Run: `pytest tests/test_anonymizer.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement**

Thay từ phải sang trái. `PseudonymState` giữ `dict[(entity, value)] -> token`, đếm theo entity. `hash` dùng `hashlib.sha256((salt + "\x00" + value).encode()).hexdigest()[:16]`. Token dạng `<{TYPE}>`, `<{TYPE}_{n}>`, `<{TYPE}_{hash}>`.

- [ ] **Step 4: Run pass**

Run: `pytest tests/test_anonymizer.py -v`
Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add app/anonymizer.py tests/test_anonymizer.py
git commit -m "feat: add in-process replacer, pseudonymizer and hasher"
```

---

### Task 8: Pipeline (dedupe, chunk, JSON recursion, findings)

**Files:**
- Create: `app/pipeline.py`
- Test: `tests/test_pipeline.py`

**Interfaces:**
- Consumes: `collect_leaves`, `set_value`, `chunk_text`, `to_ad_hoc_recognizers`, `resolve`, `anonymize_text`, `AnalyzerClient` (chỉ cần method `analyze`; test dùng fake có cùng signature).
- Produces:
  - `@dataclass ProcessOptions(mode: str = "replace", min_score: float = 0.5, scan_numbers: bool = False)`
  - `@dataclass(frozen=True) Finding(path: str, entity_type: str, start: int, end: int, score: float, token: str)`
  - `@dataclass ProcessResult(data: Any, findings: list[Finding], stats: dict[str, Any])`
  - `async process_payload(data: Any, options: ProcessOptions, settings: Settings, analyzer: AnalyzerClient, *, include_paths=(), exclude_paths=()) -> ProcessResult`

- [ ] **Step 1: Viết test thất bại**

```python
import json
from app.config import load_settings
from app.models import Span
from app.pipeline import process_payload, ProcessOptions

S = load_settings(env={})

class FakeAnalyzer:
    def __init__(self, hits): self.hits, self.calls = hits, []
    async def analyze(self, text, *, min_score, entities, recognizers):
        self.calls.append(text)
        return [Span(s, e, t, sc) for s, e, t, sc in self.hits.get(text, [])]

async def test_dedupes_identical_values_but_emits_finding_per_path():
    a = FakeAnalyzer({"john@x.com": [(0, 11, "EMAIL_ADDRESS", 1.0)]})
    res = await process_payload({"a": "john@x.com", "b": ["john@x.com"]},
                                ProcessOptions(), S, a)
    assert a.calls == ["john@x.com"]
    assert res.data == {"a": "<EMAIL_ADDRESS>", "b": ["<EMAIL_ADDRESS>"]}
    assert [f.path for f in res.findings] == ["$.a", "$.b[0]"]
    assert res.stats == {"strings_scanned": 2, "unique_strings": 1,
                         "entities_found": 2, "by_type": {"EMAIL_ADDRESS": 2}}

async def test_chunk_boundary_does_not_duplicate_entity():
    text = "x" * 3995 + "a@b.com"
    class ChunkFake:
        calls: list[str] = []
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
    a = FakeAnalyzer({"0901234567": [(0, 10, "VN_PHONE_NUMBER", .85)]})
    res = await process_payload({"x": '{"phone":"0901234567"}'}, ProcessOptions(), S, a)
    assert json.loads(res.data["x"]) == {"phone": "<VN_PHONE_NUMBER>"}
    assert res.findings[0].path == '$.x#$.phone'

async def test_numbers_untouched_by_default():
    res = await process_payload({"n": 123, "f": 1.5}, ProcessOptions(), S, FakeAnalyzer({}))
    assert res.data == {"n": 123, "f": 1.5}
    assert res.findings == []
```

- [ ] **Step 2: Run fail**

Run: `pytest tests/test_pipeline.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement**

Luồng:
1. `collect_leaves` (bỏ qua leaf nằm trong JSON string cho tới bước 3).
2. Nếu `recurse_json_strings`: với leaf bắt đầu `{`/`[` và parse được, duyệt payload con bằng đệ quy nội bộ (max 3 cấp), tiền tố path `parent#...`; nếu không có thay đổi giữ nguyên string gốc.
3. Với mỗi giá trị duy nhất: chunk, gọi analyzer cho từng chunk, cộng offset, gộp, rồi `resolve`.
4. Thay thế trong từng leaf; áp `set_value`.
5. Dựng findings theo occurrence, stats theo spec.

Chunk của email trong test sẽ nằm trọn trong một chunk (vì cắt ưu tiên ranh giới câu và overlap 200), nên fake chỉ trả hit khi `text.find("a@b.com") >= 0`.

- [ ] **Step 4: Run pass**

Run: `pytest tests/test_pipeline.py -v`
Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add app/pipeline.py tests/test_pipeline.py
git commit -m "feat: orchestrate dedupe, chunking, recursion and findings"
```

---

### Task 9: HTTP API

**Files:**
- Create: `app/main.py`
- Test: `tests/test_api.py`

**Interfaces:**
- Consumes: `process_payload`, `load_settings`, `AnalyzerClient`.
- Produces: `create_app(settings: Settings | None = None, *, analyzer: Any | None = None) -> FastAPI` với routes `POST /v1/anonymize`, `POST /v1/detect`, `GET /healthz`; attribute `app.state.analyzer`.

- [ ] **Step 1: Viết test thất bại**

```python
import dataclasses
import pytest
from fastapi.testclient import TestClient
from app.analyzer_client import AnalyzerUnavailable
from app.config import load_settings
from app.models import Span
import app.main as m

class FakeAnalyzer:
    def __init__(self, hits=None, fail=False): self.hits, self.fail = hits or {}, fail
    async def health(self): return True
    async def analyze(self, text, *, min_score, entities, recognizers):
        if self.fail: raise AnalyzerUnavailable("down")
        return [Span(s, e, t, sc) for s, e, t, sc in self.hits.get(text, [])]

def client(analyzer, settings=None):
    return TestClient(m.create_app(settings=settings or load_settings(env={}),
                                   analyzer=analyzer))

def test_healthz_ok():
    assert client(FakeAnalyzer()).get("/healthz").json() == {"status": "ok", "analyzer": "ok"}

def test_anonymize_returns_raw_json():
    r = client(FakeAnalyzer({"john@x.com": [(0, 11, "EMAIL_ADDRESS", 1.0)]})) \
        .post("/v1/anonymize", json={"m": "john@x.com"})
    assert r.status_code == 200 and r.json() == {"m": "<EMAIL_ADDRESS>"}

def test_include_findings_wrapper():
    r = client(FakeAnalyzer({"john@x.com": [(0, 11, "EMAIL_ADDRESS", 1.0)]})) \
        .post("/v1/anonymize?include_findings=true", json={"m": "john@x.com"})
    body = r.json()
    assert body["data"] == {"m": "<EMAIL_ADDRESS>"}
    assert body["findings"][0]["path"] == "$.m"
    assert body["stats"]["entities_found"] == 1

def test_detect_does_not_modify_data():
    r = client(FakeAnalyzer({"john@x.com": [(0, 11, "EMAIL_ADDRESS", 1.0)]})) \
        .post("/v1/detect", json={"m": "john@x.com"})
    body = r.json()
    assert "data" not in body and body["findings"][0]["entity_type"] == "EMAIL_ADDRESS"

def test_invalid_json_media_type_and_params():
    c = client(FakeAnalyzer())
    assert c.post("/v1/anonymize", content=b"{",
                  headers={"content-type": "application/json"}).status_code == 400
    assert c.post("/v1/anonymize", content=b"{}",
                  headers={"content-type": "text/plain"}).status_code == 415
    assert c.post("/v1/anonymize?mode=mask", json={}).status_code == 422
    assert c.post("/v1/anonymize?language=vi", json={}).status_code == 422
    assert c.post("/v1/anonymize?min_score=2", json={}).status_code == 422

def test_body_limit():
    s = dataclasses.replace(load_settings(env={}), max_body_bytes=10)
    assert client(FakeAnalyzer(), s).post("/v1/anonymize", json={"x": "long value"}).status_code == 413

def test_api_key_auth():
    s = dataclasses.replace(load_settings(env={}), api_key="secret")
    c = client(FakeAnalyzer(), s)
    assert c.post("/v1/anonymize", json={}).status_code == 401
    assert c.post("/v1/anonymize", json={}, headers={"X-API-Key": "secret"}).status_code == 200

def test_analyzer_down_is_503_without_payload():
    r = client(FakeAnalyzer(fail=True)).post("/v1/anonymize", json={"secret": "abc"})
    assert r.status_code == 503
    assert "abc" not in r.text
```

- [ ] **Step 2: Run fail**

Run: `pytest tests/test_api.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement**

- Đọc body thô, kiểm `Content-Length` và `len(body)` trước khi `json.loads`.
- `min_score` mặc định lấy từ `settings.default_min_score` khi query không có.
- `asyncio.timeout(settings.request_deadline_s)`; `TimeoutError` → 504 `deadline_exceeded`.
- Query param validate: `mode ∈ {replace,pseudonym,hash}`, `0 <= min_score <= 1`, `language == "en"`, `scan_numbers` parse bool.
- Auth constant-time (`hmac.compare_digest`) khi `settings.api_key`.
- Map exception → bảng mã lỗi spec 8.4. Không đưa `str(exc)` hoặc payload vào response/log.
- `create_app` dùng `analyzer` truyền vào nếu có; nếu không, `lifespan` tạo/đóng `AnalyzerClient`. Warmup gọi `health()` trong task nền, không chặn startup.
- `/healthz` cache kết quả `analyzer.health()` 5 s.
- `X-Request-Id`: dùng header client nếu có, ngược lại `uuid4`; thêm vào log (chỉ metadata: request id, số string, số entity, thời gian, mã lỗi).

- [ ] **Step 4: Run pass**

Run: `pytest tests/test_api.py -v`
Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add app/main.py tests/test_api.py
git commit -m "feat: add HTTP API with auth, limits and fail-closed errors"
```

---

### Task 10: Docker, compose, README, live test

**Files:**
- Create: `Dockerfile`, `docker-compose.yml`, `README.md`, `tests/live/test_live_analyzer.py`
- Test: `tests/live/test_live_analyzer.py` (mark `live`)

**Interfaces:**
- Consumes: `create_app`.
- Produces: image `pii-api`, compose service cùng network `presidio_default`.

- [ ] **Step 1: Viết live test (mark `live`, skip nếu analyzer không reachable)**

```python
import os, pytest, httpx

pytestmark = pytest.mark.live

@pytest.mark.skipif(not os.getenv("PII_LIVE_URL"), reason="set PII_LIVE_URL")
def test_vietnamese_sample_has_no_original_pii():
    payload = {"messages": [{"role": "user", "content":
        "Tôi tên Nguyễn Văn An, CCCD 079203012345, sđt 0901234567, email an.nv@example.com, STK 1234567890123."}]}
    r = httpx.post(os.environ["PII_LIVE_URL"] + "/v1/anonymize", json=payload, timeout=60)
    assert r.status_code == 200
    body = r.text
    for secret in ("Nguyễn Văn An", "079203012345", "0901234567", "an.nv@example.com", "1234567890123"):
        assert secret not in body

def test_order_code_without_context_is_untouched():
    payload = {"note": "Đơn hàng 123456789012 đã giao"}
    r = httpx.post(os.environ["PII_LIVE_URL"] + "/v1/anonymize", json=payload, timeout=60)
    assert "123456789012" in r.text
```

- [ ] **Step 2: Chạy để thấy skip/fail có kiểm soát**

Run: `pytest tests/live -v`
Expected: SKIP khi chưa set `PII_LIVE_URL`.

- [ ] **Step 3: Viết `Dockerfile`**

`python:3.11-slim`; cài package; tạo user non-root; `CMD ["uvicorn","app.main:app","--host","0.0.0.0","--port","8000"]`.

`docker-compose.yml`:

```yaml
services:
  pii-api:
    build: .
    ports: ["8000:8000"]
    environment:
      PII_ANALYZER_URL: http://presidio-analyzer:3000
      PII_API_KEY: ${PII_API_KEY:-}
      PII_ANONYMIZE_SALT: ${PII_ANONYMIZE_SALT:-}
    networks: [presidio]
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request;urllib.request.urlopen('http://localhost:8000/healthz')"]
      interval: 15s
      start_period: 90s
      retries: 5
networks:
  presidio:
    external: true
    name: presidio_default
```

- [ ] **Step 4: Viết README**

Gồm: cách chạy (`docker compose up -d --build`), curl ví dụ anonymize/detect, bảng entity, giới hạn, cảnh báo heuristic tiếng Việt, hành vi JSON lồng, cách bật entity built-in qua `entities.yaml`, cách tăng `WORKERS` của analyzer nếu cần throughput.

- [ ] **Step 5: Kiểm tra stack thật**

```bash
docker compose up -d --build
curl -s http://localhost:8000/healthz
curl -s -X POST http://localhost:8000/v1/anonymize \
  -H 'content-type: application/json' \
  -d '{"messages":[{"role":"user","content":"Tôi tên Nguyễn Văn An, sđt 0901234567"}]}'
PII_LIVE_URL=http://localhost:8000 pytest tests/live -v
```

Expected: healthz `{"status":"ok","analyzer":"ok"}`; response chỉ còn token; live test pass.

- [ ] **Step 6: Commit**

```bash
git add Dockerfile docker-compose.yml README.md tests/live
git commit -m "feat: package service with docker compose and live tests"
```
