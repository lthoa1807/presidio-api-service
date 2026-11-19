import dataclasses

import pytest

from app.config import load_settings
from app.walker import PayloadLimitError, collect_leaves, set_value

S = load_settings(env={})


def test_collects_nested_strings_in_order_with_paths():
    data = {"a": "xin chào", "b": ["một", {"c": "hai"}], "n": 123}
    leaves = collect_leaves(data, S)
    assert [(l.path, l.text) for l in leaves] == [
        ("$.a", "xin chào"),
        ("$.b[0]", "một"),
        ("$.b[1].c", "hai"),
    ]
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
    leaves = collect_leaves(
        {"a": {"secret": "abc"}, "b": "xyz"}, S, exclude_paths=("$.a.*",)
    )
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
