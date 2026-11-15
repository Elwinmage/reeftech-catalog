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
        parameters={"kh": {"symbol": "KH", "effect_parameter": "kh"}},
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
