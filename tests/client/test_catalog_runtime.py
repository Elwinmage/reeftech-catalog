"""Tests of catalog_runtime inside Home Assistant (demo integration)."""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from conftest import Release, build_release
from custom_components.catalog_demo import SPEC
from custom_components.catalog_demo import catalog_runtime as rt

DOMAIN = "catalog_demo"
UPDATE = "update.demo_equipment_catalog"
SWITCH = "switch.demo_equipment_catalog_automatic_update"


@pytest.fixture(autouse=True)
def auto_enable(enable_custom_integrations, hass, tmp_path):
    """Load the demo integration, with a config folder of its own per test."""
    hass.config.config_dir = str(tmp_path / "config")


def serve(aioclient_mock, release: Release, manifest: dict | None = None) -> None:
    aioclient_mock.clear_requests()
    aioclient_mock.get(
        release.manifest_url, text=json.dumps(manifest or release.manifest)
    )
    for url, body in release.files.items():
        aioclient_mock.get(url, content=body)


async def setup_entry(hass: HomeAssistant, entry_id: str = "one") -> MockConfigEntry:
    await async_setup_component(hass, "http", {})
    entry = MockConfigEntry(domain=DOMAIN, entry_id=entry_id)
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    return entry


def pack(hass: HomeAssistant) -> Path:
    return Path(hass.config.path(DOMAIN, "catalog"))


async def test_auto_install_and_websocket(
    hass, aioclient_mock, release, hass_ws_client
):
    serve(aioclient_mock, release)
    await setup_entry(hass)

    state = hass.states.get(UPDATE)
    assert state.attributes["installed_version"] == "2026.11.0"
    assert state.attributes["auto_update"] is True
    assert hass.states.get(SWITCH).state == "on"
    # Scope 'aquarium' only: the boiler category is not downloaded
    assert (pack(hass) / "items/eheim/item.json").is_file()
    assert not (pack(hass) / "categories/boiler").exists()

    client = await hass_ws_client(hass)
    await client.send_json_auto_id({"type": f"{DOMAIN}/catalog"})
    result = (await client.receive_json())["result"]
    assert (
        result["version"] == "2026.11.0"
        and result["section"] == "equipment"
        and result["updating"] is False
    )
    data = result["data"]
    assert set(data["categories"]) == {"heater"}
    assert rt.get_updater(hass, DOMAIN).view["items"]["eheim"]["brand"] == "Eheim"
    assert data["tasks"]["visual_inspection"]["name"] == {
        "en": "Visual inspection"
    }  # built-in
    assert (
        data["items"]["eheim"]["image"]
        == f"/{DOMAIN}_catalog/pack/items/eheim/image.webp?v=2026.11.0"
    )

    await client.send_json_auto_id(
        {"type": f"{DOMAIN}/catalog/maintenance", "item_id": "eheim"}
    )
    tasks = (await client.receive_json())["result"]["tasks"]
    assert [(t["task"], t["schedule"]["every"]["default"]) for t in tasks] == [
        ("clean", 6)
    ]

    await client.send_json_auto_id({"type": f"{DOMAIN}/catalog", "refresh": True})
    assert (await client.receive_json())["success"]


async def test_static_files(hass, aioclient_mock, release, hass_client):
    serve(aioclient_mock, release)
    await setup_entry(hass)
    http = await hass_client()
    response = await http.get(f"/{DOMAIN}_catalog/pack/items/eheim/image.webp")
    assert response.status == 200 and await response.read() == b"RIFFeheim"
    response = await http.get(f"/{DOMAIN}_catalog/builtin/tasks/visual_inspection.json")
    assert response.status == 200


async def test_offline_keeps_nothing_installed(hass, aioclient_mock, release):
    aioclient_mock.get(release.manifest_url, status=404)
    await setup_entry(hass)
    updater = rt.get_updater(hass, DOMAIN)
    assert "HTTP 404" in updater.last_error
    assert (
        updater.latest is None and not updater.update_available and updater.compatible
    )
    assert hass.states.get(UPDATE).attributes["installed_version"] == rt.NOT_INSTALLED
    with pytest.raises(HomeAssistantError):
        await updater.async_install()  # never checked successfully


async def test_manual_install_and_switch(
    hass, aioclient_mock, release, catalog_root, tmp_path
):
    await async_setup_component(hass, "homeassistant", {})
    hass_storage_entry = rt.Store(hass, 1, f"{DOMAIN}.catalog")
    await hass_storage_entry.async_save({"auto_update": False})
    serve(aioclient_mock, release)
    await setup_entry(hass)
    state = hass.states.get(UPDATE)
    assert state.attributes["installed_version"] == rt.NOT_INSTALLED
    assert state.attributes["latest_version"] == "2026.11.0"
    assert state.state == "on"

    notes = await rt.get_updater(hass, DOMAIN).client.latest()
    assert "## Added" in notes.notes

    await hass.services.async_call(
        "update", "install", {"entity_id": UPDATE}, blocking=True
    )
    assert hass.states.get(UPDATE).attributes["installed_version"] == "2026.11.0"
    assert hass.states.get(UPDATE).state == "off"

    # A new release changing one entry: only that archive is downloaded
    (catalog_root / "equipment/items/eheim/image.webp").write_bytes(b"RIFFnew")
    second = build_release(
        catalog_root, tmp_path / "d2", "equipment", "2026.11.1", release.manifest
    )
    serve(aioclient_mock, second)
    await hass.services.async_call(
        "homeassistant", "update_entity", {"entity_id": UPDATE}, blocking=True
    )
    assert hass.states.get(UPDATE).attributes["latest_version"] == "2026.11.1"
    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": SWITCH}, blocking=True
    )
    await hass.async_block_till_done(wait_background_tasks=True)
    assert hass.states.get(UPDATE).attributes["installed_version"] == "2026.11.1"
    assert (pack(hass) / "items/eheim/image.webp").read_bytes() == b"RIFFnew"
    assert [
        str(call[1])
        for call in aioclient_mock.mock_calls
        if str(call[1]).endswith(".zip")
    ] == [
        "https://github.com/Elwinmage/reeftech-catalog/releases/download/equipment-2026.11.1/item.eheim.zip"
    ]

    await hass.services.async_call(
        "switch", "turn_off", {"entity_id": SWITCH}, blocking=True
    )
    assert hass.states.get(SWITCH).state == "off"
    assert (await rt.Store(hass, 1, f"{DOMAIN}.catalog").async_load()) == {
        "auto_update": False
    }


async def test_release_notes(hass, aioclient_mock, release, hass_ws_client):
    serve(aioclient_mock, release)
    await setup_entry(hass)
    client = await hass_ws_client(hass)
    await client.send_json_auto_id(
        {"type": "update/release_notes", "entity_id": UPDATE}
    )
    assert "## Added" in (await client.receive_json())["result"]
    rt.get_updater(hass, DOMAIN).latest.notes = ""
    await client.send_json_auto_id(
        {"type": "update/release_notes", "entity_id": UPDATE}
    )
    assert (await client.receive_json())["result"] is None


async def test_incompatible_release(
    hass, aioclient_mock, catalog_root, tmp_path, caplog
):
    release = build_release(
        catalog_root, tmp_path / "d", "equipment", "2026.11.0", catalog_demo="9.0.0"
    )
    serve(aioclient_mock, release)
    await setup_entry(hass)
    assert "needs catalog_demo 9.0.0 or newer" in caplog.text
    state = hass.states.get(UPDATE)
    assert state.attributes["release_summary"] == "Needs catalog_demo 9.0.0 or newer."
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            "update", "install", {"entity_id": UPDATE}, blocking=True
        )


async def test_broken_archive(hass, aioclient_mock, release, caplog):
    for url in release.files:
        release.files[url] = b"x" * len(release.files[url])
    serve(aioclient_mock, release)
    await setup_entry(hass)
    assert "catalog update failed" in caplog.text
    assert hass.states.get(UPDATE).attributes["installed_version"] == rt.NOT_INSTALLED
    assert not rt.get_updater(hass, DOMAIN).in_progress


async def test_install_in_progress_is_skipped(hass, aioclient_mock, release):
    aioclient_mock.get(release.manifest_url, status=500)
    await setup_entry(hass)
    updater = rt.get_updater(hass, DOMAIN)
    serve(aioclient_mock, release)
    updater.in_progress = True
    await updater.async_check()  # auto install skipped
    await updater.async_install()  # returns at once
    assert updater.installed is None


async def test_periodic_check(hass, aioclient_mock, release):
    aioclient_mock.get(release.manifest_url, status=500)
    await setup_entry(hass)
    serve(aioclient_mock, release)
    async_fire_time_changed(
        hass, dt_util.utcnow() + timedelta(hours=SPEC.check_hours, minutes=1)
    )
    await hass.async_block_till_done(wait_background_tasks=True)
    assert hass.states.get(UPDATE).attributes["installed_version"] == "2026.11.0"


async def test_subscribe(hass, aioclient_mock, release, hass_ws_client):
    aioclient_mock.get(release.manifest_url, status=500)
    await setup_entry(hass)
    client = await hass_ws_client(hass)
    await client.send_json_auto_id({"type": f"{DOMAIN}/catalog/subscribe"})
    assert (await client.receive_json())["success"]
    assert (await client.receive_json())["event"] == {
        "version": None,
        "updating": False,
    }
    serve(aioclient_mock, release)
    await rt.get_updater(hass, DOMAIN).async_check()
    events = [
        (await client.receive_json())["event"],
        (await client.receive_json())["event"],
    ]
    assert events == [
        {"version": None, "updating": True},
        {"version": "2026.11.0", "updating": False},
    ]


async def test_several_entries(hass, aioclient_mock, release, hass_ws_client):
    serve(aioclient_mock, release)
    first = await setup_entry(hass, "one")
    second = await setup_entry(hass, "two")
    updater = rt.get_updater(hass, DOMAIN)
    assert updater.entry_ids == ["one", "two"]
    assert len(hass.states.async_entity_ids("update")) == 1
    assert rt.catalog_entities(hass, second, DOMAIN, "update") == []
    assert rt.catalog_entities(hass, first, DOMAIN, "sensor") == []

    # The hosting entry goes: the other one is reloaded and hosts the entities
    await hass.config_entries.async_unload(first.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    # The reload of the remaining entry restarted the updater
    assert rt.get_updater(hass, DOMAIN).entry_ids == ["two"]
    assert hass.states.get(UPDATE).state != "unavailable"
    await rt.async_unload_catalog(hass, first, SPEC)  # already gone: no effect

    await hass.config_entries.async_unload(second.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert rt.get_updater(hass, DOMAIN) is None

    client = await hass_ws_client(hass)
    for command in ("catalog", "catalog/subscribe", "catalog/maintenance"):
        await client.send_json_auto_id({"type": f"{DOMAIN}/{command}"})
        assert (await client.receive_json())["error"]["code"] == "not_loaded"

    # Set up again in the same run: paths and commands are not registered twice
    await hass.config_entries.async_setup(second.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert rt.get_updater(hass, DOMAIN) is not None


async def test_supplements_spec_and_no_http(hass, aioclient_mock):
    spec = rt.CatalogSpec("catalog_demo", "supplements", "Supplements")
    aioclient_mock.get(
        "https://github.com/Elwinmage/reeftech-catalog/releases/download/supplements-latest/manifest.json",
        status=404,
    )
    entry = MockConfigEntry(domain=DOMAIN, entry_id="sup")
    updater = await rt.async_setup_catalog(hass, entry, spec)
    await rt.async_setup_catalog(hass, entry, spec)  # same entry twice: listed once
    assert updater.entry_ids == ["sup"]
    assert f"websocket_{DOMAIN}" in hass.data[rt.DATA_KEY]
    assert f"paths_{DOMAIN}" not in hass.data[rt.DATA_KEY]  # no http component loaded
    await hass.async_block_till_done(wait_background_tasks=True)
    await rt.async_unload_catalog(hass, entry, spec)


async def test_dispatcher_signal(hass, aioclient_mock, release):
    """Entities of other config entries follow the catalog by a signal."""
    calls: list[None] = []
    async_dispatcher_connect(
        hass, rt.signal_catalog_changed(DOMAIN), lambda: calls.append(None)
    )
    aioclient_mock.get(release.manifest_url, status=500)
    entry = await setup_entry(hass)
    assert calls  # loaded at start
    calls.clear()
    serve(aioclient_mock, release)
    await rt.get_updater(hass, DOMAIN).async_check()
    assert calls  # installed
    calls.clear()
    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert calls  # gone
