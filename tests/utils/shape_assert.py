from __future__ import annotations

import re
from typing import Any


_TYPE_MAP = {
    "array": list,
    "bool": bool,
    "dict": dict,
    "float": float,
    "int": int,
    "number": (int, float),
    "null": type(None),
    "str": str,
}


def assert_has_keys(obj: dict[str, Any], keys: list[str]) -> None:
    missing = [key for key in keys if key not in obj]
    assert not missing, f"Missing keys: {missing}"


def assert_type(value: Any, expected_type: str) -> None:
    if "|" in expected_type:
        options = [item.strip() for item in expected_type.split("|") if item.strip()]
        for option in options:
            try:
                assert_type(value, option)
                return
            except AssertionError:
                continue
        raise AssertionError(f"Expected one of {options}, got {type(value).__name__}")

    assert expected_type in _TYPE_MAP, f"Unknown expected type: {expected_type}"
    expected = _TYPE_MAP[expected_type]
    if expected_type == "bool":
        assert isinstance(value, bool), f"Expected bool, got {type(value).__name__}"
        return
    if expected_type == "int":
        assert isinstance(value, int) and not isinstance(value, bool), f"Expected int, got {type(value).__name__}"
        return
    assert isinstance(value, expected), f"Expected {expected_type}, got {type(value).__name__}"


def assert_regex(value: str, pattern: str) -> None:
    assert re.match(pattern, value), f"Value `{value}` does not match `{pattern}`"
