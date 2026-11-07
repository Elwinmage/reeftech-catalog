# Consuming the catalog

Contract between this repository and the integrations and cards that use it.

## Who reads what

| Consumer | Section | Entries kept | Local folder |
|---|---|---|---|
| `reef_maintenance` | equipment | `tasks`, categories and items with scope `aquarium` | `<config>/reef_maintenance/catalog/` |
| `maintenance_hub` | equipment | everything | `<config>/maintenance_hub/catalog/` |
| `redsea` | supplements | everything | `<config>/redsea/catalog/` |

Both maintenance integrations may be installed together; each keeps its own
copy so that neither depends on the other.

## Download and install (integrations)

Same mechanism as reeftank with reeftank-catalog:

1. Read `https://github.com/Elwinmage/reeftech-catalog/releases/download/<section>-latest/manifest.json`
   at start-up, then every 12 hours. A network failure keeps the installed
   catalog, silently (debug log only).
2. Skip the release, and say so on the update entity, when `format` is newer
   than the integration knows or when `min_integration[<domain>]` is newer than
   the integration.
3. Filter the entries by `kind` and `scopes` (no download needed for that).
4. Download only the entries whose `sha256` differs from the installed ones,
   from `https://github.com/Elwinmage/reeftech-catalog/releases/download/<archive>`.
5. Check each archive: size and sha256 from the manifest, flat file names,
   only `json`/`webp`/`png`, bounded sizes, descriptors that parse.
6. Prepare the new catalog next to the old one and swap it in one move: an
   interrupted update leaves the previous catalog untouched.
7. Expose an `update.<domain>_catalog` entity (installed/latest version, notes,
   automatic by default, can be turned off in the options).
8. Merge with the built-in minimal set and with a user folder
   (`<config>/<domain>/catalog_user/`, same layout) whose entries win on
   identical ids.

## Exposure to the cards

The cards never call the network. Each integration exposes:

- a static path for the visuals: `/<domain>_catalog/<entry id>/image.webp`
  (public catalog data, no authentication needed);
- a websocket command returning the merged catalog, with visual names turned
  into URLs, the installed version and `format`:
  `reef_maintenance/catalog`, `maintenance_hub/catalog`, `redsea/supplements`;
- a `…/catalog/subscribe` command sending `{version}` when a release is
  installed, so cards refresh without a reload.

A card detects which integrations are loaded through `hass.config.components`
and falls back to its own generic visuals when none is.

## Equipment registry (maintenance integrations)

An **equipment instance** is: a catalog entry (item or category) + what it is
attached to (a socket switch such as `switch.rspower_…_socket_3`, any other
entity, a HA device, or nothing) + its tasks. It is stored by the maintenance
integration, not by redsea:

- attaching an entry offers to create its tasks (category tasks, overridden by
  the item ones); two heaters on two sockets give two task sets, named after
  their socket;
- changing the entry of an attachment offers to remove or archive the old
  tasks, never silently;
- a websocket query `…/equipment/by_entity` lets ha-reef-card show, for an
  RSPower socket, the visual of the equipment plugged on it.

When no maintenance integration is installed, the RSPower view keeps its
current behaviour (linked HA device or icon) and shows an install link.

## Supplements (redsea)

- `effects` feed the automatic dose proposal only when `verified` is true;
  the proposal is always validated by the user.
- `brine`: salinity raise per mL computed from the user concentration
  (default from `concentration_g_l`).
- A configured head keeps the supplement id; `uid` matches the Red Sea cloud
  library.
