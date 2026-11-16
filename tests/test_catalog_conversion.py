"""Tests of client/catalog_conversion.py."""

from __future__ import annotations

import catalog_conversion as conv

TABLE = {
    "amount_unit": "ml",
    "points": [
        {"amount": 1, "value": 6},
        {"amount": 0.5, "value": 12},
        {"amount": 2, "value": 3},
    ],
}
TITRATION = {"method": "titration", "table": TABLE}


def test_bounds_and_ranges():
    assert conv.table_bounds(TABLE) == (0.5, 2)
    assert conv.reading_range(TITRATION) == (3, 12)
    direct = {"method": "direct", "range": {"min": 0, "max": 20}}
    assert conv.reading_range(direct) == (0, 20)
    assert conv.reading_range({"method": "direct"}) is None


def test_to_value_interpolates_in_any_order():
    assert conv.to_value(TABLE, 0.5) == 12
    assert conv.to_value(TABLE, 0.75) == 9
    assert conv.to_value(TABLE, 1) == 6
    assert conv.to_value(TABLE, 1.5) == 4.5
    assert conv.to_value(TABLE, 2) == 3


def test_no_extrapolation():
    assert conv.to_value(TABLE, 0.1) is None
    assert conv.to_value(TABLE, 2.5) is None


def test_convert():
    assert conv.convert({"method": "direct"}, 8.3) == 8.3
    assert conv.convert(TITRATION, 0.75) == 9
    assert conv.convert(TITRATION, 5) is None
