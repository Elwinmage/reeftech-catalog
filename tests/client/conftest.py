"""Fixtures of the catalog client tests: a real release built by make_release."""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path

import pytest

import make_release

REPO = Path(__file__).resolve().parents[2]


def dump(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def interval(default: int = 1) -> dict:
    return {
        "type": "interval",
        "unit": "months",
        "every": {"default": default, "min": 1, "max": 12},
    }


@dataclass
class Release:
    """A built release: manifest and archives by URL."""

    manifest: dict
    files: dict[str, bytes]  # URL -> body
    manifest_url: str

    def archive_url(self, name: str) -> str:
        return f"https://github.com/Elwinmage/reeftech-catalog/releases/download/{self.manifest['entries'][name]['archive']}"


def make_catalog(root: Path) -> Path:
    """A small equipment section with aquarium and home entries."""
    shutil.copytree(REPO / "schema", root / "schema")
    dump(
        root / "equipment/tasks/clean.json",
        {"name": {"en": "Clean"}, "schedule": interval(3)},
    )
    dump(
        root / "equipment/tasks/service.json",
        {"name": {"en": "Service"}, "schedule": interval(12), "regulatory": True},
    )
    dump(
        root / "equipment/categories/heater/category.json",
        {
            "name": {"en": "Heater"},
            "scopes": ["aquarium"],
            "icon": "mdi:thermometer",
            "maintenance": [{"task": "clean"}],
        },
    )
    (root / "equipment/categories/heater/image.webp").write_bytes(b"RIFFheater")
    dump(
        root / "equipment/categories/boiler/category.json",
        {
            "name": {"en": "Boiler"},
            "scopes": ["home"],
            "icon": "mdi:water-boiler",
            "maintenance": [{"task": "service"}],
        },
    )
    dump(
        root / "equipment/items/eheim/item.json",
        {
            "category": "heater",
            "brand": "Eheim",
            "model": "200",
            "maintenance": [{"task": "clean", "schedule": interval(6)}],
        },
    )
    (root / "equipment/items/eheim/image.webp").write_bytes(b"RIFFeheim")
    (root / "equipment/items/eheim/thumb.webp").write_bytes(b"RIFFthumb")
    dump(
        root / "supplements/items/kh/supplement.json",
        {
            "brand": "B",
            "name": "KH",
            "fullname": "B - KH",
            "kind": "kh",
            "verified": False,
        },
    )
    return root


def build_release(
    root: Path,
    out: Path,
    section: str,
    version: str,
    previous: dict | None = None,
    **kwargs,
) -> Release:
    manifest = make_release.build(root, section, version, out, previous, kwargs or None)
    files = {}
    for path in (out / "upload").iterdir():
        files[
            f"https://github.com/Elwinmage/reeftech-catalog/releases/download/{section}-{version}/{path.name}"
        ] = path.read_bytes()
    url = f"https://github.com/Elwinmage/reeftech-catalog/releases/download/{section}-latest/manifest.json"
    return Release(manifest, files, url)


@pytest.fixture
def catalog_root(tmp_path: Path) -> Path:
    return make_catalog(tmp_path / "catalog")


@pytest.fixture
def release(catalog_root: Path, tmp_path: Path) -> Release:
    return build_release(catalog_root, tmp_path / "dist", "equipment", "2026.11.0")
