"""Tests of the catalog tools, on small catalogs built in a temporary folder."""

from __future__ import annotations

import json
import shutil
import sys
import zipfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))

import catalog  # noqa: E402
import make_release  # noqa: E402
import validate  # noqa: E402


def dump(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def interval(default=1, low=1, high=3, unit="months"):
    return {
        "type": "interval",
        "unit": unit,
        "every": {"default": default, "min": low, "max": high},
    }


@pytest.fixture
def root(tmp_path: Path) -> Path:
    """A minimal valid catalog."""
    shutil.copytree(REPO / "schema", tmp_path / "schema")
    dump(
        tmp_path / "equipment/tasks/clean.json",
        {"name": {"en": "Clean"}, "schedule": interval()},
    )
    dump(
        tmp_path / "equipment/categories/heater/category.json",
        {
            "name": {"en": "Heater"},
            "scopes": ["aquarium"],
            "icon": "mdi:thermometer",
            "maintenance": [{"task": "clean"}],
        },
    )
    dump(
        tmp_path / "equipment/items/eheim-200/item.json",
        {"category": "heater", "brand": "Eheim", "model": "200"},
    )
    dump(
        tmp_path / "supplements/items/brine/supplement.json",
        {
            "brand": "Generic",
            "name": "Brine",
            "fullname": "DIY - Brine",
            "kind": "brine",
            "concentration_g_l": {"default": 200, "min": 100, "max": 300},
            "regulation": ["salinity"],
            "verified": True,
            "source": "user",
        },
    )
    return tmp_path


def errors(root: Path) -> list[str]:
    return validate.validate(root).errors


def test_repository_catalog_is_valid():
    assert validate.validate(REPO).errors == []


def test_minimal_catalog_is_valid(root):
    assert errors(root) == []


def test_unknown_task_and_category(root):
    dump(
        root / "equipment/items/bad/item.json",
        {"category": "nope", "brand": "X", "model": "Y"},
    )
    dump(
        root / "equipment/categories/pump/category.json",
        {
            "name": {"en": "Pump"},
            "scopes": ["aquarium"],
            "icon": "mdi:pump",
            "maintenance": [{"task": "missing"}],
        },
    )
    found = errors(root)
    assert any("unknown category 'nope'" in e for e in found)
    assert any("unknown task 'missing'" in e for e in found)


def test_task_listed_twice(root):
    dump(
        root / "equipment/items/twice/item.json",
        {
            "category": "heater",
            "brand": "X",
            "model": "Y",
            "maintenance": [{"task": "clean"}, {"task": "clean", "regulatory": True}],
        },
    )
    assert any("listed twice" in e for e in errors(root))


def test_schedule_bounds_and_dates(root):
    dump(
        root / "equipment/tasks/bad.json",
        {"name": {"en": "Bad"}, "schedule": interval(5, 1, 3)},
    )
    dump(
        root / "equipment/tasks/feb.json",
        {
            "name": {"en": "Feb"},
            "schedule": {"type": "seasonal", "month": 2, "day": 30},
        },
    )
    found = errors(root)
    assert any("min <= default <= max" in e for e in found)
    assert any("no day 30 in month 2" in e for e in found)


def test_item_scope_outside_category(root):
    dump(
        root / "equipment/items/pool/item.json",
        {"category": "heater", "brand": "X", "model": "Y", "scopes": ["pool"]},
    )
    assert any("not allowed by category" in e for e in errors(root))


def test_english_is_mandatory_and_others_warn(root):
    dump(
        root / "equipment/tasks/fr.json",
        {"name": {"fr": "Nettoyer"}, "schedule": interval()},
    )
    report = validate.validate(root)
    assert any("'en' is a required property" in e for e in report.errors)
    assert any("missing translations" in w for w in report.warnings)


def test_unexpected_and_oversized_files(root):
    folder = root / "equipment/items/eheim-200"
    (folder / "notes.txt").write_text("x")
    (folder / "thumb.webp").write_bytes(b"0" * 50_000)
    found = errors(root)
    assert any("unexpected file 'notes.txt'" in e for e in found)
    assert any("thumb.webp is 50000 bytes" in e for e in found)


def test_invalid_json(root):
    (root / "equipment/tasks/broken.json").write_text("{", encoding="utf-8")
    assert any("unreadable JSON" in e for e in errors(root))


def supplement(root: Path, name: str, **data) -> None:
    base = {
        "brand": "B",
        "name": name,
        "fullname": f"B - {name}",
        "kind": "kh",
        "verified": False,
    }
    dump(root / f"supplements/items/{name}/supplement.json", base | data)


def test_supplement_rules(root):
    supplement(root, "salty", regulation=["salinity"])
    supplement(root, "conc", concentration_g_l={"default": 200, "min": 100, "max": 300})
    supplement(root, "brine2", kind="brine")
    supplement(
        root,
        "strong",
        kind="brine",
        concentration_g_l={"default": 400, "min": 100, "max": 500},
    )
    supplement(
        root,
        "typo",
        effects=[{"parameter": "kh", "unit": "dkh", "per_ml_per_100l": 3.5}],
    )
    supplement(
        root,
        "unit",
        effects=[{"parameter": "kh", "unit": "ppm", "per_ml_per_100l": 0.1}],
    )
    supplement(root, "trusted", verified=True)
    supplement(root, "dup1", uid="abc")
    supplement(root, "dup2", uid="abc")
    supplement(root, "samename", fullname="B - dup1")
    supplement(
        root,
        "notbundle",
        bundle=[
            {"slot": 1, "part": "dup1", "ratio": 1},
            {"slot": 2, "part": "dup2", "ratio": 1},
        ],
    )
    supplement(root, "emptybundle", kind="bundle")
    supplement(
        root,
        "pack",
        kind="bundle",
        bundle=[
            {"slot": 1, "part": "dup1", "ratio": 1},
            {"slot": 1, "part": "ghost", "ratio": 1},
            {"slot": 2, "part": "emptybundle", "ratio": 0.5},
        ],
    )
    found = errors(root)
    for expected in (
        "only a brine can drive salinity",
        "concentration_g_l is for brine only",
        "a brine needs concentration_g_l",
        "concentration_g_l must satisfy",
        "is implausible",
        "unit 'ppm' not supported for 'kh'",
        "verified dosing data needs a source",
        "uid 'abc' already used",
        "fullname 'B - dup1' already used",
        "only a supplement of kind 'bundle' has parts",
        "a bundle needs its parts",
        "bundle slot used twice",
        "unknown bundle part 'ghost'",
        "bundle part 'emptybundle' is itself a bundle",
    ):
        assert any(expected in e for e in found), expected


def test_unverified_effects_warn(root):
    supplement(
        root, "kh", effects=[{"parameter": "kh", "unit": "dkh", "per_ml_per_100l": 0.1}]
    )
    report = validate.validate(root)
    assert report.errors == []
    assert any("not used for automatic dose proposals" in w for w in report.warnings)


def test_main_exit_codes(root, capsys):
    assert validate.main(["--root", str(root)]) == 0
    assert (
        validate.main(["--root", str(root), "--strict"]) == 1
    )  # translation/image warnings
    (root / "equipment/tasks/broken.json").write_text("{", encoding="utf-8")
    assert validate.main(["--root", str(root)]) == 1
    assert "error(s)" in capsys.readouterr().out


def test_release_is_incremental(root, tmp_path):
    first = make_release.build(
        root,
        "equipment",
        "2026.11.0",
        tmp_path / "d1",
        min_integration={"reef_maintenance": "0.3.0"},
    )
    assert set(first["entries"]) == {"tasks", "category.heater", "item.eheim-200"}
    item = first["entries"]["item.eheim-200"]
    assert item["scopes"] == ["aquarium"] and item["category"] == "heater"
    assert item["archive"] == "equipment-2026.11.0/item.eheim-200.zip"
    with zipfile.ZipFile(tmp_path / "d1/upload/item.eheim-200.zip") as archive:
        assert archive.namelist() == ["item.json"]

    dump(
        root / "equipment/tasks/clean.json",
        {"name": {"en": "Clean it"}, "schedule": interval()},
    )
    (root / "equipment/items/eheim-200/item.json").unlink()
    (root / "equipment/items/eheim-200").rmdir()
    second = make_release.build(
        root, "equipment", "2026.11.1", tmp_path / "d2", previous=first
    )
    assert (
        second["entries"]["category.heater"]["archive"]
        == "equipment-2026.11.0/category.heater.zip"
    )
    assert second["entries"]["tasks"]["since"] == "2026.11.1"
    assert second["min_integration"] == {"reef_maintenance": "0.3.0"}
    assert sorted(p.name for p in (tmp_path / "d2/upload").iterdir()) == ["tasks.zip"]
    notes = (tmp_path / "d2/notes.md").read_text()
    assert "## Updated" in notes and "## Removed" in notes and "`eheim-200`" in notes

    third = make_release.build(
        root, "equipment", "2026.11.2", tmp_path / "d3", previous=second
    )
    assert "No content change." in (tmp_path / "d3/notes.md").read_text()
    assert third["entries"] == second["entries"]


def test_release_supplements_summary(root, tmp_path):
    supplement(root, "kh", uid="rs-1")
    manifest = make_release.build(root, "supplements", "2026.11.0", tmp_path / "out")
    assert manifest["entries"]["supplement.kh"]["uid"] == "rs-1"
    assert manifest["entries"]["supplement.brine"]["supplement_kind"] == "brine"
    assert manifest["section"] == "supplements" and manifest["format"] == 1


def test_release_argument_checks(root, tmp_path):
    with pytest.raises(SystemExit):
        make_release.build(root, "equipment", "v1", tmp_path)
    with pytest.raises(SystemExit):
        make_release.build(root, "fish", "2026.11.0", tmp_path)
    with pytest.raises(SystemExit):
        make_release.build(
            root,
            "equipment",
            "2026.11.0",
            tmp_path,
            previous={"section": "supplements"},
        )
    with pytest.raises(SystemExit):
        make_release._parse_min(["unknown=1.0"], "equipment")
    with pytest.raises(SystemExit):
        make_release._parse_min(["redsea"], "supplements")
    # Consumers of the other section are skipped (both sections released together)
    assert make_release._parse_min(
        ["redsea=1.0", "reef_maintenance=0.3.0"], "equipment"
    ) == {"reef_maintenance": "0.3.0"}


def test_release_main(root, tmp_path, capsys):
    out = tmp_path / "out"
    assert (
        make_release.main(
            [
                "supplements",
                "2026.11.0",
                "--root",
                str(root),
                "--out",
                str(out),
                "--min-integration",
                "redsea=2.0.0",
            ]
        )
        == 0
    )
    prev = out / "manifest.json"
    assert (
        make_release.main(
            [
                "supplements",
                "2026.11.1",
                "--root",
                str(root),
                "--out",
                str(tmp_path / "o2"),
                "--previous",
                str(prev),
            ]
        )
        == 0
    )
    assert "0 to upload" in capsys.readouterr().out
    data = json.loads(prev.read_text())
    data["format"] = 99
    prev.write_text(json.dumps(data))
    with pytest.raises(SystemExit):
        make_release.main(
            [
                "supplements",
                "2026.11.2",
                "--root",
                str(root),
                "--out",
                str(tmp_path / "o3"),
                "--previous",
                str(prev),
            ]
        )


def test_structure_errors(tmp_path, root):
    # Missing descriptors, duplicated images, invalid data in each kind.
    (root / "equipment/categories/empty").mkdir()
    (root / "supplements/items/empty").mkdir()
    folder = root / "equipment/items/eheim-200"
    (folder / "image.webp").write_bytes(b"x")
    (folder / "image.png").write_bytes(b"x")
    dump(root / "equipment/categories/bad/category.json", {"name": {"en": "Bad"}})
    dump(root / "equipment/items/bad/item.json", {"brand": "X"})
    supplement(root, "invalid", kind="unknown")
    supplement(
        root,
        "twice",
        effects=[
            {"parameter": "ca", "unit": "ppm", "per_ml_per_100l": 1},
            {"parameter": "ca", "unit": "ppm", "per_ml_per_100l": 2},
        ],
    )
    dump(
        root / "supplements/items/salty-fx/supplement.json",
        {
            "brand": "B",
            "name": "B",
            "fullname": "B - salty",
            "kind": "brine",
            "verified": False,
            "concentration_g_l": {"default": 200, "min": 100, "max": 300},
            "effects": [{"parameter": "mg", "unit": "ppm", "per_ml_per_100l": 1}],
        },
    )
    found = errors(root)
    for expected in (
        "equipment/categories/empty: missing category.json",
        "supplements/items/empty: missing supplement.json",
        "image.webp and image.png both present",
        "parameter 'ca' listed twice",
        "a brine has no effects",
    ):
        assert any(expected in e for e in found), expected
    assert any(e.startswith("equipment/items/bad") for e in found)
    assert any(e.startswith("supplements/items/invalid") for e in found)

    empty = tmp_path / "empty"
    shutil.copytree(REPO / "schema", empty / "schema")
    assert any("section folder missing" in e for e in errors(empty))
    assert catalog.collect(empty, "equipment") == []
    with pytest.raises(ValueError):
        catalog.collect(empty, "fish")


def test_sync_client(root, tmp_path, capsys):
    import sync_client

    base = tmp_path / "checkouts"
    dump(
        root / "equipment/categories/boiler/category.json",
        {"name": {"en": "Boiler"}, "scopes": ["home"], "icon": "mdi:x"},
    )
    dump(
        root / "equipment/items/pool/item.json",
        {"category": "heater", "brand": "X", "model": "Y", "scopes": ["pool"]},
    )
    for folder, (domain, _, _) in sync_client.TARGETS.items():
        if folder != "ha-maintenance-hub":
            (base / folder / "custom_components" / domain).mkdir(parents=True)

    stale = sync_client.sync(base, check=True, root=root)
    assert "ha-reefbeat-component/catalog_pack.py" in stale
    assert not (
        base / "ha-reefbeat-component/custom_components/redsea/catalog_pack.py"
    ).exists()

    stale = sync_client.sync(base, root=root)
    assert len(stale) == 6  # 2 integrations x (2 modules + snapshot)
    redsea = base / "ha-reefbeat-component/custom_components/redsea"
    assert (
        (redsea / "catalog_pack.py")
        .read_text()
        .startswith("# Generated from reeftech-catalog/client/catalog_pack.py")
    )
    assert (redsea / "catalog_builtin/items/brine/supplement.json").is_file()
    reef = (
        base
        / "ha-reef-maintenance-component/custom_components/reef_maintenance/catalog_builtin"
    )
    assert sorted(p.name for p in (reef / "categories").iterdir()) == ["heater"]
    assert sorted(p.name for p in (reef / "items").iterdir()) == ["eheim-200"]
    assert (reef / "tasks/clean.json").is_file()

    assert sync_client.sync(base, check=True, root=root) == []
    (reef / "tasks/extra.json").write_text("{}")
    assert sync_client.sync(base, check=True, root=root) == [
        "ha-reef-maintenance-component/catalog_builtin"
    ]
    (reef / "tasks/extra.json").unlink()
    (reef / "tasks/clean.json").write_text("{}")
    assert sync_client.sync(base, check=True, root=root) == [
        "ha-reef-maintenance-component/catalog_builtin"
    ]
    shutil.rmtree(reef / "tasks")
    assert sync_client.sync(base, check=True, root=root) == [
        "ha-reef-maintenance-component/catalog_builtin"
    ]
    shutil.rmtree(reef)
    assert sync_client._same_tree(tmp_path / "none1", tmp_path / "none2")

    assert sync_client.main(["--base", str(base), "--check"]) == 1
    assert "out of date" in capsys.readouterr().out
    assert sync_client.main(["--base", str(base)]) == 0
    assert sync_client.main(["--base", str(base), "--check"]) == 0
    assert "everything up to date" in capsys.readouterr().out


def test_sync_snapshot_all_scopes(root, tmp_path):
    import sync_client

    sync_client.write_snapshot(root, "equipment", None, tmp_path / "all")
    assert (tmp_path / "all/categories/heater/category.json").is_file()
    sync_client.write_snapshot(root, "equipment", ("home",), tmp_path / "home")
    assert not (tmp_path / "home/categories").exists()
