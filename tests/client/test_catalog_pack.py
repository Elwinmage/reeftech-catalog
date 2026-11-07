"""Tests of catalog_pack (no Home Assistant needed)."""

from __future__ import annotations

import copy
import io
import json
import zipfile
from pathlib import Path

import aiohttp
import pytest

from conftest import Release, dump
from custom_components.catalog_demo import catalog_pack as cp


def parsed(release: Release) -> cp.Manifest:
    return cp.parse_manifest(release.manifest, "equipment")


def zip_of(files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    return buffer.getvalue()


# -- manifest -----------------------------------------------------------------


def test_parse_manifest(release):
    manifest = parsed(release)
    assert manifest.version == "2026.11.0" and manifest.section == "equipment"
    item = manifest.entries["item.eheim"]
    assert (
        item.entry_id == "eheim"
        and str(item.rel_dir) == "items/eheim"
        and item.scopes == ("aquarium",)
    )
    assert (
        str(manifest.entries["tasks"].rel_dir) == "tasks"
        and manifest.entries["tasks"].entry_id == "tasks"
    )
    assert str(manifest.entries["category.heater"].rel_dir) == "categories/heater"
    assert "## Added" in manifest.notes


def _broken(release: Release, change) -> dict:
    data = copy.deepcopy(release.manifest)
    change(data)
    return data


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda d: d.update(format=2), "unsupported manifest format"),
        (lambda d: d.update(section="supplements"), "expected 'equipment'"),
        (lambda d: d.update(version="bad version!"), "bad manifest version"),
        (lambda d: d.update(min_integration=[]), "min_integration must be an object"),
        (lambda d: d.update(min_integration={"x": 3}), "bad min_integration of x"),
        (lambda d: d.update(notes=3), "notes must be a text"),
        (lambda d: d.update(entries=[]), "entries must be an object"),
        (lambda d: d["entries"].update(x=3), "entry x is not an object"),
        (
            lambda d: d["entries"]["tasks"].update(kind="supplement"),
            "not in section equipment",
        ),
        (
            lambda d: d["entries"].update(other=d["entries"]["tasks"]),
            "bad entry name 'other'",
        ),
        (
            lambda d: d["entries"].update({"item.Bad": d["entries"]["item.eheim"]}),
            "bad entry name 'item.Bad'",
        ),
        (lambda d: d["entries"]["tasks"].update(sha256="xx"), "bad sha256"),
        (lambda d: d["entries"]["tasks"].update(size=True), "bad size"),
        (lambda d: d["entries"]["tasks"].update(size=0), "bad size"),
        (lambda d: d["entries"]["tasks"].update(archive="../x.zip"), "bad archive"),
        (
            lambda d: d["entries"]["tasks"].update(archive="supplements-1/x.zip"),
            "bad archive",
        ),
        (lambda d: d["entries"]["tasks"].update(since=None), "bad entry tasks version"),
        (lambda d: d["entries"]["item.eheim"].update(scopes="aquarium"), "bad scopes"),
    ],
)
def test_parse_manifest_errors(release, change, message):
    with pytest.raises(cp.PackError, match=message):
        cp.parse_manifest(_broken(release, change), "equipment")


def test_parse_manifest_wrong_input():
    with pytest.raises(cp.PackError, match="unknown section"):
        cp.parse_manifest({}, "fish")
    with pytest.raises(cp.PackError, match="not an object"):
        cp.parse_manifest([], "equipment")


def test_select(release):
    manifest = parsed(release)
    assert cp.select(manifest, None) is manifest
    aquarium = cp.select(manifest, ["aquarium"])
    assert set(aquarium.entries) == {"tasks", "category.heater", "item.eheim"}
    assert set(aquarium.raw["entries"]) == set(aquarium.entries)
    assert set(cp.select(manifest, ["home"]).entries) == {"tasks", "category.boiler"}


def test_is_compatible(release):
    manifest = parsed(release)
    assert cp.is_compatible(manifest, "demo", "1.0.0")
    manifest.min_integration["demo"] = "v1.2.0"
    assert cp.is_compatible(manifest, "demo", "v1.2.0")
    assert not cp.is_compatible(manifest, "demo", "1.1.9")
    assert not cp.is_compatible(manifest, "demo", "garbage")


# -- installed pack -----------------------------------------------------------


async def _install(
    release: Release, root: Path, manifest: cp.Manifest | None = None
) -> cp.Manifest:
    manifest = manifest or parsed(release)
    staging = cp.prepare_staging(root, [])
    for entry in manifest.entries.values():
        blob = release.files[release.archive_url(entry.name)]
        cp.verify(blob, entry)
        cp.extract_entry(blob, entry, staging / entry.rel_dir)
    cp.commit(root, staging, manifest)
    return manifest


async def test_install_and_read(release, tmp_path):
    root = tmp_path / "pack"
    assert cp.read_installed(root, "equipment") is None
    manifest = await _install(release, root)
    assert cp.read_installed(root, "equipment").raw == manifest.raw
    assert (root / "items/eheim/image.webp").read_bytes() == b"RIFFeheim"
    assert not cp.staging_dir(root).exists()

    keep, fetch = cp.plan(manifest, manifest)
    assert fetch == [] and len(keep) == 4
    assert cp.plan(None, manifest) == (
        [],
        sorted(manifest.entries.values(), key=lambda e: e.name),
    )

    # Reinstall over an existing pack, keeping everything
    staging = cp.prepare_staging(root, keep)
    cp.commit(root, staging, manifest)
    assert (root / "tasks/clean.json").is_file()

    # A missing entry, or an empty task folder, means "not installed"
    for path in (root / "tasks").iterdir():
        path.unlink()
    assert cp.read_installed(root, "equipment") is None
    (root / "manifest.json").write_text("{", encoding="utf-8")
    assert cp.read_installed(root, "equipment") is None


async def test_read_installed_missing_descriptor(release, tmp_path):
    root = tmp_path / "pack"
    await _install(release, root)
    (root / "items/eheim/item.json").unlink()
    assert cp.read_installed(root, "equipment") is None


def test_verify(release):
    entry = parsed(release).entries["tasks"]
    blob = release.files[release.archive_url("tasks")]
    with pytest.raises(cp.PackError, match="bytes, expected"):
        cp.verify(blob + b"x", entry)
    with pytest.raises(cp.PackError, match="checksum mismatch"):
        cp.verify(bytes(len(blob)), entry)


@pytest.mark.parametrize(
    ("files", "message"),
    [
        ({}, "0 files"),
        ({"sub/item.json": b"{}"}, "unexpected file"),
        ({"item.json": b"{}", "notes.txt": b"x"}, "unexpected file"),
        ({"image.webp": b"x"}, "no item.json"),
        ({"item.json": b"{"}, "unreadable item.json"),
        ({"item.json": b"[]"}, "item.json is not an object"),
    ],
)
def test_extract_errors(release, tmp_path, files, message):
    entry = parsed(release).entries["item.eheim"]
    with pytest.raises(cp.PackError, match=message):
        cp.extract_entry(zip_of(files), entry, tmp_path / "out")


def test_extract_bad_zip_and_size(release, tmp_path, monkeypatch):
    entry = parsed(release).entries["item.eheim"]
    with pytest.raises(cp.PackError, match="not a zip"):
        cp.extract_entry(b"nope", entry, tmp_path)
    monkeypatch.setattr(cp, "MAX_ENTRY_BYTES", 1)
    with pytest.raises(cp.PackError, match="too big"):
        cp.extract_entry(zip_of({"item.json": b"{}" * 10}), entry, tmp_path)


# -- network ------------------------------------------------------------------


class FakeResponse:
    def __init__(self, status: int, body: bytes) -> None:
        self.status = status
        self._body = body
        self.content = self

    async def iter_chunked(self, size: int):
        for start in range(0, len(self._body), size):
            yield self._body[start : start + size]

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


class FakeSession:
    def __init__(self, routes: dict) -> None:
        self.routes = routes

    def get(self, url: str, timeout=None):
        route = self.routes.get(url, (404, b""))
        if isinstance(route, Exception):
            raise route
        return FakeResponse(*route)


async def test_client(release):
    routes = {url: (200, body) for url, body in release.files.items()}
    routes[release.manifest_url] = (200, json.dumps(release.manifest).encode())
    client = cp.PackClient(FakeSession(routes), "equipment")
    assert client.release_url("2026.11.0").endswith("/releases/tag/equipment-2026.11.0")
    manifest = await client.latest()
    assert manifest.version == "2026.11.0"
    blob = await client.entry(manifest.entries["item.eheim"])
    assert blob == release.files[release.archive_url("item.eheim")]


async def test_client_errors(release, monkeypatch):
    url = release.manifest_url
    client = cp.PackClient(FakeSession({url: (200, b"\xff")}), "equipment")
    with pytest.raises(cp.PackError, match="unreadable manifest"):
        await client.latest()
    client = cp.PackClient(FakeSession({}), "equipment")
    with pytest.raises(cp.PackError, match="HTTP 404"):
        await client.latest()
    client = cp.PackClient(FakeSession({url: aiohttp.ClientError("down")}), "equipment")
    with pytest.raises(cp.PackError, match="down"):
        await client.latest()
    client = cp.PackClient(FakeSession({url: TimeoutError()}), "equipment")
    with pytest.raises(cp.PackError):
        await client.latest()
    monkeypatch.setattr(cp, "MAX_MANIFEST_BYTES", 10)
    client = cp.PackClient(FakeSession({url: (200, b"x" * 70000)}), "equipment")
    with pytest.raises(cp.PackError, match="more than 10 bytes"):
        await client.latest()


# -- merged view --------------------------------------------------------------


async def test_build_view(release, tmp_path):
    pack = tmp_path / "pack"
    await _install(release, pack)
    builtin = tmp_path / "builtin"
    dump(builtin / "tasks/clean.json", {"name": {"en": "Builtin clean"}})
    dump(
        builtin / "categories/fan/category.json",
        {"name": {"en": "Fan"}, "scopes": ["aquarium"]},
    )
    dump(
        builtin / "categories/fan_home/category.json",
        {"name": {"en": "Fan"}, "scopes": ["home"]},
    )
    user = tmp_path / "user"
    dump(
        user / "items/mine/item.json", {"category": "fan", "brand": "Me", "model": "1"}
    )
    dump(
        user / "items/orphan/item.json",
        {"category": "nope", "brand": "Me", "model": "1"},
    )
    dump(
        user / "items/heater2/item.json",
        {"category": "heater", "brand": "Me", "model": "2", "scopes": ["home"]},
    )
    (user / "items/broken").mkdir(parents=True)
    (user / "items/broken/item.json").write_text("{", encoding="utf-8")
    dump(user / "items/Bad_Id/item.json", {"category": "fan"})
    (user / "items/nodescriptor").mkdir()
    (user / "items/file.json").write_text("{}", encoding="utf-8")
    (user / "tasks").mkdir(parents=True)
    (user / "tasks/list.json").write_text("[]", encoding="utf-8")
    (user / "tasks/huge.json").write_text(
        json.dumps({"x": "a" * (cp.MAX_JSON_BYTES + 1)}), encoding="utf-8"
    )

    layers = [
        cp.Layer("builtin", builtin, "/d/builtin", "1.0"),
        cp.Layer("pack", pack, "/d/pack", "2026.11.0"),
        cp.Layer("user", user, "/d/user", "user"),
        cp.Layer("absent", tmp_path / "absent", "/d/absent", "0"),
    ]
    view = cp.build_view(layers, "equipment", ["aquarium"])
    assert view["tasks"]["clean"]["name"] == {"en": "Clean"}  # pack overrides builtin
    assert set(view["categories"]) == {"heater", "fan"}
    assert set(view["items"]) == {"eheim", "mine"}
    eheim = view["items"]["eheim"]
    assert eheim["image"] == "/d/pack/items/eheim/image.webp?v=2026.11.0"
    assert eheim["thumb"] == "/d/pack/items/eheim/thumb.webp?v=2026.11.0"
    assert eheim["scopes"] == ["aquarium"] and eheim["source"] == "pack"
    mine = view["items"]["mine"]
    assert mine["image"] is None and mine["thumb"] is None and mine["source"] == "user"
    assert (
        view["categories"]["heater"]["thumb"] == view["categories"]["heater"]["image"]
    )

    everything = cp.build_view(layers, "equipment")
    assert {"boiler", "fan_home"} <= set(everything["categories"])
    assert "heater2" in everything["items"]

    supplements = cp.build_view([cp.Layer("user", user, "/d/user", "u")], "supplements")
    # Item descriptors are not supplement descriptors
    assert supplements == {"items": {}}


def test_resolve_maintenance():
    view = {
        "tasks": {
            "clean": {
                "name": {"en": "Clean"},
                "icon": "mdi:broom",
                "schedule": {"type": "interval"},
            },
            "service": {
                "name": {"en": "Service"},
                "schedule": {"type": "x"},
                "regulatory": True,
            },
        },
        "categories": {
            "heater": {"maintenance": [{"task": "clean"}, {"task": "ghost"}, "bad"]}
        },
        "items": {
            "a": {
                "category": "heater",
                "maintenance": [{"task": "clean", "schedule": {"type": "own"}}],
            },
            "b": {
                "category": "heater",
                "inherit_maintenance": False,
                "maintenance": [{"task": "service", "regulatory": False}],
            },
        },
    }
    assert cp.resolve_maintenance(view, category_id="heater") == [
        {
            "task": "clean",
            "name": {"en": "Clean"},
            "description": {},
            "icon": "mdi:broom",
            "schedule": {"type": "interval"},
            "regulatory": False,
        }
    ]
    assert cp.resolve_maintenance(view, item_id="a")[0]["schedule"] == {"type": "own"}
    only = cp.resolve_maintenance(view, item_id="b")
    assert [t["task"] for t in only] == ["service"] and only[0]["regulatory"] is False
    assert cp.resolve_maintenance(view, item_id="unknown") == []
    assert cp.resolve_maintenance(view) == []
