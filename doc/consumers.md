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
| `redsea`, `reeftank` | water_tests | everything (ReefDose turns a titration amount into a value with `catalog_conversion.py`) |
| `redsea`, `reeftank` | parameters | everything (ReefDose offers the parameters with a `regulation` block; the cards take their units and bands from it) |

Both maintenance integrations may be installed together; each keeps its own
copy so that neither depends on the other.

The downloaded pack is shared (see [Shared pack](#shared-pack)), so it is keyed
by section, and so are the updaters: a domain reading several sections (redsea:
supplements, water tests, parameters) calls `async_setup_catalog` once per
section (see [Several sections](#several-sections)).

## The client

`scripts/sync_client.py`, run from the folder holding the checkouts side by
side (or with `--base`), writes into `custom_components/<domain>/`:

- `catalog_pack.py`: manifest, download, checks, atomic install, merged view
  (no Home Assistant dependency);
- `catalog_runtime.py`: the Home Assistant side (shared pack of the section,
  updater shared by all the config entries of the domain, update entity,
  automatic update switch, "update now" button, static paths, websocket
  commands);
- `catalog_builtin/`: a snapshot of the section, filtered by scopes, used
  before the first download and offline.

The copies carry a "do not edit" header; `--check` fails when one is out of
date (for a pre-commit hook). Both modules are tested at 100 % here, with a
demo integration (`tests/client/custom_components/catalog_demo`); an
integration may leave them out of its own coverage
(`omit = ["*/catalog_pack.py", "*/catalog_runtime.py"]`).

### Sections of another catalog

The client only knows the sections of this repository. A catalog that keeps
this release format in a repository of its own is read with the same client:
the integration reading it (and the tool editing it) declares each of its
sections when its package is imported, before using the client:

```python
from .catalog_pack import register_section

register_section("plants", {"plant": ("items", "plant.json")}, "someone/garden-catalog")
```

`kinds` maps each kind of entry to its folder and its descriptor file
(`plants/items/<id>/plant.json`); the last argument is the GitHub repository
publishing the releases `plants-<version>` and `plants-latest`. From then on
`repo_of("plants")` is that repository, which `PackClient`, `SharedPack`,
`async_acquire_pack` and `CatalogSpec.repository` use by default, and the
section is downloaded, checked and merged like the others (shared pack and
shared user layer included).

Each integration holds its own copy of the client, so a declaration only
concerns the integration making it; declaring a section again with the same
content does nothing, and a declaration contradicting a known section or kind
is refused. The built-in snapshots of such a section are written by the tools
of its own catalog; `sync_client.py` of this repository only copies the client.

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
# update.py (and the same in switch.py with "switch", button.py with "button")
from .catalog_runtime import catalog_entities


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([*catalog_entities(hass, entry, DOMAIN, "update"), ...])
```

`manifest.json` needs `"dependencies": ["http"]` (websocket_api comes with
it). Messages to add to `strings.json` and the translations:

```json
"exceptions": {
  "catalog_unknown": { "message": "The catalog has not been checked yet." },
  "catalog_incompatible": { "message": "Catalog {version} needs version {minimum} of the integration or newer." },
  "catalog_install_failed": { "message": "Catalog update failed: {error}" },
  "catalog_check_failed": { "message": "Catalog check failed: {error}" }
},
"entity": {
  "switch": { "catalog_auto_update": { "name": "Automatic update" } },
  "button": { "catalog_update_now": { "name": "Update now" } }
}
```

The *Update now* button checks every section of the domain and installs the
newer compatible releases at once, even with the automatic update switch off.
It is only created when the integration forwards the `button` platform to the
entry hosting the catalog.

### Several sections

A domain reading more than one section gives one `CatalogSpec` per section, and
calls `async_setup_catalog` / `async_unload_catalog` for each, with the same
config entry:

```python
SUPPLEMENTS = CatalogSpec(DOMAIN, "supplements", "Supplements", device_title="ReefBeat catalog")
WATER_TESTS = CatalogSpec(DOMAIN, "water_tests", "Water tests", by_section=True)
PARAMETERS = CatalogSpec(DOMAIN, "parameters", "Parameters", by_section=True)
```

- The **first** section set up keeps the plain folders and URLs (nothing moves
  for an integration that already reads one); the others pass
  `by_section=True`, which adds the section to them: `catalog_<section>/`,
  `catalog_user_<section>/`, `catalog_builtin_<section>/` and
  `/<domain>_catalog_<section>/...` (`sync_client.py` writes the snapshots with
  that name, see its `Reads`).
- **One device** for the domain, named after the first section (`device_title`
  when given), with one `update` entity per section, **one** automatic update
  switch and **one** *Update now* button, which apply to every section. `catalog_entities` returns them for
  the entry hosting the catalog.
- **Websocket**: the commands take an optional `section`; without it the first
  section answers, so a card that reads a single section need not name it.
  `<domain>/catalog/maintenance` is registered with the equipment section.
- **Signals**: `signal_catalog_changed(domain)` fires for a change of any
  section, `signal_catalog_changed(domain, section)` for one section.
- `get_updater(hass, domain, section=None)`, `get_updaters(hass, domain)`.

### Behaviour

1. At start-up (in the background, never delaying the set-up) and every 12
   hours, read `https://github.com/Elwinmage/reeftech-catalog/releases/download/<section>-latest/manifest.json`.
   A network failure keeps the installed catalog, quietly (debug log).
2. Download only the entries whose `sha256` changed, from
   `https://github.com/Elwinmage/reeftech-catalog/releases/download/<archive>`.
   The pack holds every scope; each integration keeps its own scopes when it
   builds its view.
3. Check each archive: size and sha256 from the manifest, flat file names,
   only `json`/`webp`/`png`, bounded sizes, JSON objects that parse.
4. Prepare the new pack next to the old one and swap it in one move: an
   interrupted update leaves the previous pack untouched.
5. Warn, with a note on the update entity, for an integration whose
   `min_integration[<domain>]` is newer than its version: the release is
   installed for the others, and this one keeps its previous pack (see
   below).
6. One config entry hosts the device *<title>* with `update.<title>`,
   `switch.<title>_automatic_update` (on by default, kept in
   `.storage/<domain>.catalog`) and `button.<title>_update_now`. When that entry is removed while others stay,
   the next one is reloaded and hosts them. An integration with several
   entries of the same kind (redsea: one per doser) gives the catalog an
   entry of its own instead, created by a config flow step, so that its
   device does not hang on one of them.
7. Each change of the catalog (loaded, installed, stopped) is also sent as
   the dispatcher signal `signal_catalog_changed(domain)`: entities of other
   entries, possibly set up before the catalog, follow it with
   `async_dispatcher_connect`.

### Shared pack

The downloaded pack of a section is common to every integration reading it and
to the editors: it is kept in `<config>/reeftech_catalog/pack/<section>/` by a
`SharedPack`, one per section, whoever downloads first. The visuals are served
at `/reeftech_catalog_pack/<section>/`.

- **Holders.** The updater of each integration is a holder, and so is a tool
  that takes the pack with `async_acquire_pack(hass, section, holder, auto=...)`
  and gives it back with `async_release_pack`. The last holder stops the checks.
- **Automatic installs.** A release is installed by itself when at least one
  holder wants it: the *Automatic update* switch of an integration turned off
  does not hold back the others. `SharedPack.async_check(force=True)` checks
  and installs whatever the holders want (a *refresh* button).
- **Updates.** Installing sends `signal_catalog_changed(domain)` for every
  updater of the section, after reloading its view. `SharedPack` also
  accepts listeners (`async_add_listener`) and keeps the reason of a failed
  check or install in `last_error`.
- **An integration too old for a release.** When `min_integration` of the new
  release is newer than the integration, the release is installed anyway; the
  integration does not use it. Right before the swap, the previous shared pack
  (when the integration could use it) is copied into the integration's own
  folder `<config>/<domain>/catalog/`, served at `/<domain>_catalog/pack/`, and
  the view is built on it. Once the integration is updated, it switches to the
  shared pack and its own folder is removed.
- **Older clients.** An integration still carrying a client from before the
  shared pack keeps downloading into its own folder, and only misses the
  sharing. When it gets the new client, its own folder is removed as soon as the
  shared pack can serve it.

### Folders

| Layer | Folder | Served at |
|---|---|---|
| built-in | `custom_components/<domain>/catalog_builtin/` | `/<domain>_catalog/builtin/` |
| downloaded (shared) | `<config>/reeftech_catalog/pack/<section>/` | `/reeftech_catalog_pack/<section>/` |
| downloaded (own, only for an integration too old for the release) | `<config>/<domain>/catalog/` | `/<domain>_catalog/pack/` |
| shared user | `<config>/reeftech_catalog/user/<section>/` | `/<domain>_catalog/shared/` |
| user | `<config>/<domain>/catalog_user/` | `/<domain>_catalog/user/` |

For a section after the first of its domain, `_<section>` is added to the
folder name of the built-in, own and user rows (`catalog_user_water_tests/`) and
to the URL prefix (`/<domain>_catalog_water_tests/...`).

All of them share the section layout of this repository (the shared folders
have one subfolder per section, as `items/` exists in each). On identical
ids the later layer wins: built-in < downloaded < shared user < user. Entries of
both user layers are reported with `source: "user"`. Visuals are public catalog
data, served without authentication.

### Shared user layer

The shared folder is common to every integration reading the section, so a
tool editing the catalog writes **once** and all of them see the entry. The
folder is created by the integrations. A writer:

1. writes the entry in `<config>/reeftech_catalog/user/<section>/` with the
   section layout (`items/<id>/item.json`, `image.webp`, `thumb.webp`...),
   after checking it against the schemas of the release (see below);
2. sends the dispatcher signal `catalog_runtime.SIGNAL_USER_CATALOG_CHANGED`
   (`reeftech_catalog_user_changed`) with the section changed, or `None` for all
   of them. Each updater of that section reloads its view in the background,
   notifies its listeners (cards subscribed to `<domain>/catalog/subscribe`
   reload) and stamps the visuals of the shared layer with a new revision, so
   that a replaced image is fetched again.

Nothing is read from the shared folder between two signals (and at start-up).
When an entry reaches the catalog in a release, the writer removes its own copy.

### Schemas of a release

The manifest of each release has a `schema` record (`archive`, `sha256`,
`size`, `since`, like an entry) pointing to `schema.zip`: `common.schema.json`
and the schemas of the kinds of the section. It is data only, and it is not an
entry: integrations reading the entries never download it. A tool editing the
catalog reads it with `PackClient.entry(manifest.schema)` and `extract_entry`, to
check a draft against the schemas of the release the shared pack has installed. Rules that JSON Schema cannot express (cross references, bounds
between fields, uniqueness) are in `client/catalog_rules.py`, used both by
`scripts/validate.py` (CI) and by the editing tools, which get a copy through
`sync_client.py`: a release never carries code. `validate_draft` runs, for one
entry, everything the CI would check.

## Exposure to the cards

The cards never call the network. Each integration answers:

- `<domain>/catalog` (`refresh`: optional, re-reads the folders; `section`:
  optional, the first section of the domain by default): `{section,
  format, version, updating, data}`. For equipment `data` holds `tasks`,
  `categories` and `items`; for supplements and water tests `items`. Every category, item
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

- attaching an entry offers to create its tasks (`resolve_maintenance`). A task
  icon is `mdi:` or `redsea:` (the Red Sea set, provided by the redsea
  integration): pass `icon_sets=("mdi", "redsea")` when that set is available, the
  default `("mdi",)` gives `None` for a `redsea:` icon so that your own default shows;
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
