from pathlib import Path

from app.config import load_entities
from app.recognizers import to_ad_hoc_recognizers

CFG = Path("app/config/entities.yaml")


def test_groups_patterns_by_entity():
    recs = to_ad_hoc_recognizers(load_entities(CFG)[0])
    addr = next(r for r in recs if r["supported_entity"] == "VN_ADDRESS")
    assert len(addr["patterns"]) == 2
    assert addr["supported_language"] == "en"


def test_disabled_entity_is_skipped():
    from app.config import EntityConfig

    recs = to_ad_hoc_recognizers(
        [EntityConfig("X", r"\d+", 0.5, False, (), enabled=False)]
    )
    assert recs == []
