# Contributing

## Add an equipment model

1. Find its category in `equipment/categories/` (or propose a new one).
2. Create `equipment/items/<brand>-<model>/item.json`, ids in lower case with
   `-` or `_` (`eheim-thermocontrol-200`).
3. Add the tasks it needs beyond its category, or other intervals, in
   `maintenance`. Reuse tasks of `equipment/tasks/`; add a new task file only
   when none fits, with at least an English name.
4. Optional visual: `image.webp` (transparent background, about 512 px on the
   long side, < 300 KB) and `thumb.webp` (128 px, < 40 KB).

## Add a supplement

Create `supplements/items/<brand>-<name>/supplement.json`. Dosing data
(`effects`) must come from the manufacturer: give the `source` and set
`verified` to `true` only once it has been checked.

## Visuals

Only visuals you are allowed to share under CC BY 4.0: your own photos or
drawings. No pictures copied from manufacturers' sites or shops.

## Before opening the pull request

```
python scripts/validate.py
pytest
```
