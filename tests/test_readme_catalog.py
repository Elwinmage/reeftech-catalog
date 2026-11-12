"""Tests of scripts/readme_catalog.py: the lists of the README."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))

import readme_catalog as rc  # noqa: E402


def dump(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


@pytest.fixture
def root(tmp_path: Path) -> Path:
    """A small catalog covering every kind of entry."""
    dump(
        tmp_path / "equipment/tasks/clean.json",
        {
            "name": {"en": "Clean", "fr": "Nettoyer"},
            "icon": "mdi:broom",
            "schedule": {
                "type": "interval",
                "unit": "months",
                "every": {"default": 3, "min": 1, "max": 6},
            },
        },
    )
    dump(
        tmp_path / "equipment/tasks/winter.json",
        {
            "name": {"en": "Winter"},
            "schedule": {"type": "seasonal", "month": 10, "day": 5, "window_days": 7},
        },
    )
    dump(
        tmp_path / "equipment/tasks/spring.json",
        {
            "name": {"en": "Spring"},
            "schedule": {"type": "seasonal", "month": 4, "day": 1},
        },
    )
    dump(
        tmp_path / "equipment/tasks/yearly.json",
        {
            "name": {"en": "Yearly"},
            "schedule": {
                "type": "interval",
                "unit": "years",
                "every": {"default": 1, "min": 1, "max": 1},
            },
        },
    )
    dump(
        tmp_path / "equipment/tasks/odd.json",
        {"name": "Odd", "schedule": {"type": "x"}},
    )
    dump(tmp_path / "equipment/tasks/bare.json", {"name": {"fr": "Nu"}})
    dump(
        tmp_path / "equipment/categories/heater/category.json",
        {
            "name": {"en": "Heater"},
            "scopes": ["aquarium", "pool"],
            "maintenance": [{"task": "clean"}, {"task": "yearly"}, "winter"],
        },
    )
    (tmp_path / "equipment/categories/heater/thumb.webp").write_bytes(b"x")
    (tmp_path / "equipment/categories/.hidden").mkdir(parents=True)
    dump(
        tmp_path / "equipment/items/eheim/item.json",
        {
            "category": "heater",
            "brand": "Eheim",
            "model": "200 | pro",
            "power_w": 200,
            "maintenance": [{"task": "clean"}, {"task": "gone"}],
        },
    )
    dump(
        tmp_path / "equipment/items/own/item.json",
        {
            "category": "missing",
            "brand": "Acme",
            "model": "X",
            "inherit_maintenance": False,
        },
    )
    (tmp_path / "equipment/items/own/image.png").write_bytes(b"x")
    dump(
        tmp_path / "supplements/items/kh/supplement.json",
        {
            "brand": "Red Sea",
            "name": "KH",
            "kind": "kh",
            "effects": [{"parameter": "kh", "unit": "dKH", "per_ml_per_100l": 0.1}],
            "verified": True,
        },
    )
    (tmp_path / "supplements/items/kh/image.webp").write_bytes(b"x")
    dump(
        tmp_path / "supplements/items/zinc/supplement.json",
        {
            "brand": "Acme",
            "name": "Zinc",
            "kind": "exotic",
            "effects": [{"parameter": "zn", "unit": "ppm", "per_ml_per_100l": 0.01}],
        },
    )
    dump(
        tmp_path / "supplements/items/brine/supplement.json",
        {"brand": "DIY", "name": "Brine", "kind": "brine", "concentration_g_l": 200},
    )
    dump(
        tmp_path / "supplements/items/food/supplement.json",
        {"brand": "Acme", "name": "Food", "kind": "food"},
    )
    (tmp_path / "README.md").write_text(
        "# Catalog\n\nIntro.\n\n" + rc.BEGIN + "\nold\n" + rc.END + "\n\n## End\n",
        encoding="utf-8",
    )
    return tmp_path


def test_render(root: Path) -> None:
    block = rc.render(root)
    assert block.startswith(rc.BEGIN) and block.endswith(rc.END)
    lines = block.splitlines()
    # Categories: visual, scopes and default tasks by name
    assert (
        '| <img src="equipment/categories/heater/thumb.webp" width="48" alt=""> '
        "| `heater` | Heater | aquarium, pool | Clean, Yearly, Winter |"
    ) in lines
    # Models: the category visual by default, own tasks then inherited ones
    assert (
        '| <img src="equipment/categories/heater/thumb.webp" width="48" alt=""> '
        "| Eheim | 200 \\| pro | Heater | 200 W | Clean, gone, Yearly |"
    ) in lines
    assert (
        '| <img src="equipment/items/own/image.png" width="48" alt=""> '
        "| Acme | X | missing |  |  |"
    ) in lines
    # Tasks and their schedules
    assert "| `clean` | Clean | 3 months (1-6) | mdi:broom |" in lines
    assert "| `yearly` | Yearly | 1 years |  |" in lines
    assert "| `winter` | Winter | yearly, 05/10 (-7 d) |  |" in lines
    assert "| `spring` | Spring | yearly, 01/04 |  |" in lines
    assert "| `odd` | Odd | x |  |" in lines
    assert "| `bare` | bare |  |  |" in lines
    # Supplements: by brand, then name
    assert "### Supplements (4, 3 brands)" in lines
    after = lines[lines.index("### Supplements (4, 3 brands)") :]
    supplements = [line for line in after if "| Acme |" in line or "| DIY |" in line]
    assert supplements == [
        "|  | Acme | Food | food |  |",
        "|  | Acme | Zinc | exotic | +0.01 ppm zn (unverified) |",
        "|  | DIY | Brine | brine | salinity (200 g/L) |",
    ]
    assert (
        '| <img src="supplements/items/kh/image.webp" width="48" alt=""> '
        "| Red Sea | KH | KH | +0.1 dKH KH |"
    ) in lines


def test_parameters_render(tmp_path: Path) -> None:
    # A normal band, a target-only band, and no band at all.
    dump(
        tmp_path / "parameters/items/kh/parameter.json",
        {
            "name": {"en": "Alkalinity"},
            "symbol": "KH",
            "category": "alkalinity",
            "unit": "dkh",
            "range": {"low": 7, "high": 8.5, "optimal": 8},
        },
    )
    dump(
        tmp_path / "parameters/items/temp/parameter.json",
        {
            "name": {"en": "Temperature"},
            "symbol": "T",
            "category": "physical",
            "unit": "celsius",
            "range": {"optimal": 25.5},
        },
    )
    dump(
        tmp_path / "parameters/items/fe/parameter.json",
        {"name": {"en": "Iron"}, "symbol": "Fe", "category": "trace", "unit": "ppb"},
    )
    block = rc.render(tmp_path)
    lines = block.splitlines()
    assert "### Water parameters (3)" in lines
    # physical before alkalinity before trace, each with its band rendering
    after = lines[lines.index("### Water parameters (3)") :]
    rows = [line for line in after if line.startswith("| ")][1:]  # drop header
    assert rows[0] == "| T | Temperature | physical | °C | target 25.5 |"
    assert rows[1] == "| KH | Alkalinity | alkalinity | dKH | 7-8.5 (8) |"
    assert rows[2] == "| Fe | Iron | trace element | ppb |  |"


def test_schedule_without_one() -> None:
    assert rc._schedule(None) == ""


def test_empty_catalog(tmp_path: Path) -> None:
    block = rc.render(tmp_path)
    assert "### Equipment categories (0)" in block
    assert "### Supplements (0, 0 brands)" in block


def test_update_markers() -> None:
    assert rc.update("a\n" + rc.BEGIN + "x" + rc.END + "\nb", "NEW") == "a\nNEW\nb"
    assert rc.update("# Title\n\n", "NEW") == "# Title\n\n## Catalog content\n\nNEW\n"
    for broken in (rc.BEGIN, rc.END, rc.END + rc.BEGIN):
        with pytest.raises(ValueError):
            rc.update(broken, "NEW")


def test_main_writes_then_checks(root: Path, capsys) -> None:
    assert rc.main(["--root", str(root), "--check"]) == 1
    assert "not up to date" in capsys.readouterr().err
    assert rc.main(["--root", str(root)]) == 0
    text = (root / "README.md").read_text(encoding="utf-8")
    assert text.startswith("# Catalog\n\nIntro.\n\n" + rc.BEGIN)
    assert text.endswith(rc.END + "\n\n## End\n")
    assert "README.md updated" in capsys.readouterr().out
    assert rc.main(["--root", str(root), "--check"]) == 0
    assert rc.main(["--root", str(root)]) == 0
    assert "up to date" in capsys.readouterr().out


def test_main_broken_markers(root: Path, capsys) -> None:
    (root / "README.md").write_text(rc.END + rc.BEGIN, encoding="utf-8")
    assert rc.main(["--root", str(root)]) == 1
    assert "broken" in capsys.readouterr().err


def test_repository_readme_is_up_to_date() -> None:
    assert rc.main(["--root", str(REPO), "--check"]) == 0
