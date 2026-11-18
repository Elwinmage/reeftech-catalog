"""Tests of client/catalog_rules.py, the way a tool editing the catalog uses it."""

from __future__ import annotations

import zipfile
from pathlib import Path

import catalog_rules as rules
import pytest

import make_release

REPO = Path(__file__).resolve().parent.parent
INTERVAL = {
    "type": "interval",
    "unit": "months",
    "every": {"default": 1, "min": 1, "max": 3},
}


@pytest.fixture(scope="module")
def validators():
    return rules.load_validators(REPO / "schema")


@pytest.fixture
def ctx() -> rules.Context:
    return rules.Context(
        tasks={"clean": {"name": {"en": "Clean"}, "schedule": INTERVAL}},
        categories={"heater": {"name": {"en": "Heater"}, "scopes": ["aquarium"]}},
        supplements={
            "kh": {
                "brand": "B",
                "name": "KH",
                "fullname": "B - KH",
                "uid": "u1",
                "kind": "kh",
                "verified": False,
            },
            "pack": {"kind": "bundle", "bundle": []},
        },
        parameters={"kh": {"symbol": "KH", "effect_parameter": "kh", "unit": "dkh"}},
    )


def draft(validators, ctx, kind, entry_id, data, files=None):
    name = {"task": None}.get(kind, rules.DESCRIPTORS.get(kind))
    files = {name: 100, **(files or {})} if name else {}
    return rules.validate_draft(kind, entry_id, data, files, ctx, validators)


def test_valid_drafts(validators, ctx):
    item = {"category": "heater", "brand": "Eheim", "model": "200", "name": {"en": "E"}}
    report = draft(validators, ctx, "item", "eheim-200", item, {"image.webp": 1000})
    assert report.errors == []
    assert report.warnings == [
        "equipment/items/eheim-200: missing translations: fr, de, es, it, pl, pt"
    ]
    report = draft(
        validators, ctx, "task", "wash", {"name": {"en": "Wash"}, "schedule": INTERVAL}
    )
    assert report.errors == []
    assert [w.split(":")[0] for w in report.warnings] == ["equipment/tasks/wash.json"]


def test_where_of():
    assert rules.where_of("task", "clean") == "equipment/tasks/clean.json"
    assert rules.where_of("item", "x") == "equipment/items/x"


def test_item_rules(validators, ctx):
    data = {
        "category": "heater",
        "brand": "E",
        "model": "1",
        "scopes": ["home"],
        "maintenance": [
            {"task": "ghost"},
            {"task": "clean"},
            {"task": "clean", "regulatory": True},
        ],
    }
    found = draft(validators, ctx, "item", "e-1", data).errors
    assert any("unknown task 'ghost'" in e for e in found)
    assert any("task 'clean' listed twice" in e for e in found)
    assert any("scopes ['home'] not allowed by category 'heater'" in e for e in found)
    data["category"] = "nope"
    assert any(
        "unknown category 'nope'" in e
        for e in draft(validators, ctx, "item", "e-1", data).errors
    )


def test_files_are_checked(validators, ctx):
    item = {"category": "heater", "brand": "E", "model": "1"}
    report = draft(
        validators,
        ctx,
        "item",
        "e-1",
        item,
        {"image.webp": 400_000, "image.png": 10, "notes.txt": 5},
    )
    for expected in (
        "image.webp is 400000 bytes",
        "image.webp and image.png both present",
        "unexpected file 'notes.txt'",
    ):
        assert any(expected in e for e in report.errors), expected
    report = rules.validate_draft(
        "item", "e-1", item, {"thumb.webp": 5}, ctx, validators
    )
    assert any("missing item.json" in e for e in report.errors)


def test_schema_errors_stop_the_rules(validators, ctx):
    report = draft(validators, ctx, "item", "e-1", {"brand": "E"})
    assert report.errors and all(
        e.startswith("equipment/items/e-1:") for e in report.errors
    )
    assert report.warnings == []
    assert any("(root)" in e or "category" in e for e in report.errors)


def test_supplement_uniqueness_ignores_the_edited_entry(validators, ctx):
    data = {
        "brand": "B",
        "name": "KH",
        "fullname": "B - KH",
        "uid": "u1",
        "kind": "kh",
        "verified": False,
    }
    found = draft(validators, ctx, "supplement", "kh2", data).errors
    assert any("uid 'u1' already used by 'kh'" in e for e in found)
    assert any("fullname 'B - KH' already used by 'kh'" in e for e in found)
    # Editing 'kh' itself: the caller leaves it out of the context
    del ctx.supplements["kh"]
    assert draft(validators, ctx, "supplement", "kh", data).errors == []


def test_bundle_parts(validators, ctx):
    data = {
        "brand": "B",
        "name": "Duo",
        "fullname": "B - Duo",
        "kind": "bundle",
        "verified": False,
        "bundle": [
            {"slot": 1, "part": "kh", "ratio": 1},
            {"slot": 1, "part": "ghost", "ratio": 1},
            {"slot": 2, "part": "pack", "ratio": 1},
            {"slot": 3, "part": "duo", "ratio": 1},
        ],
    }
    found = draft(validators, ctx, "supplement", "duo", data).errors
    assert any("bundle slot used twice" in e for e in found)
    assert any("unknown bundle part 'ghost'" in e for e in found)
    assert any("bundle part 'pack' is itself a bundle" in e for e in found)
    assert any(
        "bundle part 'duo' is itself a bundle" in e for e in found
    )  # its own part


def test_parameter_uniqueness(validators, ctx):
    data = {
        "name": {"en": "KH 2"},
        "symbol": "KH",
        "category": "alkalinity",
        "unit": "dkh",
        "effect_parameter": "kh",
    }
    found = draft(validators, ctx, "parameter", "kh2", data).errors
    assert any("symbol 'KH' already used by 'kh'" in e for e in found)
    assert any("effect_parameter 'kh' already mapped by 'kh'" in e for e in found)


def test_schemas_of_a_release_are_enough(validators, ctx, tmp_path):
    """The editor validates against schema.zip of the release, not the repository."""
    out = tmp_path / "dist"
    make_release.build(REPO, "equipment", "2026.11.0", out)
    with zipfile.ZipFile(out / "upload/schema.zip") as archive:
        archive.extractall(tmp_path / "schema")
    from_release = rules.load_validators(tmp_path / "schema")
    assert set(from_release) == {"task", "category", "item"}
    report = draft(
        from_release,
        ctx,
        "item",
        "e-1",
        {"category": "heater", "brand": "E", "model": "1"},
    )
    assert report.errors == []


TABLE = {
    "amount_unit": "ml",
    "points": [{"amount": 0.5, "value": 12}, {"amount": 1, "value": 6}],
}


def water_test(**test):
    base = {"parameter": "kh", "unit": "dkh", "method": "titration", "table": TABLE}
    return {
        "brand": "Red Sea",
        "name": "Kit",
        "tests": [{**base, **test}],
        "verified": True,
        "source": "manual",
    }


def test_valid_water_test(validators, ctx):
    report = draft(validators, ctx, "water_test", "rs", water_test())
    assert report.errors == []
    assert report.warnings == [
        "water_tests/items/rs: no picture, the generic test will be shown"
    ]
    direct = water_test(method="direct", range={"min": 0, "max": 20})
    del direct["tests"][0]["table"]
    assert (
        draft(validators, ctx, "water_test", "rs", direct, {"image.webp": 1}).errors
        == []
    )


def test_water_test_rules(validators, ctx):
    data = water_test()
    data["verified"] = False
    found = draft(validators, ctx, "water_test", "rs", data)
    assert any("not verified" in w for w in found.warnings)
    # One test per entry: a kit of several tests is several entries
    data["tests"].append(dict(data["tests"][0]))
    assert draft(validators, ctx, "water_test", "rs", data).errors
    del data["tests"][1]
    del data["source"]
    data["verified"] = True
    assert any(
        "needs a source" in e
        for e in draft(validators, ctx, "water_test", "rs", data).errors
    )
    ctx.water_tests["other"] = {"brand": "red sea", "name": "KIT"}
    assert any(
        "already 'other'" in e
        for e in draft(validators, ctx, "water_test", "rs", water_test()).errors
    )


def test_water_test_parameter_checks(validators, ctx):
    found = draft(validators, ctx, "water_test", "a", water_test(parameter="zz")).errors
    assert any("unknown parameter 'zz'" in e for e in found)
    found = draft(validators, ctx, "water_test", "a", water_test(unit="ppm")).errors
    assert any("differs from the one of the parameter" in e for e in found)
    # Without parameters in the context nothing is cross-checked
    ctx.parameters.clear()
    assert (
        draft(validators, ctx, "water_test", "a", water_test(parameter="zz")).errors
        == []
    )


def test_water_test_methods(validators, ctx):
    def errors(**test):
        return draft(validators, ctx, "water_test", "a", water_test(**test)).errors

    assert any("takes its range" in e for e in errors(range={"min": 0, "max": 1}))
    no_table = water_test()
    del no_table["tests"][0]["table"]
    assert any(
        "needs its table" in e
        for e in draft(validators, ctx, "water_test", "a", no_table).errors
    )
    assert any(
        "no table" in e for e in errors(method="direct", range={"min": 0, "max": 9})
    )
    flat = water_test(method="direct", range={"min": 5, "max": 5})
    del flat["tests"][0]["table"]
    assert any(
        "below max" in e for e in draft(validators, ctx, "water_test", "a", flat).errors
    )


def test_water_test_tables(validators, ctx):
    def errors(points):
        table = {"amount_unit": "ml", "points": points}
        return draft(validators, ctx, "water_test", "a", water_test(table=table)).errors

    p = lambda a, v: {"amount": a, "value": v}  # noqa: E731
    assert any("amounts must be strictly" in e for e in errors([p(1, 5), p(1, 6)]))
    assert any("values must be strictly" in e for e in errors([p(1, 5), p(2, 5)]))
    assert any(
        "values must be strictly" in e for e in errors([p(1, 5), p(2, 7), p(3, 6)])
    )
    assert any("implausible" in e for e in errors([p(1, 5), p(2, 70)]))
    # Unknown (parameter, unit): no plausibility bound
    odd = water_test(parameter="ph", unit="ph")
    ctx.parameters["ph"] = {"symbol": "pH", "unit": "ph"}
    assert draft(validators, ctx, "water_test", "a", odd).errors == []


def test_brand_key():
    assert rules.brand_key("Red Sea") == rules.brand_key("red-sea") == "redsea"
    assert rules.brand_key("Aqua Médic") == "aquamedic"


def test_brand_keeps_its_spelling(validators, ctx):
    ctx.brands[rules.brand_key("Red Sea")] = "Red Sea"
    item = {"category": "heater", "brand": "Red-Sea", "model": "1"}
    found = draft(validators, ctx, "item", "red-sea-1", item).errors
    assert any("brand 'Red-Sea' is written 'Red Sea' elsewhere" in e for e in found)
    ok = draft(validators, ctx, "item", "red-sea-1", {**item, "brand": "Red Sea"})
    assert not any("brand" in e for e in ok.errors)
    # A new brand is remembered for the next entries
    draft(validators, ctx, "item", "acme-1", {**item, "brand": "Acme"})
    assert ctx.brands["acme"] == "Acme"
    late = draft(validators, ctx, "item", "acme-2", {**item, "brand": "ACME"})
    assert any("written 'Acme'" in e for e in late.errors)


def test_icon_sets(validators, ctx):
    task = {"name": {"en": "T"}, "schedule": INTERVAL}
    for icon, ok in (("mdi:pump", True), ("redsea:gyre-off", True), ("fa:pump", False)):
        errors = draft(validators, ctx, "task", "t", {**task, "icon": icon}).errors
        assert (errors == []) is ok, (icon, errors)


def test_parameter_regulation(validators, ctx):
    def errors(**regulation):
        data = {
            "name": {"en": "Zinc"},
            "symbol": "Zn",
            "category": "trace",
            "unit": "ppm",
            "range": {"optimal": 1},
            "regulation": {"tolerance": 0.2, "max_rate": 0.5, "step": 0.1} | regulation,
        }
        return draft(validators, ctx, "parameter", "zn", data).errors

    assert errors() == []
    assert any("step must not exceed" in e for e in errors(step=0.5))
    assert any("max_rate must be at least" in e for e in errors(max_rate=0.1))
    # The default target is the optimal value of the band
    no_target = {
        "name": {"en": "Zinc"},
        "symbol": "Zn",
        "category": "trace",
        "unit": "ppm",
        "regulation": {"tolerance": 0.2, "max_rate": 0.5, "step": 0.1},
    }
    found = draft(validators, ctx, "parameter", "zn", no_target).errors
    assert any("needs range.optimal" in e for e in found)
