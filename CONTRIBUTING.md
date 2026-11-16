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

Create `supplements/items/<brand>-<name>/supplement.json` with its Red Sea
`uid` and `fullname` when the supplement exists in the ReefBeat library. Dosing data
(`effects`) must come from the manufacturer: give the `source` and set
`verified` to `true` only once it has been checked.

## Add a water test

Create `water_tests/items/<brand>-<name>/water_test.json`: one entry per
product (kit or checker), with one `tests[]` item per measured parameter. For a
titration copy the points `amount -> value` of the manufacturer manual into
`table` (amounts strictly increasing, values strictly monotone), give the
`source` and set `verified` to `true` only once checked. Two ranges of the same
parameter are two products.

## Visuals

Only visuals you are allowed to share under CC BY 4.0: your own photos or
drawings. No pictures copied from manufacturers' sites or shops.

## Before opening the pull request

```
python scripts/validate.py
pytest
```

The lists of `README.md` are refreshed automatically after the merge: a pull
request only adds the folder of its entry, so that two open pull requests never
conflict on the README.
