# Consuming the catalog

Contract between this repository and the integrations and cards that use it.
The integration side is implemented once, in [`client/`](../client), and
copied into each integration by `scripts/sync_client.py`.

## Who reads what

| Consumer | Section | Entries kept |
|---|---|---|
| `reef_maintenance` | equipment | task library, categories and items with scope `aquarium` |
| `maintenance_hub` | equipment | everything |
| `redsea` | supplements | everything |

Both maintenance integrations may be installed together; each keeps its own
copy so that neither depends on the other.

## The client

`scripts/sync_client.py`, run from the folder holding the checkouts side by
side (or with `--base`), writes into `custom_components/<domain>/`:

- `catalog_pack.py`: manifest, download, checks, atomic install, merged view
  (no Home Assistant dependency);
- `catalog_runtime.py`: the Home Assistant side (updater shared by all the
  config entries of the domain, update entity, automatic update switch,
  static paths, websocket commands);
- `catalog_builtin/`: a snapshot of the section, filtered by scopes, used
  before the first download and offline.

The copies carry a "do not edit" header; `--check` fails when one is out of
date (for a pre-commit hook). Both modules are tested at 100 % here, with a
demo integration (`tests/client/custom_components/catalog_demo`); an
integration may leave them out of its own coverage
(`omit = ["*/catalog_pack.py", "*/catalog_runtime.py"]`).

### Wiring

```python
# __init__.py
from .catalog_runtime import CatalogSpec, async_setup_catalog, async_unload_catalog

CATALOG = CatalogSpec(DOMAIN, "supplements", "ReefBeat supplements catalog")
# reef_maintenance: CatalogSpec(DOMAIN, "equipment", "Reef equipment catalog", scopes=("aquarium",))
# maintenance_hub:  CatalogSpec(DOMAIN, "equipment", "Equipment catalog")

async def async_setup_entry(hass, entry):
    await async_setup_catalog(hass, entry, CATALOG)
    ...

async def async_unload_entry(hass, entry):
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    await async_unload_catalog(hass, entry, CATALOG)
    return unloaded
```

```python
# update.py (and the same in switch.py with "switch")
from .catalog_runtime import catalog_entities

async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([*catalog_entities(hass, entry, DOMAIN, "update"), ...])
```

`manifest.json` needs `"dependencies": ["http"]` (websocket_api comes with
it). Exception messages to add to `strings.json` and the translations:

```json
"exceptions": {
  "catalog_unknown": { "message": "The catalog has not been checked yet." },
  "catalog_incompatible": { "message": "Catalog {version} needs version {minimum} of the integration or newer." },
  "catalog_install_failed": { "message": "Catalog update failed: {error}" }
}
```

### Behaviour

1. At start-up (in the background, never delaying the set-up) and every 12
   hours, read `https://github.com/Elwinmage/reeftech-catalog/releases/download/<section>-latest/manifest.json`.
   A network failure keeps the installed catalog, quietly (debug log).
2. Keep the entries of the consumer's scopes, using the manifest only.
3. Skip, with a warning and a note on the update entity, a release whose
   `min_integration[<domain>]` is newer than the integration.
4. Download only the entries whose `sha256` changed, from
   `https://github.com/Elwinmage/reeftech-catalog/releases/download/<archive>`.
5. Check each archive: size and sha256 from the manifest, flat file names,
   only `json`/`webp`/`png`, bounded sizes, JSON objects that parse.
6. Prepare the new pack next to the old one and swap it in one move: an
   interrupted update leaves the previous pack untouched.
7. One config entry hosts the device *<title>* with `update.<title>` and
   `switch.<title>_automatic_update` (on by default, kept in
   `.storage/<domain>.catalog`). When that entry is removed while others stay,
   the next one is reloaded and hosts them.

### Folders

| Layer | Folder | Served at |
|---|---|---|
| built-in | `custom_components/<domain>/catalog_builtin/` | `/<domain>_catalog/builtin/` |
| downloaded | `<config>/<domain>/catalog/` | `/<domain>_catalog/pack/` |
| user | `<config>/<domain>/catalog_user/` | `/<domain>_catalog/user/` |

All three share the section layout of this repository. On identical ids the
user layer wins over the downloaded one, which wins over the built-in one.
Visuals are public catalog data, served without authentication.

## Exposure to the cards

The cards never call the network. Each integration answers:

- `<domain>/catalog` (`refresh`: optional, re-reads the folders): `{section,
  format, version, updating, data}`. For equipment `data` holds `tasks`,
  `categories` and `items`; for supplements `items`. Every category, item
  and supplement carries `image`, `thumb` (URLs or null) and `source`
  (`builtin`, `pack`, `user`); items carry their resolved `scopes`.
- `<domain>/catalog/subscribe`: an event `{version, updating}` each time it
  changes, so cards reload the catalog without a page reload.
- `<domain>/catalog/maintenance` (equipment only, `item_id` or
  `category_id`): the tasks an equipment would get, category tasks overridden
  by the item ones, with names, schedule and `regulatory`.

A card detects which integrations are loaded through `hass.config.components`
and falls back to its own generic visuals when none is.

## Equipment registry (maintenance integrations)

An **equipment instance** is: a catalog entry (item or category) + what it is
attached to (a socket switch such as `switch.rspower_…_socket_3`, any other
entity, a HA device, or nothing) + its tasks. It is stored by the maintenance
integration, not by redsea:

- attaching an entry offers to create its tasks (`resolve_maintenance`);
  two heaters on two sockets give two task sets, named after their socket;
- changing the entry of an attachment offers to remove or archive the old
  tasks, never silently;
- a websocket query `…/equipment/by_entity` lets ha-reef-card show, for an
  RSPower socket, the visual of the equipment plugged on it.

When no maintenance integration is installed, the RSPower view keeps its
current behaviour (linked HA device or icon) and shows an install link.

## Supplements (redsea)

- Supplements are looked up by `uid`, then by `fullname`.
- `effects` feed the automatic dose proposal only when `verified` is true;
  the proposal is always validated by the user.
- `brine`: salinity raise per mL computed from the user concentration
  (default from `concentration_g_l`).
