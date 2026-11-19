from pathlib import Path

from app.config import load_entities, load_settings


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
