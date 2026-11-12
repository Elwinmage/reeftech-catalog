"""Check the catalog before it is merged or released.

Errors make the command fail; warnings (missing translations, missing images,
unverified dosing data) are printed only, unless --strict is given.

    python scripts/validate.py [--root .] [--strict]
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from itertools import pairwise
from pathlib import Path

from jsonschema import Draft202012Validator
from referencing import Registry, Resource

from catalog import (
    ASSET_FILES,
    DESCRIPTOR_MAX,
    KNOWN_LANGS,
    SECTIONS,
    collect,
    entry_id,
    read_json,
)

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
# Brine concentration bounds (g/L); natural sea water is about 35 g/L and
# salt stops dissolving around 360 g/L.
BRINE_BOUNDS = (50, 350)

DESCRIPTORS = {
    "category": "category.json",
    "item": "item.json",
    "supplement": "supplement.json",
    "parameter": "parameter.json",
}

# Order the reference band must keep: min <= low <= optimal <= high <= max,
# skipping any bound that is absent.
RANGE_ORDER = ("min", "low", "optimal", "high", "max")


@dataclass
class Report:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def error(self, where: str, message: str) -> None:
        self.errors.append(f"{where}: {message}")

    def warn(self, where: str, message: str) -> None:
        self.warnings.append(f"{where}: {message}")


def _validators(root: Path) -> dict[str, Draft202012Validator]:
    schema_dir = root / "schema"
    resources = []
    schemas = {}
    for path in schema_dir.glob("*.schema.json"):
        schema = read_json(path)
        resources.append((schema["$id"], Resource.from_contents(schema)))
        schemas[path.name.split(".")[0]] = schema
    registry = Registry().with_resources(resources)
    return {
        name: Draft202012Validator(schema, registry=registry)
        for name, schema in schemas.items()
        if name != "common"
    }


def _check_i18n(report: Report, where: str, texts: dict | None) -> None:
    if not texts:
        return
    missing = [lang for lang in KNOWN_LANGS if lang not in texts]
    if missing:
        report.warn(where, f"missing translations: {', '.join(missing)}")


def _check_schedule(report: Report, where: str, schedule: dict | None) -> None:
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


def _check_task_refs(report: Report, where: str, refs: list, tasks: dict) -> None:
    for ref in refs:
        if ref["task"] not in tasks:
            report.error(where, f"unknown task {ref['task']!r}")
        _check_schedule(report, f"{where} [{ref['task']}]", ref.get("schedule"))
    ids = [ref["task"] for ref in refs]
    for task in {t for t in ids if ids.count(t) > 1}:
        report.error(where, f"task {task!r} listed twice")


def _check_files(report: Report, where: str, entry, descriptor: str) -> None:
    if descriptor not in entry.files:
        report.error(where, f"missing {descriptor}")
    for name, path in entry.files.items():
        size = path.stat().st_size
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
    if "image.webp" in entry.files and "image.png" in entry.files:
        report.error(where, "image.webp and image.png both present, keep one")


def _load(
    report: Report, where: str, path: Path, validator: Draft202012Validator
) -> dict | None:
    try:
        data = read_json(path)
    except (OSError, json.JSONDecodeError) as err:
        report.error(where, f"unreadable JSON ({err})")
        return None
    errors = sorted(validator.iter_errors(data), key=lambda e: list(e.path))
    for err in errors:
        location = "/".join(str(p) for p in err.path) or "(root)"
        report.error(where, f"{location}: {err.message}")
    return None if errors else data


def _check_equipment(root: Path, report: Report, validators) -> None:
    entries = collect(root, "equipment")
    tasks: dict[str, dict] = {}
    categories: dict[str, dict] = {}

    for entry in entries:
        if entry.kind != "tasks":
            continue
        for name, path in entry.files.items():
            where = f"equipment/tasks/{name}"
            data = _load(report, where, path, validators["task"])
            if data is None:
                continue
            tasks[name.removesuffix(".json")] = data
            _check_i18n(report, where, data["name"])
            _check_i18n(report, where + " description", data.get("description"))
            _check_schedule(report, where, data["schedule"])

    for kind, folder in (("category", "categories"), ("item", "items")):
        for entry in (e for e in entries if e.kind == kind):
            eid = entry_id(entry)
            where = f"equipment/{folder}/{eid}"
            descriptor = DESCRIPTORS[kind]
            _check_files(report, where, entry, descriptor)
            if descriptor not in entry.files:
                continue
            data = _load(report, where, entry.files[descriptor], validators[kind])
            if data is None:
                continue
            _check_task_refs(report, where, data.get("maintenance", []), tasks)
            if kind == "category":
                categories[eid] = data
                _check_i18n(report, where, data["name"])
                if not any(n.startswith("image.") for n in entry.files):
                    report.warn(where, "no generic image, only the icon will be shown")
                continue
            category = categories.get(data["category"])
            if category is None:
                report.error(where, f"unknown category {data['category']!r}")
                continue
            extra = set(data.get("scopes", [])) - set(category["scopes"])
            if extra:
                report.error(
                    where,
                    f"scopes {sorted(extra)} not allowed by category {data['category']!r}",
                )
            _check_i18n(report, where, data.get("name"))
            if not any(n.startswith("image.") for n in entry.files):
                report.warn(where, "no image, the category image will be shown")


def _check_supplements(root: Path, report: Report, validators) -> None:
    uids: dict[str, str] = {}
    fullnames: dict[str, str] = {}
    supplements: dict[str, dict] = {}
    for entry in collect(root, "supplements"):
        eid = entry_id(entry)
        where = f"supplements/items/{eid}"
        _check_files(report, where, entry, "supplement.json")
        if "supplement.json" not in entry.files:
            continue
        data = _load(
            report, where, entry.files["supplement.json"], validators["supplement"]
        )
        if data is None:
            continue
        supplements[eid] = data
        if not any(n.startswith("image.") for n in entry.files):
            report.warn(where, "no bottle image, the generic bottle will be shown")

        for key, seen_values in (("uid", uids), ("fullname", fullnames)):
            value = data.get(key)
            if value is None:
                continue
            if value in seen_values:
                report.error(
                    where, f"{key} {value!r} already used by {seen_values[value]!r}"
                )
            seen_values[value] = eid
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

    for eid, data in supplements.items():
        slots = [part["slot"] for part in data.get("bundle", [])]
        if len(slots) != len(set(slots)):
            report.error(f"supplements/items/{eid}", "bundle slot used twice")
        for part in data.get("bundle", []):
            target = supplements.get(part["part"])
            if target is None:
                report.error(
                    f"supplements/items/{eid}", f"unknown bundle part {part['part']!r}"
                )
            elif target["kind"] == "bundle":
                report.error(
                    f"supplements/items/{eid}",
                    f"bundle part {part['part']!r} is itself a bundle",
                )


def _check_parameters(root: Path, report: Report, validators) -> None:
    symbols: dict[str, str] = {}
    effect_params: dict[str, str] = {}
    for entry in collect(root, "parameters"):
        eid = entry_id(entry)
        where = f"parameters/items/{eid}"
        _check_files(report, where, entry, "parameter.json")
        if "parameter.json" not in entry.files:
            continue
        data = _load(
            report, where, entry.files["parameter.json"], validators["parameter"]
        )
        if data is None:
            continue
        _check_i18n(report, where, data["name"])

        symbol = data["symbol"]
        if symbol in symbols:
            report.error(
                where, f"symbol {symbol!r} already used by {symbols[symbol]!r}"
            )
        symbols[symbol] = eid

        effect = data.get("effect_parameter")
        if effect is not None:
            if effect in effect_params:
                report.error(
                    where,
                    f"effect_parameter {effect!r} already mapped by "
                    f"{effect_params[effect]!r}",
                )
            effect_params[effect] = eid

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


def validate(root: Path) -> Report:
    report = Report()
    validators = _validators(root)
    for section in SECTIONS:
        if not (root / section).is_dir():
            report.error(section, "section folder missing")
    _check_equipment(root, report, validators)
    _check_supplements(root, report, validators)
    _check_parameters(root, report, validators)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument(
        "--root", type=Path, default=Path(__file__).resolve().parent.parent
    )
    parser.add_argument(
        "--strict", action="store_true", help="treat warnings as errors"
    )
    args = parser.parse_args(argv)

    report = validate(args.root)
    for line in report.warnings:
        print(f"warning: {line}")
    for line in report.errors:
        print(f"error: {line}")
    failed = report.errors or (args.strict and report.warnings)
    print(f"{len(report.errors)} error(s), {len(report.warnings)} warning(s)")
    return 1 if failed else 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
