"""Tests of catalog_runtime inside Home Assistant (demo integration)."""

from __future__ import annotations

import json
import shutil
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.dispatcher import (
    async_dispatcher_connect,
    async_dispatcher_send,
)
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from conftest import Release, build_release, dump
from custom_components.catalog_demo import SPEC
from custom_components.catalog_demo import catalog_runtime as rt

DOMAIN = "catalog_demo"
UPDATE = "update.demo_equipment_catalog"
SWITCH = "switch.demo_equipment_catalog_automatic_update"
BUTTON = "button.demo_equipment_catalog_update_now"


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
    """The shared pack of the section, common to every reader."""
    return Path(hass.config.path("reeftech_catalog", "pack", "equipment"))


def own_pack(hass: HomeAssistant) -> Path:
    """The pack of the domain's own folder (an integration too old for a release)."""
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
    # The shared pack holds every scope; the view keeps the aquarium ones
    assert (pack(hass) / "items/eheim/item.json").is_file()
    assert (pack(hass) / "categories/boiler/category.json").is_file()
    assert not own_pack(hass).joinpath("items").exists()

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
        == "/reeftech_catalog_pack/equipment/items/eheim/image.webp?v=2026.11.0"
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
    response = await http.get("/reeftech_catalog_pack/equipment/items/eheim/image.webp")
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
    """No previous pack to give: the integration keeps its built-in snapshot."""
    release = build_release(
        catalog_root, tmp_path / "d", "equipment", "2026.11.0", catalog_demo="9.0.0"
    )
    serve(aioclient_mock, release)
    await setup_entry(hass)
    assert "needs catalog_demo 9.0.0 or newer" in caplog.text
    updater = rt.get_updater(hass, DOMAIN)
    # The release is installed for the readers that can use it...
    assert updater.shared.installed.version == "2026.11.0"
    # ...but not used by this one
    assert not updater.uses_shared and updater.installed is None
    assert "eheim" not in updater.view["items"]
    state = hass.states.get(UPDATE)
    assert state.attributes["installed_version"] == rt.NOT_INSTALLED
    assert state.attributes["release_summary"] == "Needs catalog_demo 9.0.0 or newer."
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            "update", "install", {"entity_id": UPDATE}, blocking=True
        )


async def test_too_old_integration_keeps_the_previous_pack(
    hass, aioclient_mock, release, catalog_root, tmp_path
):
    """A release it cannot use leaves it the previous pack as its own."""
    serve(aioclient_mock, release)
    await setup_entry(hass)
    updater = rt.get_updater(hass, DOMAIN)
    assert updater.uses_shared and not own_pack(hass).joinpath("items").exists()

    second = build_release(
        catalog_root,
        tmp_path / "d2",
        "equipment",
        "2026.11.1",
        release.manifest,
        catalog_demo="9.0.0",
    )
    serve(aioclient_mock, second)
    await updater.async_check()
    await hass.async_block_till_done(wait_background_tasks=True)

    assert updater.shared.installed.version == "2026.11.1"
    assert not updater.uses_shared and not updater.compatible
    assert updater.installed.version == "2026.11.0"  # its own copy
    assert (own_pack(hass) / "items/eheim/item.json").is_file()
    assert updater.view["items"]["eheim"]["image"] == (
        f"/{DOMAIN}_catalog/pack/items/eheim/image.webp?v=2026.11.0"
    )
    state = hass.states.get(UPDATE)
    assert state.attributes["installed_version"] == "2026.11.0"
    assert state.attributes["latest_version"] == "2026.11.1"

    # Updated: the shared pack serves again and the own folder is dropped
    updater.integration_version = "9.1.0"
    await updater.async_reload()
    assert updater.uses_shared and updater.installed.version == "2026.11.1"
    assert not own_pack(hass).joinpath("items").exists()
    assert updater.view["items"]["eheim"]["image"].startswith(
        "/reeftech_catalog_pack/equipment/"
    )


async def test_own_folder_of_an_older_client_is_dropped(hass, aioclient_mock, release):
    """The pack an integration downloaded itself is replaced by the shared one."""
    serve(aioclient_mock, release)
    entry = await setup_entry(hass)
    shutil.copytree(pack(hass), own_pack(hass), dirs_exist_ok=True)
    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert rt.get_shared_pack(hass, "equipment") is None
    assert (own_pack(hass) / "items/eheim/item.json").is_file()

    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert rt.get_updater(hass, DOMAIN).uses_shared
    assert not own_pack(hass).joinpath("items").exists()


async def test_broken_archive(hass, aioclient_mock, release, caplog):
    for url in release.files:
        release.files[url] = b"x" * len(release.files[url])
    serve(aioclient_mock, release)
    await setup_entry(hass)
    assert "catalog update failed" in caplog.text
    assert hass.states.get(UPDATE).attributes["installed_version"] == rt.NOT_INSTALLED
    assert not rt.get_updater(hass, DOMAIN).in_progress


async def test_manual_install_failure(hass, aioclient_mock, release):
    await async_setup_component(hass, "homeassistant", {})
    await rt.Store(hass, 1, f"{DOMAIN}.catalog").async_save({"auto_update": False})
    for url in release.files:
        release.files[url] = b"x" * len(release.files[url])
    serve(aioclient_mock, release)
    await setup_entry(hass)
    with pytest.raises(HomeAssistantError, match="Catalog update failed"):
        await hass.services.async_call(
            "update", "install", {"entity_id": UPDATE}, blocking=True
        )
    assert rt.get_updater(hass, DOMAIN).installed is None


async def test_install_in_progress_is_skipped(hass, aioclient_mock, release):
    aioclient_mock.get(release.manifest_url, status=500)
    await setup_entry(hass)
    updater = rt.get_updater(hass, DOMAIN)
    serve(aioclient_mock, release)
    updater.shared.in_progress = True
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
    assert "pack_paths_supplements" not in hass.data[rt.DATA_KEY]
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


async def test_shared_user_layer(hass, aioclient_mock, release, hass_client):
    """A writer drops entries in the shared folder and tells the integration."""
    serve(aioclient_mock, release)
    await setup_entry(hass)
    updater = rt.get_updater(hass, DOMAIN)
    shared = Path(hass.config.path("reeftech_catalog", "user", "equipment"))
    assert shared.is_dir() and updater.shared_root == shared
    assert "mine" not in updater.view["items"]

    calls: list[None] = []
    async_dispatcher_connect(
        hass, rt.signal_catalog_changed(DOMAIN), lambda: calls.append(None)
    )
    dump(
        shared / "items/mine/item.json",
        {"category": "heater", "brand": "Me", "model": "1"},
    )
    (shared / "items/mine/image.webp").write_bytes(b"RIFFmine")
    # Overrides the downloaded item
    dump(
        shared / "items/eheim/item.json",
        {"category": "heater", "brand": "Shared", "model": "200"},
    )

    # Nothing is read until the writer says so
    assert "mine" not in updater.view["items"]
    async_dispatcher_send(hass, rt.SIGNAL_USER_CATALOG_CHANGED, "equipment")
    await hass.async_block_till_done(wait_background_tasks=True)
    assert calls and updater.user_revision == 1
    items = updater.view["items"]
    assert items["mine"]["source"] == "user"
    assert (
        items["mine"]["image"] == f"/{DOMAIN}_catalog/shared/items/mine/image.webp?v=u1"
    )
    assert items["eheim"]["brand"] == "Shared"  # shared layer beats the pack

    http = await hass_client()
    response = await http.get(f"/{DOMAIN}_catalog/shared/items/mine/image.webp")
    assert response.status == 200 and await response.read() == b"RIFFmine"

    # The user folder of the domain is the most specific layer
    dump(
        Path(hass.config.path(DOMAIN, "catalog_user")) / "items/eheim/item.json",
        {"category": "heater", "brand": "Domain", "model": "200"},
    )
    # A change of another section is not for this domain; None is for all of them
    async_dispatcher_send(hass, rt.SIGNAL_USER_CATALOG_CHANGED, "supplements")
    await hass.async_block_till_done(wait_background_tasks=True)
    assert (
        updater.user_revision == 1
        and updater.view["items"]["eheim"]["brand"] == "Shared"
    )
    async_dispatcher_send(hass, rt.SIGNAL_USER_CATALOG_CHANGED, None)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert updater.user_revision == 2
    assert updater.view["items"]["eheim"]["brand"] == "Domain"
    assert updater.view["items"]["mine"]["image"].endswith(
        "image.webp?v=u2"
    )  # new stamp: browsers reload the visual

    # Once the integration is unloaded, the signal is not listened to any more
    await rt.async_unload_catalog(
        hass, hass.config_entries.async_entries(DOMAIN)[0], SPEC
    )
    async_dispatcher_send(hass, rt.SIGNAL_USER_CATALOG_CHANGED, None)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert updater.user_revision == 2


# -- shared pack: holders other than an integration (editors) --------------------


async def test_editor_holds_the_pack_without_any_integration(
    hass, aioclient_mock, release
):
    """A tool that reads no section keeps and refreshes the pack all the same."""
    serve(aioclient_mock, release)
    await async_setup_component(hass, "http", {})
    pack_ = await rt.async_acquire_pack(hass, "equipment", "editor", auto=True)
    assert rt.get_shared_pack(hass, "equipment") is pack_ and pack_.wants_auto
    changes: list[None] = []
    remove = pack_.async_add_listener(lambda: changes.append(None))
    await hass.async_block_till_done(wait_background_tasks=True)
    assert pack_.installed.version == "2026.11.0" and changes
    assert (pack(hass) / "items/eheim/item.json").is_file()
    assert pack_.last_error is None and not pack_.update_available

    # A second holder shares it and is not started again
    again = await rt.async_acquire_pack(hass, "equipment", "other")
    assert again is pack_ and pack_.holders["other"] == (False, rt.CHECK_HOURS)
    pack_.set_auto("editor", False)
    assert not pack_.wants_auto

    remove()
    changes.clear()
    await pack_.async_check()
    assert not changes

    rt.async_release_pack(hass, "equipment", "unknown")  # not a holder: no effect
    rt.async_release_pack(hass, "equipment", "editor")
    assert rt.get_shared_pack(hass, "equipment") is pack_  # "other" still holds it
    rt.async_release_pack(hass, "equipment", "other")
    assert rt.get_shared_pack(hass, "equipment") is None
    rt.async_release_pack(hass, "equipment", "other")  # gone: no effect


async def test_forced_check_and_never_checked(
    hass, aioclient_mock, release, catalog_root, tmp_path
):
    aioclient_mock.get(release.manifest_url, status=500)
    await async_setup_component(hass, "http", {})
    pack_ = await rt.async_acquire_pack(hass, "equipment", "editor")  # no auto
    await hass.async_block_till_done(wait_background_tasks=True)
    with pytest.raises(rt.PackError, match="not been checked"):
        await pack_.async_install()
    assert pack_.last_error and pack_.installed is None
    assert not pack_.update_available  # nothing known yet

    serve(aioclient_mock, release)
    await pack_.async_check()  # nobody wants an automatic install
    assert pack_.installed is None and pack_.update_available
    await pack_.async_check(force=True)
    assert pack_.installed.version == "2026.11.0"

    # The check pace is the one of the most demanding holder
    await rt.async_acquire_pack(hass, "equipment", "fast", check_hours=1)
    serve(aioclient_mock, release)
    second = build_release(
        catalog_root, tmp_path / "d2", "equipment", "2026.11.1", release.manifest
    )
    serve(aioclient_mock, second)
    pack_.set_auto("fast", True)
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(hours=1, minutes=1))
    await hass.async_block_till_done(wait_background_tasks=True)
    assert pack_.installed.version == "2026.11.1"
    # Give the pack back: its timer must not outlive the test
    rt.async_release_pack(hass, "equipment", "fast")
    rt.async_release_pack(hass, "equipment", "editor")


async def test_acquire_failure_leaves_nothing_behind(hass, monkeypatch):
    def broken(self):
        raise OSError("read-only file system")

    monkeypatch.setattr(rt.SharedPack, "_prepare_folder", broken)
    with pytest.raises(OSError):
        await rt.async_acquire_pack(hass, "equipment", "editor")
    assert rt.get_shared_pack(hass, "equipment") is None


# -- a domain reading two sections ----------------------------------------------

SECOND = "supplements"
SECOND_UPDATE = "update.demo_catalog_demo_supplements_catalog"
FIRST_UPDATE = "update.demo_catalog_demo_equipment_catalog"
DEVICE_SWITCH = "switch.demo_catalog_automatic_update"


@pytest.fixture
def two_sections(monkeypatch):
    from custom_components import catalog_demo

    first = replace(SPEC, device_title="Demo catalog")
    monkeypatch.setattr(catalog_demo, "SPECS", [first, catalog_demo.SPEC_SECOND])


@pytest.fixture
def second_release(catalog_root, tmp_path) -> Release:
    return build_release(catalog_root, tmp_path / "dist2", SECOND, "2026.11.1")


def serve_both(aioclient_mock, release: Release, second: Release) -> None:
    serve(aioclient_mock, release)
    for url, body in second.files.items():
        aioclient_mock.get(url, content=body)
    aioclient_mock.get(second.manifest_url, text=json.dumps(second.manifest))


async def test_two_sections_one_device(
    hass, aioclient_mock, release, second_release, two_sections, hass_ws_client
):
    serve_both(aioclient_mock, release, second_release)
    entry = await setup_entry(hass)

    # One update entity per section on one device, one switch for the domain
    assert hass.states.get(FIRST_UPDATE).attributes["installed_version"] == "2026.11.0"
    assert hass.states.get(SECOND_UPDATE).attributes["installed_version"] == (
        "2026.11.1"
    )
    assert hass.states.get(DEVICE_SWITCH).state == "on"
    switches = [s for s in hass.states.async_entity_ids("switch")]
    assert switches == [DEVICE_SWITCH]
    from homeassistant.helpers import device_registry as dr

    # Through the helper: reading `devices` as a mapping is deprecated
    devices = dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
    assert [d.name for d in devices] == ["Demo catalog"]
    assert [
        rt.get_updater(hass, DOMAIN, s).spec.section for s in ("equipment", SECOND)
    ] == [
        "equipment",
        SECOND,
    ]
    assert [u.spec.section for u in rt.get_updaters(hass, DOMAIN)] == [
        "equipment",
        SECOND,
    ]
    # Without a section: the first one
    assert rt.get_updater(hass, DOMAIN) is rt.get_updater(hass, DOMAIN, "equipment")
    assert rt.get_updater(hass, DOMAIN, "parameters") is None

    client = await hass_ws_client(hass)
    await client.send_json_auto_id({"type": f"{DOMAIN}/catalog"})
    result = (await client.receive_json())["result"]
    assert result["section"] == "equipment"
    await client.send_json_auto_id({"type": f"{DOMAIN}/catalog", "section": SECOND})
    result = (await client.receive_json())["result"]
    assert result["section"] == SECOND and result["version"] == "2026.11.1"
    items = result["data"]["items"]
    assert set(items) == {"kh", "builtin-kh"}
    assert items["builtin-kh"]["source"] == "builtin"
    assert items["kh"]["source"] == "pack"
    await client.send_json_auto_id(
        {"type": f"{DOMAIN}/catalog", "section": "parameters"}
    )
    answer = await client.receive_json()
    assert answer["error"]["code"] == "not_loaded"
    assert "parameters" in answer["error"]["message"]
    await client.send_json_auto_id(
        {"type": f"{DOMAIN}/catalog/subscribe", "section": SECOND}
    )
    assert (await client.receive_json())["success"]
    assert (await client.receive_json())["event"]["version"] == "2026.11.1"


async def test_two_sections_static_files(
    hass, aioclient_mock, release, second_release, two_sections, hass_client
):
    serve_both(aioclient_mock, release, second_release)
    await setup_entry(hass)
    http = await hass_client()
    # The first section keeps the plain URL, the second one carries its name
    assert (
        await http.get(f"/{DOMAIN}_catalog/builtin/tasks/visual_inspection.json")
    ).status == 200
    response = await http.get(
        f"/{DOMAIN}_catalog_{SECOND}/builtin/items/builtin-kh/supplement.json"
    )
    assert response.status == 200
    assert Path(hass.config.path(DOMAIN, f"catalog_user_{SECOND}")).is_dir()
    assert Path(hass.config.path(DOMAIN, "catalog_user")).is_dir()


async def test_one_switch_for_every_section(
    hass, aioclient_mock, release, second_release, two_sections
):
    serve_both(aioclient_mock, release, second_release)
    await setup_entry(hass)
    first, second = rt.get_updaters(hass, DOMAIN)
    await hass.services.async_call(
        "switch", "turn_off", {"entity_id": DEVICE_SWITCH}, blocking=True
    )
    assert not first.auto_update and not second.auto_update
    assert not first.shared.wants_auto and not second.shared.wants_auto
    assert hass.states.get(FIRST_UPDATE).attributes["auto_update"] is False
    assert hass.states.get(SECOND_UPDATE).attributes["auto_update"] is False

    calls = []
    for updater in (first, second):
        updater.async_check = _recorder(calls, updater.spec.section)
    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": DEVICE_SWITCH}, blocking=True
    )
    assert calls == ["equipment", SECOND]
    assert first.auto_update and second.auto_update


def _recorder(calls: list, name: str):
    async def check() -> None:
        calls.append(name)

    return check


async def test_section_signals(
    hass, aioclient_mock, release, second_release, two_sections
):
    serve_both(aioclient_mock, release, second_release)
    await setup_entry(hass)
    domain_calls, second_calls = [], []
    async_dispatcher_connect(
        hass, rt.signal_catalog_changed(DOMAIN), lambda: domain_calls.append(None)
    )
    async_dispatcher_connect(
        hass,
        rt.signal_catalog_changed(DOMAIN, SECOND),
        lambda: second_calls.append(None),
    )
    rt.get_updater(hass, DOMAIN, SECOND).notify()
    rt.get_updater(hass, DOMAIN, "equipment").notify()
    await hass.async_block_till_done()
    assert (len(domain_calls), len(second_calls)) == (2, 1)

    # Unloading tells both readers; each section goes on its own
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert rt.get_updaters(hass, DOMAIN) == []
    assert len(second_calls) == 2


async def test_maintenance_command_follows_equipment(
    hass, aioclient_mock, release, second_release, monkeypatch, hass_ws_client
):
    """The equipment section may come after another one."""
    from custom_components import catalog_demo

    monkeypatch.setattr(catalog_demo, "SPECS", [catalog_demo.SPEC_SECOND, SPEC])
    serve_both(aioclient_mock, release, second_release)
    await setup_entry(hass)
    client = await hass_ws_client(hass)
    # No section: the first one set up, here the supplements
    await client.send_json_auto_id({"type": f"{DOMAIN}/catalog"})
    assert (await client.receive_json())["result"]["section"] == SECOND
    await client.send_json_auto_id(
        {"type": f"{DOMAIN}/catalog/maintenance", "item_id": "eheim"}
    )
    tasks = (await client.receive_json())["result"]["tasks"]
    assert [t["task"] for t in tasks] == ["clean"]


async def press(hass: HomeAssistant, entity_id: str = BUTTON) -> None:
    await hass.services.async_call(
        "button", "press", {"entity_id": entity_id}, blocking=True
    )
    await hass.async_block_till_done(wait_background_tasks=True)


async def test_update_now_button(hass, aioclient_mock, release, catalog_root, tmp_path):
    """Installs at once, even with the automatic update switch off."""
    await rt.Store(hass, 1, f"{DOMAIN}.catalog").async_save({"auto_update": False})
    serve(aioclient_mock, release)
    await setup_entry(hass)
    assert hass.states.get(BUTTON) is not None
    assert hass.states.get(UPDATE).attributes["installed_version"] == rt.NOT_INSTALLED

    await press(hass)
    assert hass.states.get(UPDATE).attributes["installed_version"] == "2026.11.0"

    # Nothing newer: checks again, installs nothing
    aioclient_mock.clear_requests()
    serve(aioclient_mock, release)
    await press(hass)
    assert [str(c[1]) for c in aioclient_mock.mock_calls] == [release.manifest_url]

    # A newer release, still with the switch off
    (catalog_root / "equipment/items/eheim/image.webp").write_bytes(b"RIFFnew")
    second = build_release(
        catalog_root, tmp_path / "d2", "equipment", "2026.11.1", release.manifest
    )
    serve(aioclient_mock, second)
    await press(hass)
    assert hass.states.get(UPDATE).attributes["installed_version"] == "2026.11.1"
    assert hass.states.get(SWITCH).state == "off"


async def test_update_now_button_errors(hass, aioclient_mock, catalog_root, tmp_path):
    aioclient_mock.get(
        "https://github.com/Elwinmage/reeftech-catalog/releases/download/"
        "equipment-latest/manifest.json",
        status=500,
    )
    await setup_entry(hass)
    with pytest.raises(HomeAssistantError) as err:
        await press(hass)
    assert err.value.translation_key == "catalog_check_failed"

    # Too new for the integration: said, not installed
    release = build_release(
        catalog_root, tmp_path / "d", "equipment", "2026.11.0", catalog_demo="9.0.0"
    )
    serve(aioclient_mock, release)
    with pytest.raises(HomeAssistantError) as err:
        await press(hass)
    assert err.value.translation_key == "catalog_incompatible"


async def test_one_button_for_every_section(
    hass, aioclient_mock, release, second_release, two_sections
):
    await rt.Store(hass, 1, f"{DOMAIN}.catalog").async_save({"auto_update": False})
    serve_both(aioclient_mock, release, second_release)
    await setup_entry(hass)
    assert hass.states.async_entity_ids("button") == ["button.demo_catalog_update_now"]
    await press(hass, "button.demo_catalog_update_now")
    assert hass.states.get(FIRST_UPDATE).attributes["installed_version"] == "2026.11.0"
    assert hass.states.get(SECOND_UPDATE).attributes["installed_version"] == (
        "2026.11.1"
    )
