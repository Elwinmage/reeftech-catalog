# reeftech-catalog

Data catalog of the ReefTech ecosystem: non-connected equipment with their
maintenance tasks, and dosing supplements. Adding a heater model or a new
supplement is a pull request here, not a new release of the integrations or of
the cards.

| Section | Content | Downloaded by | Shown by |
|---|---|---|---|
| `equipment` | task library, equipment categories (generic image and tasks), equipment models | [reef_maintenance](https://github.com/Elwinmage/ha-reef-maintenance-component) (scope `aquarium`), [maintenance_hub](https://github.com/Elwinmage/ha-maintenance-hub) (all scopes) | ha-reef-card (RSPower sockets, maintenance view), maintenance hub card |
| `supplements` | supplements, bottle visuals, dosing data | [redsea](https://github.com/Elwinmage/ha-reefbeat-component) | ha-reef-card (ReefDose) |

The cards never access the network: integrations download the releases, check
them, keep them under `<config>` and serve them locally, with a built-in
snapshot for their first start and for offline installs. The integration side
is written once in [`client/`](client) and copied into each integration by
`scripts/sync_client.py`. See [doc/consumers.md](doc/consumers.md).

## Layout

```
schema/                         JSON schemas (2020-12)
equipment/
  tasks/<task_id>.json          maintenance task templates
  categories/<category_id>/
    category.json               name, scopes, icon, default tasks
    image.webp                  generic visual (optional)
    thumb.webp                  small visual for lists (optional)
  items/<item_id>/
    item.json                   category, brand, model, power, task overrides
    image.webp / thumb.webp     (optional, category visual otherwise)
supplements/
  items/<supplement_id>/
    supplement.json             kind, colour, dosing data
    image.webp / thumb.webp     bottle visual (optional, generic bottle otherwise)
scripts/                        validate.py, make_release.py, sync_client.py
client/                         catalog client copied into the integrations
```

A file or folder name is the entry id. Ids are **stable**: user
configurations refer to them, so an entry is never renamed, only removed.

## Equipment

- **Scopes** (`aquarium`, `pool`, `home`) say where an equipment lives.
  reef_maintenance keeps `aquarium` entries, maintenance_hub keeps all of them.
- A **category** works on its own: an equipment linked to “heater” already gets
  a visual and the usual heater tasks. A **model** only refines it (own visual,
  nominal power, other intervals).
- **Tasks** are shared templates (`equipment/tasks/`) carrying names and
  descriptions in every language. Categories and models reference them and may
  override `schedule` and `regulatory`. A model keeps its category tasks unless
  `inherit_maintenance` is `false`.
- **Schedules** are either `interval` (unit `days`/`weeks`/`months`/`years`,
  with a default and the bounds the user may choose within) or `seasonal`
  (yearly anchor date, task due `window_days` before it).

```json
{
  "category": "heater",
  "brand": "Eheim",
  "model": "thermocontrol 200",
  "power_w": 200,
  "maintenance": [
    { "task": "heater_clean", "schedule": { "type": "interval", "unit": "months", "every": { "default": 6, "min": 3, "max": 12 } } }
  ]
}
```

## Supplements

The 95 supplements of ha-reef-card (12 brands, with their bottle visuals) live
here; the card no longer embeds them.

- `uid`: Red Sea cloud uid (the card and redsea look supplements up by it);
  `fullname`: `<brand> - <name>` as known by the ReefBeat library. Both are
  unique and never change.
- `name` is the product name as printed (not translated); `display_name` and
  `short_name` are the labels of the dosing head and of the bottle visual.
- `sizes`: bottle sizes in mL offered for the container volume.
- `kind`: `kh`, `ca`, `mg`, `trace`, `nutrient`, `food`, `bundle`, `brine`,
  `other`.
- `bundle` (kind `bundle` only): its parts, each one another supplement of the
  catalog, with a slot and a dosing ratio to part 1 (Red Sea ReefCare).
- `effects`: raise of a parameter for **1 mL in 100 L**, used by the automatic
  dose proposal. Values outside plausible bounds are rejected by the checks.
- `verified`: dosing data checked against the manufacturer, with a `source`.
  Unverified effects are never used for automatic proposals.
- `brine` (DIY Brine) is the only kind with `concentration_g_l` and the only
  one allowed to drive salinity regulation (`"regulation": ["salinity"]`); its
  raise is computed from the concentration, so it has no `effects`.

## Releases

Each section is released on its own (*Actions → Release*), with a version like
`2026.11.0`:

- `<section>-<version>`: the archives of the entries that changed, the manifest
  and the notes. Unchanged entries keep pointing to the release that built them,
  so integrations only download what changed. **Never delete a versioned
  release.**
- `<section>-latest`: only the current `manifest.json`, at a fixed URL.

After a release, run `sync_client.py` so that the next version of each
integration ships an up-to-date built-in snapshot.

The manifest gives, per entry, its kind, scopes, archive, sha256, size and the
version that last changed it, plus the oldest version of each integration able
to read the section (`min_integration`).

## Checks

```
pip install -r requirements.txt
python scripts/validate.py          # errors fail, warnings are listed
python scripts/validate.py --strict # warnings fail too
pytest --cov=scripts --cov=client
python scripts/sync_client.py --base .. [--check]  # refresh the integrations' copies
```

Warnings are missing translations of tasks and equipment (`en` is
mandatory, `fr de es it pl pt` expected), missing visuals and unverified
dosing data. See
[CONTRIBUTING.md](CONTRIBUTING.md) to add an entry.

## License

Catalog data and visuals: CC BY 4.0. Scripts: MIT. See [LICENSE.md](LICENSE.md).
