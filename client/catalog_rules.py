"""ReefTech catalog rules: what an entry must follow, beyond its JSON Schema.

Canonical copy: reeftech-catalog/client/. `scripts/validate.py` (CI, pre-commit,
releases) and the tools editing the catalog (reeftech_catalog_editor) share this
one implementation, so that a draft is accepted by the editor exactly when the
CI accepts it. Tools get a copy through `scripts/sync_client.py`; edit it here,
never in a copy.

No Home Assistant dependency; needs `jsonschema` and `referencing`. Everything
works on data already read (dicts, file names and sizes), never on the
repository layout:

    validators = load_validators(schema_dir)        # schema/ of the repository,
                                                    # or schema.zip of a release
    ctx = Context(tasks=..., categories=..., supplements=..., parameters=...)
    report = validate_draft("item", "eheim-200", data, {"item.json": 120}, ctx, validators)
    report.errors, report.warnings

`Context` holds the *other* entries a draft is checked against (task library,
categories, and the supplements / parameters it must be unique among): when a
draft replaces an entry, leave that entry out of the context.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from itertools import pairwise
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from referencing import Registry, Resource

# Languages of the ecosystem. English is mandatory, the others are reported
# as missing (warning) so translators know what to fill in.
KNOWN_LANGS = ("en", "fr", "de", "es", "it", "pl", "pt")

# Files allowed next to a descriptor, with their size limit in bytes.
ASSET_FILES = {
    "image.webp": 300_000,
    "image.png": 300_000,
    "thumb.webp": 40_000,
}
DESCRIPTOR_MAX = 32_000

# Plausible raise for 1 mL in 100 L, per (parameter, unit). A value outside
# these bounds is almost surely a typo (factor 10 or wrong unit) and could
# make an automatic dose proposal dangerous, so it is an error.
EFFECT_BOUNDS = {
    ("kh", "dkh"): (0.001, 1.0),
    ("ca", "ppm"): (0.01, 20.0),
    ("mg", "ppm"): (0.01, 30.0),
    ("k", "ppm"): (0.01, 20.0),
    ("sr", "ppm"): (0.001, 5.0),
    ("no3", "ppm"): (0.001, 10.0),
    ("po4", "ppm"): (0.0001, 1.0),
    ("i", "ppb"): (0.01, 100.0),
    ("i", "ppm"): (0.00001, 0.1),
    ("fe", "ppm"): (0.00001, 0.1),
}
# Plausible readings of a water test, per (parameter, unit). A table value
# outside these bounds is almost surely a typo (factor 10, wrong unit) and could
# make a dose proposal dangerous, so it is an error.
READING_BOUNDS = {
    ("kh", "dkh"): (0, 50),
    ("ca", "ppm"): (0, 2000),
    ("mg", "ppm"): (0, 4000),
    ("k", "ppm"): (0, 1500),
    ("sr", "ppm"): (0, 50),
    ("no3", "ppm"): (0, 500),
    ("po4", "ppm"): (0, 10),
    ("i", "ppb"): (0, 1000),
    ("fe", "ppm"): (0, 2),
}
# Brine concentration bounds (g/L); natural sea water is about 35 g/L and
# salt stops dissolving around 360 g/L.
BRINE_BOUNDS = (50, 350)

# Order the reference band must keep: min <= low <= optimal <= high <= max,
# skipping any bound that is absent.
RANGE_ORDER = ("min", "low", "optimal", "high", "max")

#: Descriptor file of each kind of entry (a task is a file of its own)
DESCRIPTORS = {
    "category": "category.json",
    "item": "item.json",
    "supplement": "supplement.json",
    "parameter": "parameter.json",
    "water_test": "water_test.json",
}
#: Folder of each kind, from the root of the repository
FOLDERS = {
    "task": "equipment/tasks",
    "category": "equipment/categories",
    "item": "equipment/items",
    "supplement": "supplements/items",
    "parameter": "parameters/items",
    "water_test": "water_tests/items",
}


@dataclass
class Report:
    """Errors (the entry is refused) and warnings (it is accepted, but could be better)."""

    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def error(self, where: str, message: str) -> None:
        self.errors.append(f"{where}: {message}")

    def warn(self, where: str, message: str) -> None:
        self.warnings.append(f"{where}: {message}")


@dataclass
class Context:
    """The entries a draft is checked against, by id."""

    tasks: dict[str, dict[str, Any]] = field(default_factory=dict)
    categories: dict[str, dict[str, Any]] = field(default_factory=dict)
    supplements: dict[str, dict[str, Any]] = field(default_factory=dict)
    parameters: dict[str, dict[str, Any]] = field(default_factory=dict)
    water_tests: dict[str, dict[str, Any]] = field(default_factory=dict)


def where_of(kind: str, entry_id: str) -> str:
    """Location of an entry in the repository, as shown in the reports."""
    if kind == "task":
        return f"{FOLDERS[kind]}/{entry_id}.json"
    return f"{FOLDERS[kind]}/{entry_id}"


def load_validators(schema_dir: Path) -> dict[str, Draft202012Validator]:
    """One validator per kind of entry, from a folder of `<kind>.schema.json`."""
    resources = []
    schemas = {}
    for path in schema_dir.glob("*.schema.json"):
        schema = json.loads(path.read_text(encoding="utf-8"))
        resources.append((schema["$id"], Resource.from_contents(schema)))
        schemas[path.name.split(".")[0]] = schema
    registry = Registry().with_resources(resources)
    return {
        name: Draft202012Validator(schema, registry=registry)
        for name, schema in schemas.items()
        if name != "common"
    }


# -- generic checks -------------------------------------------------------------


def check_schema(
    report: Report, where: str, data: Any, validator: Draft202012Validator
) -> bool:
    """Report what the JSON Schema refuses; True when it accepts the data."""
    errors = sorted(validator.iter_errors(data), key=lambda e: list(e.path))
    for err in errors:
        location = "/".join(str(p) for p in err.path) or "(root)"
        report.error(where, f"{location}: {err.message}")
    return not errors


def check_files(
    report: Report, where: str, files: Mapping[str, int], descriptor: str
) -> None:
    """The files of an entry (name -> size in bytes): descriptor, visuals, limits."""
    if descriptor not in files:
        report.error(where, f"missing {descriptor}")
    for name, size in files.items():
        if name == descriptor:
            limit = DESCRIPTOR_MAX
        elif name in ASSET_FILES:
            limit = ASSET_FILES[name]
        else:
            report.error(
                where,
                f"unexpected file {name!r} (allowed: {descriptor}, {', '.join(ASSET_FILES)})",
            )
            continue
        if size > limit:
            report.error(where, f"{name} is {size} bytes, limit {limit}")
    if "image.webp" in files and "image.png" in files:
        report.error(where, "image.webp and image.png both present, keep one")


def _check_i18n(report: Report, where: str, texts: dict[str, str] | None) -> None:
    if not texts:
        return
    missing = [lang for lang in KNOWN_LANGS if lang not in texts]
    if missing:
        report.warn(where, f"missing translations: {', '.join(missing)}")


def _check_schedule(
    report: Report, where: str, schedule: dict[str, Any] | None
) -> None:
    if not schedule:
        return
    if schedule["type"] == "interval":
        every = schedule["every"]
        if not every["min"] <= every["default"] <= every["max"]:
            report.error(where, f"schedule needs min <= default <= max, got {every}")
    elif schedule["day"] > (
        29
        if schedule["month"] == 2
        else 30
        if schedule["month"] in (4, 6, 9, 11)
        else 31
    ):
        report.error(where, f"no day {schedule['day']} in month {schedule['month']}")


def _check_task_refs(
    report: Report, where: str, refs: list[dict[str, Any]], tasks: Mapping[str, Any]
) -> None:
    for ref in refs:
        if ref["task"] not in tasks:
            report.error(where, f"unknown task {ref['task']!r}")
        _check_schedule(report, f"{where} [{ref['task']}]", ref.get("schedule"))
    ids = [ref["task"] for ref in refs]
    for task in {t for t in ids if ids.count(t) > 1}:
        report.error(where, f"task {task!r} listed twice")


def _has_image(files: Mapping[str, int]) -> bool:
    return any(name.startswith("image.") for name in files)


def _check_unique(
    report: Report,
    where: str,
    key: str,
    value: Any,
    others: Mapping[str, dict[str, Any]],
    verb: str = "used",
) -> None:
    for other_id, other in others.items():
        if other.get(key) == value:
            report.error(where, f"{key} {value!r} already {verb} by {other_id!r}")
            return


# -- one rule set per kind ------------------------------------------------------


def _check_task(report, where, entry_id, data, files, ctx) -> None:
    _check_i18n(report, where, data["name"])
    _check_i18n(report, where + " description", data.get("description"))
    _check_schedule(report, where, data["schedule"])


def _check_category(report, where, entry_id, data, files, ctx) -> None:
    _check_task_refs(report, where, data.get("maintenance", []), ctx.tasks)
    _check_i18n(report, where, data["name"])
    if not _has_image(files):
        report.warn(where, "no generic image, only the icon will be shown")


def _check_item(report, where, entry_id, data, files, ctx) -> None:
    _check_task_refs(report, where, data.get("maintenance", []), ctx.tasks)
    category = ctx.categories.get(data["category"])
    if category is None:
        report.error(where, f"unknown category {data['category']!r}")
        return
    extra = set(data.get("scopes", [])) - set(category["scopes"])
    if extra:
        report.error(
            where,
            f"scopes {sorted(extra)} not allowed by category {data['category']!r}",
        )
    _check_i18n(report, where, data.get("name"))
    if not _has_image(files):
        report.warn(where, "no image, the category image will be shown")


def _check_supplement(report, where, entry_id, data, files, ctx) -> None:
    if not _has_image(files):
        report.warn(where, "no bottle image, the generic bottle will be shown")

    for key in ("uid", "fullname"):
        value = data.get(key)
        if value is not None:
            _check_unique(report, where, key, value, ctx.supplements)
    if "bundle" in data and data["kind"] != "bundle":
        report.error(where, "only a supplement of kind 'bundle' has parts")
    if data["kind"] == "bundle" and "bundle" not in data:
        report.error(where, "a bundle needs its parts")

    brine = data["kind"] == "brine"
    if brine and "concentration_g_l" not in data:
        report.error(where, "a brine needs concentration_g_l")
    if not brine and "concentration_g_l" in data:
        report.error(where, "concentration_g_l is for brine only")
    if "salinity" in data.get("regulation", []) and not brine:
        report.error(where, "only a brine can drive salinity regulation")
    if brine:
        conc = data.get("concentration_g_l")
        if conc and not (
            BRINE_BOUNDS[0]
            <= conc["min"]
            <= conc["default"]
            <= conc["max"]
            <= BRINE_BOUNDS[1]
        ):
            report.error(
                where,
                f"concentration_g_l must satisfy {BRINE_BOUNDS[0]} <= min <= default <= max <= {BRINE_BOUNDS[1]}",
            )
        if data.get("effects"):
            report.error(
                where,
                "a brine has no effects: its salinity raise is computed from the concentration",
            )

    seen = set()
    for effect in data.get("effects", []):
        key = (effect["parameter"], effect["unit"])
        if effect["parameter"] in seen:
            report.error(where, f"parameter {effect['parameter']!r} listed twice")
        seen.add(effect["parameter"])
        bounds = EFFECT_BOUNDS.get(key)
        if bounds is None:
            report.error(
                where,
                f"unit {effect['unit']!r} not supported for {effect['parameter']!r}",
            )
        elif not bounds[0] <= effect["per_ml_per_100l"] <= bounds[1]:
            report.error(
                where,
                f"{effect['parameter']} raise {effect['per_ml_per_100l']} {effect['unit']} "
                f"per mL/100 L is implausible (expected {bounds[0]}..{bounds[1]})",
            )

    if data["verified"] and "source" not in data:
        report.error(where, "verified dosing data needs a source")
    if data.get("effects") and not data["verified"]:
        report.warn(
            where, "effects not verified: not used for automatic dose proposals"
        )


def _check_parameter(report, where, entry_id, data, files, ctx) -> None:
    _check_i18n(report, where, data["name"])
    _check_unique(report, where, "symbol", data["symbol"], ctx.parameters)

    effect = data.get("effect_parameter")
    if effect is not None:
        _check_unique(
            report,
            where,
            "effect_parameter",
            effect,
            ctx.parameters,
            verb="mapped",
        )

    band = data.get("range")
    if band:
        present = [(key, band[key]) for key in RANGE_ORDER if key in band]
        for (prev_key, prev), (key, value) in pairwise(present):
            if prev > value:
                report.error(
                    where,
                    f"range {prev_key}={prev} must be <= {key}={value}",
                )
        if "source" not in data:
            report.warn(where, "reference band without a source")


def _check_reading(report, where, key, values) -> None:
    """Every value of a test is a plausible reading of its parameter."""
    bounds = READING_BOUNDS.get(key)
    if bounds is None:
        return
    for value in values:
        if not bounds[0] <= value <= bounds[1]:
            report.error(
                where,
                f"{value} {key[1]} is implausible for {key[0]} "
                f"(expected {bounds[0]}..{bounds[1]})",
            )


def _check_table(report, where, table) -> list[float]:
    """Amounts strictly increasing, values strictly monotone; the values."""
    points = table["points"]
    amounts = [point["amount"] for point in points]
    values = [point["value"] for point in points]
    if any(prev >= amount for prev, amount in pairwise(amounts)):
        report.error(where, "table amounts must be strictly increasing")
    steps = [value - prev for prev, value in pairwise(values)]
    if not (all(step > 0 for step in steps) or all(step < 0 for step in steps)):
        report.error(where, "table values must be strictly increasing or decreasing")
    return values


def _check_test(report, where, test, parameters) -> None:
    key = (test["parameter"], test["unit"])
    if parameters:
        parameter = parameters.get(test["parameter"])
        if parameter is None:
            report.error(where, f"unknown parameter {test['parameter']!r}")
        elif parameter["unit"] != test["unit"]:
            report.error(
                where,
                f"unit {test['unit']!r} differs from the one of the parameter "
                f"({parameter['unit']!r})",
            )
    if test["method"] == "direct":
        if "table" in test:
            report.error(where, "a direct test has no table")
        band = test.get("range")
        if band:
            if band["min"] >= band["max"]:
                report.error(where, "range min must be below max")
            _check_reading(report, where, key, (band["min"], band["max"]))
        return
    if "range" in test:
        report.error(where, "a titration takes its range from its table")
    if "table" not in test:
        report.error(where, "a titration needs its table")
        return
    _check_reading(report, where, key, _check_table(report, where, test["table"]))


def _check_water_test(report, where, entry_id, data, files, ctx) -> None:
    brand, name = data["brand"].lower(), data["name"].lower()
    for other_id, other in ctx.water_tests.items():
        if (
            other.get("brand", "").lower() == brand
            and other.get("name", "").lower() == name
        ):
            report.error(
                where, f"{data['brand']} {data['name']} is already {other_id!r}"
            )
            break
    seen = set()
    for index, test in enumerate(data["tests"]):
        if test["parameter"] in seen:
            report.error(where, f"parameter {test['parameter']!r} listed twice")
        seen.add(test["parameter"])
        _check_test(report, f"{where} tests[{index}]", test, ctx.parameters)
    if data["verified"] and "source" not in data:
        report.error(where, "verified conversion data needs a source")
    if not data["verified"]:
        report.warn(where, "not verified: not used for automatic dose proposals")
    if not _has_image(files):
        report.warn(where, "no picture, the generic test will be shown")


_RULES = {
    "task": _check_task,
    "category": _check_category,
    "item": _check_item,
    "supplement": _check_supplement,
    "parameter": _check_parameter,
    "water_test": _check_water_test,
}


def check_bundle(
    report: Report,
    where: str,
    data: dict[str, Any],
    supplements: Mapping[str, dict[str, Any]],
) -> None:
    """The parts of a bundle: distinct slots, existing supplements, no nesting."""
    parts = data.get("bundle", [])
    slots = [part["slot"] for part in parts]
    if len(slots) != len(set(slots)):
        report.error(where, "bundle slot used twice")
    for part in parts:
        target = supplements.get(part["part"])
        if target is None:
            report.error(where, f"unknown bundle part {part['part']!r}")
        elif target["kind"] == "bundle":
            report.error(where, f"bundle part {part['part']!r} is itself a bundle")


def check_bundles(report: Report, supplements: Mapping[str, dict[str, Any]]) -> None:
    """`check_bundle` for every supplement of a catalog (once all are known)."""
    for entry_id, data in supplements.items():
        check_bundle(report, where_of("supplement", entry_id), data, supplements)


# -- entry points ---------------------------------------------------------------


def validate_entry(
    report: Report,
    kind: str,
    entry_id: str,
    data: dict[str, Any],
    files: Mapping[str, int],
    ctx: Context,
    validators: Mapping[str, Draft202012Validator],
) -> bool:
    """Schema, then the rules of the kind. True when the schema accepted the data.

    Bundle parts need the whole catalog: see `check_bundles` and `validate_draft`.
    """
    where = where_of(kind, entry_id)
    if not check_schema(report, where, data, validators[kind]):
        return False
    _RULES[kind](report, where, entry_id, data, files, ctx)
    return True


def validate_draft(
    kind: str,
    entry_id: str,
    data: dict[str, Any],
    files: Mapping[str, int],
    ctx: Context,
    validators: Mapping[str, Draft202012Validator],
) -> Report:
    """Everything the CI checks about one entry, for a tool that edits the catalog.

    `files` maps the file names of the entry, descriptor included, to their
    sizes in bytes (a task has no other file: pass `{}`).
    """
    report = Report()
    where = where_of(kind, entry_id)
    if kind != "task":
        check_files(report, where, files, DESCRIPTORS[kind])
    accepted = validate_entry(report, kind, entry_id, data, files, ctx, validators)
    if accepted and kind == "supplement":
        # The draft may be its own part: it counts among the supplements
        check_bundle(report, where, data, {**ctx.supplements, entry_id: data})
    return report
