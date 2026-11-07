"""Minimal integration used to test the catalog client (symlinked modules)."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .catalog_runtime import CatalogSpec, async_setup_catalog, async_unload_catalog

DOMAIN = "catalog_demo"
SPEC = CatalogSpec(DOMAIN, "equipment", "Demo equipment catalog", scopes=("aquarium",))
PLATFORMS = [Platform.SWITCH, Platform.UPDATE]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Start the catalog, then the platforms."""
    await async_setup_catalog(hass, entry, SPEC)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload the platforms, then the catalog."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    await async_unload_catalog(hass, entry, SPEC)
    return unloaded
